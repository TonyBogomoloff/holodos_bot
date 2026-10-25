import argparse
import asyncio
import fcntl
import json
import logging
import os
import signal
from pathlib import Path

from .bot import Bot
from .config import Config
from .providers import Groq, Luna, ProviderError, Telegram
from .recipes import PublicWeb
from .store import Store

COMMANDS = [{"command": name, "description": text} for name, text in [
    ("start", "Главное меню"), ("plan", "План питания"), ("profile", "Профиль питания"),
    ("products", "Мои продукты"), ("new", "Новый список продуктов"),
    ("help", "Как пользоваться")]]


async def execute(args):
    config = Config.load(args.config, args.secrets)
    config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    telegram, groq, web = Telegram(config.token), Groq(config.groq_key), PublicWeb()
    # Authentication/menu checks must not run schema migrations before the deploy backup.
    store = Store(config.data_dir / "holodos.sqlite3") if args.command in {'run', 'status'} else None
    try:
        if args.command == "status":
            print(json.dumps({"queue": store.stats(), "database": store.db.execute("PRAGMA quick_check").fetchone()[0]}))
            return
        me = await telegram.call("getMe")
        webhook = await telegram.call("getWebhookInfo")
        if webhook.get("url"):
            raise ProviderError("existing_webhook_requires_review")
        process = await asyncio.create_subprocess_exec(config.codex_binary, "login", "status",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(process.communicate(), 15)
        if process.returncode or b"Logged in using ChatGPT" not in out + err:
            raise ProviderError("chatgpt_login_required")
        if args.command == "configure":
            await telegram.call("setMyCommands", commands=COMMANDS)
            await telegram.call("setMyDescription", description=
                "Подберу блюда из холодильника или меню на день и неделю по твоему бюджету и предпочтениям. "
                "Покажу недостающие покупки и примерные КБЖУ. Рецепты — с фото и ссылками на источники.")
            await telegram.call("setMyShortDescription", short_description=
                "Блюда из холодильника · Меню по бюджету · Покупки · Примерные КБЖУ")
            if await telegram.call("getMyCommands") != COMMANDS:
                raise ProviderError("telegram_menu_not_verified")
        if args.command in {"check", "configure"}:
            print(json.dumps({"ok": True, "username": me["username"], "model": config.model,
                              "effort": config.effort, "chatgpt_login": True,
                              "menu_verified": args.command == "configure"}))
            return
        with (config.data_dir / ".run.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProviderError("bot_already_running") from None
            bot = Bot(config, store, telegram, groq, Luna(config), web)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, bot.stop.set)
            logging.info("started username=%s model=%s effort=%s", me["username"], config.model, config.effort)
            await bot.run()
    finally:
        await telegram.close()
        await groq.close()
        await web.close()
        if store:
            store.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "check", "configure", "status"])
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--secrets", type=Path, default=Path("/home/tony/Desktop/_code/config.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(execute(args))
    except (ProviderError, ValueError, OSError) as e:
        # Arbitrary exception messages can contain file paths, payloads, or credential-bearing URLs.
        logging.error("startup_failed code=%s", str(e) if isinstance(e, ProviderError) else type(e).__name__)
        raise SystemExit(1) from None
