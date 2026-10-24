import asyncio
import html
import json
import logging
import re
import shutil
import time
from pathlib import Path

from .providers import ProviderError, prepare_image
from .recipes import INVENTORY_PROMPT, INVENTORY_SCHEMA, RecipeSearch

log = logging.getLogger(__name__)
WELCOME = "Привет! Покажи, что у тебя в холодильнике, — подберу, что приготовить 🍳\n\n" \
          "Пришли одно или несколько фото. Можно и просто перечислить продукты — текстом или голосом."
PRIVACY = "Фото и текст обрабатывает OpenAI, голос — Groq. Загруженные файлы удаляются после обработки, " \
          "список и правки — после суток бездействия. Резервные копии живут до суток. " \
          "/delete — удалить список и правки сейчас."


def normalize_update(update, allowed=()):
    callback = update.get("callback_query")
    message = callback.get("message", {}) if callback else update.get("message", {})
    sender = callback.get("from", {}) if callback else message.get("from", {})
    owner = sender.get("id")
    if (type(owner) is not int or owner <= 0 or sender.get("is_bot") or message.get("chat", {}).get("type") != "private"
            or message.get("chat", {}).get("id") != owner or (allowed and owner not in allowed)):
        return None, None
    if callback:
        data = callback.get("data", "")
        return owner, {"kind": "callback", "data": data[:64], "callback_id": callback.get("id", "")}
    if message.get("photo"):
        photo = message["photo"][-1]
        return owner, {"kind": "photo", "file_id": photo["file_id"], "unique": photo["file_unique_id"],
                       "caption": message.get("caption", "")[:2000], "group": message.get("media_group_id")}
    doc = message.get("document", {})
    if doc.get("mime_type") in {"image/jpeg", "image/png", "image/webp"}:
        return owner, {"kind": "photo", "file_id": doc["file_id"], "unique": doc["file_unique_id"],
                       "caption": message.get("caption", "")[:2000]}
    voice = message.get("voice") or message.get("audio")
    if voice:
        return owner, {"kind": "voice", "file_id": voice["file_id"], "duration": voice.get("duration", 0)}
    return owner, {"kind": "text", "text": message.get("text", "")[:6000]}


def keyboard(state):
    rev = state["rev"]
    rows = []
    def button(text, action):
        return {"text": text, "callback_data": f"h:{rev}:{action}"}
    if state["photos"]:
        rows.append([button("Готово, распознать", "recognize")])
    elif state["inventory"]:
        rows.append([button("🍳 Подобрать блюда", "search")])
    if state.get("previous") is not None:
        rows.append([button("Отменить правку", "undo")])
    if state["inventory"] or state["photos"]:
        rows.append([button("Добавить фото", "add"), button("Новый список", "new")])
    return {"inline_keyboard": rows}


def inventory_text(state, notice=""):
    parts = [notice] if notice else []
    if state["inventory"]:
        parts.append("Твои продукты:")
        for item in state["inventory"]:
            qty = f" — {item['quantity']}" if item["quantity"] else ""
            parts.append(f"{'❓' if item['uncertain'] else '•'} {item['name']}{qty}")
        if any(x["uncertain"] for x in state["inventory"]):
            parts.append("\nПункты с ❓ лучше уточнить — напиши или скажи голосом.")
        parts.append("\nЧто-то поправить? Напиши или отправь голосовое.")
    if state["photos"]:
        parts.append(f"\nФото: {len(state['photos'])}. Можешь добавить ещё или нажать «Готово, распознать».")
    if state.get("preferences"):
        parts.append("\nУчту: " + state["preferences"][:220])
    return "\n".join(parts) or WELCOME


def chunks(text, size=3800):
    while len(text) > size:
        at = text.rfind("\n", 0, size)
        if at < size // 2:
            at = text.rfind(" ", 0, size)
        if at <= 0:
            at = size
        yield text[:at]
        text = text[at:].lstrip()
    if text:
        yield text


