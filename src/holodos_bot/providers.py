import asyncio
import json
import logging
import os
import signal
import tempfile
from pathlib import Path, PurePosixPath

import httpx
import jsonschema
from PIL import Image, ImageOps

from .button_icons import decorate


class ProviderError(Exception):
    def __init__(self, code, retry_after=0):
        super().__init__(code)
        self.retry_after = retry_after


class Telegram:
    def __init__(self, token, client=None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.files = f"https://api.telegram.org/file/bot{token}/"
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(65, connect=10), trust_env=False)
        self.button_icons_enabled = True

    async def close(self):
        await self.client.aclose()

    async def call(self, method, **payload):
        markup = payload.get('reply_markup')
        if isinstance(markup, dict):
            payload['reply_markup'] = decorate(markup, self.button_icons_enabled)
        try:
            response = await self.client.post(self.base + method,
                                              json={k: v for k, v in payload.items() if v is not None})
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError("telegram_transport_unknown") from None
        # Retry only an explicit rejection of emoji, never an ambiguous transport failure.
        description = str(data.get('description', '')).lower() if isinstance(data, dict) else ''
        has_icons = any(b.get('icon_custom_emoji_id') for row in
                        (payload.get('reply_markup') or {}).get('inline_keyboard', []) for b in row)
        if (response.status_code == 400 and has_icons and not data.get('ok')
                and any(word in description for word in ('emoji', 'premium', 'button_icon'))):
            self.button_icons_enabled = False
            payload['reply_markup'] = decorate(markup, False)
            return await self.call(method, **payload)
        return self.result(data, response.status_code)

    @staticmethod
    def result(data, status):
        if isinstance(data, dict) and data.get("ok"):
            return data["result"]
        description = str(data.get("description", "")) if isinstance(data, dict) else ""
        if "message is not modified" in description:
            return True
        if "message to edit not found" in description or "message can't be edited" in description:
            raise ProviderError("telegram_edit_missing")
        retry = data.get("parameters", {}).get("retry_after", 0) if isinstance(data, dict) else 0
        raise ProviderError(f"telegram_{data.get('error_code', status) if isinstance(data, dict) else status}",
                            min(300, int(retry)))

    async def album(self, owner, pictures):
        files, media = {}, []
        for i, (path, caption) in enumerate(pictures):
            files[f"p{i}"] = (f"dish-{i}.jpg", path.read_bytes(), "image/jpeg")
            media.append({"type": "photo", "media": f"attach://p{i}", "caption": caption, "parse_mode": "HTML"})
        method = "sendMediaGroup" if len(pictures) > 1 else "sendPhoto"
        data = {"chat_id": str(owner)}
        if len(pictures) > 1:
            data["media"] = json.dumps(media, ensure_ascii=False)
        else:
            files = {"photo": files["p0"]}
            data["caption"], data["parse_mode"] = pictures[0][1], "HTML"
        try:
            r = await self.client.post(self.base + method, data=data, files=files, timeout=90)
            return self.result(r.json(), r.status_code)
        except (httpx.HTTPError, ValueError):
            raise ProviderError("telegram_transport_unknown") from None

    async def download(self, file_id, destination, max_bytes=20_000_000):
        info = await self.call("getFile", file_id=file_id)
        path = info.get("file_path", "")
        if (not path or PurePosixPath(path).is_absolute() or any(x in path for x in ["?", "#", "\\"])
                or any(x in {".", ".."} for x in path.split("/"))):
            raise ProviderError("telegram_invalid_file")
        if info.get("file_size", 0) > max_bytes:
            raise ProviderError("file_too_large")
        total = 0
        try:
            async with self.client.stream("GET", self.files + path) as r:
                if r.status_code != 200:
                    raise ProviderError("download_failed")
                with destination.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ProviderError("file_too_large")
                        f.write(chunk)
        except httpx.HTTPError:
            raise ProviderError("download_failed") from None
        if not total or (info.get("file_size") and total != info["file_size"]):
            raise ProviderError("download_incomplete")


def prepare_image(source, destination, size=1600):
    try:
        with Image.open(source) as image:
            if image.width * image.height > 30_000_000:
                raise ProviderError("image_too_large")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((size, size))
            image.save(destination, "JPEG", quality=88)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ProviderError("invalid_image") from None


