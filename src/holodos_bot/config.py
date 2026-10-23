import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    groq_key: str = field(repr=False)
    data_dir: Path
    allowed_user_ids: tuple[int, ...] = ()
    codex_binary: str = "/home/tony/.local/bin/codex"
    model: str = "gpt-5.6-luna"
    effort: str = "low"
    workers: int = 2
    llm_timeout: int = 240
    daily_actions_per_user: int = 30
    daily_actions_total: int = 300
    retention_hours: int = 24

    @classmethod
    def load(cls, path: Path, secrets: Path):
        raw = json.loads(path.read_text())
        secret = json.loads(secrets.read_text())
        token, key = secret.get("bot_token_holodos_recept", ""), secret.get("groq_voice", "")
        if not isinstance(token, str) or ":" not in token or any(c.isspace() for c in token):
            raise ValueError("bot_token_holodos_recept_missing")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("groq_voice_missing")
        directory = Path(raw.pop("data_dir", "data"))
        if not directory.is_absolute():
            directory = path.resolve().parent / directory
        raw["allowed_user_ids"] = tuple(raw.get("allowed_user_ids", []))
        obj = cls(token, key, directory.resolve(), **raw)
        if obj.model != "gpt-5.6-luna" or obj.effort != "low":
            raise ValueError("use_authorized_luna_low")
        if any(type(x) is not int or x <= 0 for x in obj.allowed_user_ids):
            raise ValueError("invalid_allowed_user_ids")
        if not 1 <= obj.workers <= 4 or not 30 <= obj.llm_timeout <= 600:
            raise ValueError("invalid_worker_limits")
        if min(obj.daily_actions_per_user, obj.daily_actions_total, obj.retention_hours) <= 0:
            raise ValueError("invalid_limits")
        return obj