class Bot:
    def __init__(self, config, store, telegram, groq, luna, web):
        self.config, self.store, self.telegram, self.groq, self.luna = config, store, telegram, groq, luna
        self.recipes = RecipeSearch(luna, web)
        self.stop = asyncio.Event()
        self.busy_notices = {}
        self.work = config.data_dir / "work"
        self.work.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def effect(self, job, suffix, operation):
        key = f"{job['id']}:{suffix}"
        old = self.store.effect(key)
        if old and old["status"] == "sent":
            return json.loads(old["result"])
        if old and old["status"] in {"unknown", "sending"}:
            raise ProviderError("delivery_unknown")
        self.store.effect_set(key, "sending")
        try:
            result = await operation()
        except ProviderError as e:
            self.store.effect_set(key, "unknown" if str(e) == "telegram_transport_unknown" else "retryable")
            raise
        self.store.effect_set(key, "sent", result)
        return result

    async def say(self, job, text, suffix="message", markup=None):
        result = None
        pieces = list(chunks(text))
        for i, piece in enumerate(pieces):
            result = await self.effect(job, f"{suffix}:{i}", lambda p=piece, n=i: self.telegram.call(
                "sendMessage", chat_id=job["owner"], text=p,
                reply_markup=markup if n == len(pieces) - 1 else None,
                link_preview_options={"is_disabled": True}))
        return result

    async def panel(self, job, state, text=None, markup=None):
        text, markup = text or inventory_text(state), markup if markup is not None else keyboard(state)
        if len(text) > 4000:
            # Product names/quantities are schema-bounded; preferences never displace product rows.
            raise ProviderError("inventory_too_long")
        if state.get("panel"):
            try:
                await self.telegram.call("editMessageText", chat_id=job["owner"], message_id=state["panel"],
                    text=text, reply_markup=markup, link_preview_options={"is_disabled": True})
                return
            except ProviderError as e:
                if str(e) != "telegram_edit_missing":
                    raise
        result = await self.say(job, text, "panel", markup)
        state["panel"] = result["message_id"]
        with self.store.db:
            self.store.save(job["owner"], state)

    async def cached(self, job, key, operation):
        if key not in job["cache"]:
            self.store.checkpoint(job, key, await operation())
        return job["cache"][key]

    def apply(self, job, state, view="inventory", notice=""):
        self.store.checkpoint(job, "applied", {"view": view, "notice": notice}, state=state)

    async def limit(self, job):
        if not self.store.reserve(job, self.config.daily_actions_per_user, self.config.daily_actions_total):
            await self.say(job, "На сегодня лимит обработки закончился. Возвращайся завтра 🙂", "quota")
            return False
        return True

    async def handle(self, job):
        state = self.store.session(job["owner"])
        directory = self.work / str(job["id"])
        directory.mkdir(exist_ok=True, mode=0o700)
        if not job["cache"].get("applied"):
            p = job["payload"]
            if p["kind"] == "callback":
                match = re.fullmatch(r"h:(\d+):(recognize|search|shop|undo|add|new|list|pick|choices)(?::(\d+))?",
                                     p["data"])
                if not match or int(match[1]) != state["rev"]:
                    await self.say(job, "Этот список уже изменился. Вот актуальный 👇", "stale")
                    await self.panel(job, state)
                    return
                action = match[2]
                if action == "pick":
                    index = int(match[3] or -1)
                    if not 0 <= index < len(state["recipes"]):
                        return
                    if not await self.limit(job):
                        return
                    recipe = state["recipes"][index]
                    translated = await self.cached(job, "translation", lambda: self.recipes.render(recipe))
                    self.store.checkpoint(job, "selected", {**recipe, **translated})
                    self.apply(job, state, "recipe")
                elif action == "choices":
                    self.apply(job, state, "choices")
                elif action in {"search", "shop"}:
                    if state["photos"]:
                        self.apply(job, state, notice="Сначала распознаем добавленные фото.")
                    else:
                        inventory = [x for x in state["inventory"] if not x["uncertain"]]
                        if not inventory:
                            self.apply(job, state, notice="Сначала добавь или уточни продукты.")
                        elif await self.limit(job):
                            await self.panel(job, state, "Ищу блюда по твоим продуктам… 🍳",
                                             {"inline_keyboard": []})
                            found = await self.cached(job, "recipes", lambda: self.recipes.search(
                                inventory, state["preferences"], directory, action == "shop"))
                            state["recipes"] = [{k: v for k, v in r.items() if k != "picture"} for r in found]
                            state["searched"], state["rev"] = True, state["rev"] + 1
                            self.apply(job, state, "results")
                        else:
                            return
                elif action == "recognize":
                    if state["photos"]:
                        if not await self.limit(job):
                            return
                        await self.panel(job, state, "Рассматриваю фотографии… 👀", {"inline_keyboard": []})
                        images = []
                        for i, photo in enumerate(state["photos"]):
                            source, target = directory / f"input-{i}", directory / f"photo-{i}.jpg"
                            await self.telegram.download(photo["file_id"], source)
                            await asyncio.to_thread(prepare_image, source, target)
                            images.append(target)
                        result = await self.cached(job, "inventory", lambda: self.luna.ask(
                            INVENTORY_PROMPT, INVENTORY_SCHEMA,
                            {"inventory": state["inventory"], "preferences": state["preferences"],
                             "captions": [x["caption"] for x in state["photos"]]}, images=images))
                        self.update_inventory(state, result)
                        state["photos"] = []
                        self.apply(job, state, notice=result["reply"])
                    else:
                        self.apply(job, state)
                elif action == "undo":
                    if state["previous"] is not None:
                        state["inventory"], state["preferences"] = state["previous"]
                        state["previous"], state["recipes"] = None, []
                        state["rev"] += 1
                    self.apply(job, state, notice="Вернул предыдущий список.")
                elif action == "new":
                    state = self.reset(state)
                    self.apply(job, state, notice="Начнём заново. Пришли фото или перечисли продукты 🙂")
                elif action == "add":
                    self.apply(job, state, notice="Пришли ещё фото сюда — можно несколько сразу.")
                else:
                    self.apply(job, state)
            elif p["kind"] == "photo":
                if len(state["photos"]) >= 10:
                    self.apply(job, state, notice="Уже есть 10 фото. Давай сначала разберём их.")
                else:
                    if not any(x["unique"] == p["unique"] for x in state["photos"]):
                        state["photos"].append(p)
                        state["rev"] += 1
                    self.apply(job, state)
            else:
                text = p.get("text", "").strip()
                command = text.split(maxsplit=1)[0].split("@")[0].lower() if text.startswith("/") else ""
                if command in {"/start", "/products", "/cancel"}:
                    if command == "/start":
                        state["panel"] = None
                    self.apply(job, state)
                elif command == "/new":
                    state = self.reset(state)
                    self.apply(job, state)
                elif command in {"/help", "/privacy"}:
                    await self.say(job, PRIVACY if command == "/privacy" else
                        "Пришли фото холодильника и нажми «Готово, распознать».\n"
                        "Поправить список можно текстом или голосом: «Убери молоко, добавь рис».\n"
                        "Потом нажми «Подобрать блюда». Можно добавить пожелание: «Без мяса, до 30 минут».\n\n"
                        "/new — новый список\n/products — мои продукты\n/privacy — о данных\n/delete — удалить данные")
                    return
                elif command == "/delete":
                    with self.store.db:
                        self.store.db.execute("DELETE FROM sessions WHERE owner=?", (job["owner"],))
                        ids = [r[0] for r in self.store.db.execute(
                            "SELECT id FROM jobs WHERE owner=? AND id!=?", (job["owner"], job["id"]))]
                        for identity in ids:
                            self.store.db.execute("DELETE FROM effects WHERE key LIKE ?", (f"{identity}:%",))
                        self.store.db.execute("DELETE FROM jobs WHERE owner=? AND id!=?", (job["owner"], job["id"]))
                    for identity in ids:
                        shutil.rmtree(self.work / str(identity), ignore_errors=True)
                    self.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    await self.say(job, "Удалил список и сохранённые правки. Можно начать заново 🙂")
                    return
                elif command:
                    await self.say(job, "Пришли фото или список продуктов. /help — короткая подсказка.")
                    return
                else:
                    if p["kind"] == "voice":
                        if p.get("duration", 0) > 180:
                            await self.say(job, "Пришли голосовое покороче — до трёх минут 🙂")
                            return
                        if not await self.limit(job):
                            return
                        await self.panel(job, state, "Слушаю твою правку… 🎙", {"inline_keyboard": []})
                        audio = directory / "voice.ogg"
                        if "transcript" not in job["cache"]:
                            await self.telegram.download(p["file_id"], audio, 8_000_000)
                        text = await self.cached(job, "transcript", lambda: self.groq.transcribe(audio))
                    if not text:
                        await self.say(job, "Не разобрал. Попробуй ещё раз или напиши текстом 🙂")
                        await self.panel(job, state)
                        return
                    if not await self.limit(job):
                        return
                    result = await self.cached(job, "inventory", lambda: self.luna.ask(
                        INVENTORY_PROMPT, INVENTORY_SCHEMA,
                        {"inventory": state["inventory"], "preferences": state["preferences"], "correction": text}))
                    self.update_inventory(state, result)
                    notice = result["reply"]
                    if p["kind"] == "voice":
                        notice = f"Услышал: «{text[:220]}»\n{notice}"
                    self.apply(job, state, notice=notice)
        applied = job["cache"]["applied"]
        # State and applied checkpoint are committed together; retries never apply a correction twice.
        state = self.store.session(job["owner"])
        if applied["view"] == "results":
            await self.show_results(job, state)
        elif applied["view"] == "choices":
            rows = [[{"text": f"{i + 1}. {r['title_ru']}"[:64],
                      "callback_data": f"h:{state['rev']}:pick:{i}"}] for i, r in enumerate(state["recipes"])]
            rows.append([{"text": "Мои продукты", "callback_data": f"h:{state['rev']}:list"}])
            await self.panel(job, state, "Какое блюдо посмотрим?", {"inline_keyboard": rows})
        elif applied["view"] == "recipe":
            await self.show_recipe(job, state)
        else:
            await self.panel(job, state, inventory_text(state, applied["notice"]))

    @staticmethod
    def update_inventory(state, result):
        state["previous"] = [state["inventory"], state["preferences"]]
        state["inventory"], state["preferences"] = result["inventory"], result["preferences"]
        state["recipes"], state["searched"] = [], False
        state["rev"] += 1

    @staticmethod
    def reset(state):
        return {"rev": state["rev"] + 1, "inventory": [], "previous": None, "photos": [], "recipes": [],
                "panel": state["panel"], "preferences": "", "searched": False}

    async def show_results(self, job, state):
        if "progress_panel" not in job["cache"]:
            self.store.checkpoint(job, "progress_panel", state.get("panel"))
        found = job["cache"].get("recipes", [])
        rev = state["rev"]
        rows = []
        if found:
            pictures = []
            for i, r in enumerate(found):
                caption = html.escape(f"{i + 1}. {r['title_ru']}\n" + (
                    "Докупить: " + ", ".join(r["missing"]) + "\n" if r["missing"] else ""))
                caption += f'<a href="{html.escape(r["url"], quote=True)}">Источник</a>'
                pictures.append((Path(r["picture"]), caption))
                rows.append([{"text": f"{i + 1}. {r['title_ru']}"[:64], "callback_data": f"h:{rev}:pick:{i}"}])
            await self.effect(job, "album", lambda: self.telegram.album(job["owner"], pictures))
            text = f"Нашёл {len(found)} блюд. Что приготовим? 🍽"
        else:
            text = "Пока не нашёл подходящих рецептов с фото. Добавь продукты или разреши небольшую докупку."
        if len(found) < 5:
            rows.append([{"text": "Можно докупить 1–2 продукта", "callback_data": f"h:{rev}:shop"}])
        rows.append([{"text": "Мои продукты", "callback_data": f"h:{rev}:list"}])
        # Put selection below the album; an old progress panel above it is no longer interactive.
        result = await self.say(job, text, "choices", {"inline_keyboard": rows})
        state["panel"] = result["message_id"]
        with self.store.db:
            self.store.save(job["owner"], state)
        old_panel = job["cache"]["progress_panel"]
        if old_panel and old_panel != state["panel"]:
            try:
                await self.telegram.call("deleteMessage", chat_id=job["owner"], message_id=old_panel)
            except ProviderError:
                pass

    async def show_recipe(self, job, state):
        r = job["cache"]["selected"]
        parts = [r.get("title_ru", r["title"]), "", "Ингредиенты:",
                 *["• " + x for x in r["ingredients"]], "", "Как приготовить:",
                 "\n\n".join(f"{i + 1}. {x.strip()}" for i, x in enumerate(r["steps"]))]
        if r.get('translation_status') == 'source':
            parts.insert(2, 'Оригинальный рецепт:\n')
        if r.get("time"):
            duration = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", r["time"])
            if duration:
                formatted = " ".join(f"{x} {unit}" for x, unit in zip(duration.groups(), ("ч", "мин", "сек"))
                                     if x and int(x))
                if formatted:
                    parts.extend(["", "Время: " + formatted])
        if r.get("yield"):
            parts.append("Порции: " + r["yield"])
        if r.get("missing"):
            parts.append("Докупить: " + ", ".join(r["missing"]))
        parts.extend(["", "Сверь количество продуктов перед готовкой.", "Источник: " + r["url"]])
        markup = {"inline_keyboard": [[{"text": "Другие блюда", "callback_data": f"h:{state['rev']}:choices"},
                                        {"text": "Мои продукты", "callback_data": f"h:{state['rev']}:list"}]]}
        await self.say(job, "\n".join(parts), "recipe", markup)

    async def poll(self):
        while not self.stop.is_set():
            try:
                updates = await self.telegram.call("getUpdates", offset=self.store.offset(), timeout=25,
                                                   allowed_updates=["message", "callback_query"])
                for update in updates:
                    owner, payload = normalize_update(update, self.config.allowed_user_ids)
                    accepted = self.store.ingest(update["update_id"], owner, payload)
                    if not accepted and time.time() - self.busy_notices.get(owner, 0) > 60:
                        if len(self.busy_notices) > 1000:
                            self.busy_notices = {k: v for k, v in self.busy_notices.items() if time.time() - v < 60}
                        self.busy_notices[owner] = time.time()
                        await self.telegram.call("sendMessage", chat_id=owner,
                            text="Сначала разберу предыдущие сообщения. Это пришли чуть позже 🙂")
                    if payload and payload["kind"] == "callback":
                        try:
                            await self.telegram.call("answerCallbackQuery", callback_query_id=payload["callback_id"])
                        except ProviderError:
                            pass
            except ProviderError as e:
                log.warning("poll %s", str(e))
                await asyncio.sleep(max(3, e.retry_after))

    async def worker(self):
        while not self.stop.is_set():
            job = self.store.claim()
            if not job:
                await asyncio.sleep(.3)
                continue
            error, retry = None, 0
            try:
                await self.handle(job)
            except asyncio.CancelledError:
                retry = 1
                raise
            except Exception as e:
                error = str(e) if isinstance(e, ProviderError) else type(e).__name__
                log.error("job=%s failed=%s", job["id"], error)
                if isinstance(e, ProviderError) and e.retry_after and job["attempts"] < 3:
                    retry = e.retry_after
                else:
                    try:
                        await self.say(job, "Не получилось закончить. Твой список сохранён — попробуй ещё раз 🙂",
                                       "failure")
                        await self.panel(job, self.store.session(job["owner"]))
                    except ProviderError:
                        pass
            finally:
                self.store.finish(job, error, retry)
                if not retry:
                    shutil.rmtree(self.work / str(job["id"]), ignore_errors=True)

    async def maintenance(self):
        last = 0
        while not self.stop.is_set():
            if time.time() - last > 600:
                self.store.cleanup(self.config.retention_hours)
                for backup in (self.config.data_dir.parent / '.backups').glob('holodos-*.sqlite3'):
                    if backup.is_file() and backup.stat().st_mtime < time.time() - 86400:
                        backup.unlink()
                active = {str(r[0]) for r in self.store.db.execute(
                    "SELECT id FROM jobs WHERE status IN ('pending','running')")}
                for path in self.work.iterdir():
                    if path.is_dir() and path.name.isdigit() and path.name not in active:
                        shutil.rmtree(path, ignore_errors=True)
                last = time.time()
            await asyncio.sleep(1)

    async def run(self):
        self.store.recover()
        async with asyncio.TaskGroup() as group:
            group.create_task(self.poll())
            group.create_task(self.maintenance())
            for _ in range(self.config.workers):
                group.create_task(self.worker())