class Groq:
    def __init__(self, key, client=None):
        self.key = key
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), trust_env=False)

    async def close(self):
        await self.client.aclose()

    async def transcribe(self, path):
        # Decode metadata first; Telegram duration fields are not trusted limits.
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-max_alloc", "10000000",
            "-probesize", "2000000", "-analyzeduration", "5000000",
            "-show_entries", "format=duration", "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(process.communicate(), 10)
            duration = float(json.loads(out)["format"]["duration"])
            if not 0 < duration <= 180:
                raise ProviderError("voice_too_long")
        except (TimeoutError, ValueError, KeyError):
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise ProviderError("invalid_audio") from None
        try:
            with path.open("rb") as f:
                r = await self.client.post("https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.key}"},
                    data={"model": "whisper-large-v3-turbo", "response_format": "verbose_json", "temperature": "0"},
                    files={"file": (path.name, f, "application/octet-stream")})
            if r.status_code != 200:
                raise ProviderError(f"groq_{r.status_code}", 30 if r.status_code == 429 else 0)
            data = r.json()
            text = data["text"]
            if not isinstance(text, str) or len(text) > 12000:
                raise ValueError
            segments = data.get("segments", [])
            if segments and all(x.get("no_speech_prob", 0) > .8 for x in segments):
                return ""
            return text.strip()
        except (httpx.HTTPError, ValueError, KeyError):
            raise ProviderError("groq_failed") from None


class Luna:
    def __init__(self, config):
        self.config = config
        self.semaphore = asyncio.Semaphore(config.workers)

    def command(self, work, search, images):
        args = [self.config.codex_binary, "exec", "--ignore-user-config", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "--model", self.config.model,
                "-C", str(work), "--json", "--output-schema", str(work / "schema.json"),
                "--output-last-message", str(work / "result.json")]
        overrides = {"model_instructions_file": str(work / "instructions.txt"),
                     "model_reasoning_effort": self.config.effort, "forced_login_method": "chatgpt",
                     "project_doc_max_bytes": 0, "web_search": "live" if search else "disabled",
                     "approval_policy": "never"}
        for feature in ("shell_tool", "apply_patch_freeform", "multi_agent", "multi_agent_v2", "apps",
                        "plugins", "hooks", "memories", "browser_use", "computer_use", "image_generation",
                        "code_mode", "code_mode_host", "skill_search", "view_image", "sleep_tool"):
            overrides[f"features.{feature}"] = False
        if search:
            overrides["features.code_mode"] = True
            overrides["features.code_mode_host"] = True
        for key, value in overrides.items():
            args.extend(["-c", key + "=" + json.dumps(value)])
        for path in images:
            args.extend(["--image", str(path.resolve())])
        return [*args, "-"]

    async def ask(self, instructions, schema, payload, *, images=(), search=False):
        async with self.semaphore:
            return await self._ask(instructions, schema, payload, images, search)

    async def _ask(self, instructions, schema, payload, images, search):
        allowed = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "XDG_CONFIG_HOME",
                   "XDG_DATA_HOME", "XDG_CACHE_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "TMPDIR"}
        env = {k: v for k, v in os.environ.items() if k in allowed or k.startswith("LC_")}
        with tempfile.TemporaryDirectory(prefix="holodos-luna-") as directory:
            work = Path(directory)
            (work / "instructions.txt").write_text(instructions)
            (work / "schema.json").write_text(json.dumps(schema))
            with (work / "events.jsonl").open("wb") as events, (work / "stderr.txt").open("wb") as errors:
                try:
                    process = await asyncio.create_subprocess_exec(*self.command(work, search, images), env=env,
                        stdin=asyncio.subprocess.PIPE, stdout=events, stderr=errors, start_new_session=True)
                except OSError:
                    raise ProviderError("luna_unavailable") from None
                try:
                    await asyncio.wait_for(process.communicate(json.dumps(payload, ensure_ascii=False).encode()),
                                           self.config.llm_timeout)
                except (TimeoutError, asyncio.CancelledError):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
                    if asyncio.current_task().cancelling():
                        raise
                    raise ProviderError("luna_timeout") from None
            result = work / "result.json"
            if process.returncode or not result.is_file() or result.stat().st_size > 150_000:
                raise ProviderError("luna_failed")
            searches, event_types = 0, set()
            if (work / "events.jsonl").stat().st_size > 5_000_000:
                raise ProviderError("luna_output_too_large")
            for line in (work / "events.jsonl").read_text().splitlines():
                try:
                    event = json.loads(line)
                    event_types.add((event.get("type", ""), event.get("item", {}).get("type", "")))
                    if event.get("type") == "item.completed" and event.get("item", {}).get("type") in {
                            "web_search", "web_search_call"}:
                        searches += 1
                except (ValueError, AttributeError):
                    continue
            if search and not searches:
                logging.warning("search_evidence_missing event_types=%s", sorted(event_types))
                raise ProviderError("search_not_performed")
            try:
                value = json.loads(result.read_text())
                jsonschema.validate(value, schema)
            except (ValueError, jsonschema.ValidationError):
                raise ProviderError("luna_invalid_response") from None
            return value
