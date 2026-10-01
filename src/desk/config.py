"""Настройки из окружения: токен бота, модель, часовой пояс, база.

Токен читается только из окружения и никуда не печатается; `.env` в репозиторий не попадает.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from desk.llm import Model, NullModel, OllamaModel, OpenAIModel

TOKEN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
KINDS = ("none", "ollama", "openai")
DEFAULT_ZONE = timezone(timedelta(hours=3), "МСК")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    token: str = ""
    database: Path = Path("заявки.sqlite")
    timezone_name: str = "Europe/Moscow"
    model_kind: str = "none"
    model_name: str = "qwen2.5:7b"
    model_url: str = "http://127.0.0.1:11434"
    model_key: str = ""
    poll_timeout: int = 25

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = env if env is not None else os.environ
        kind = source.get("DESK_MODEL", "none").strip().lower()
        if kind not in KINDS:
            raise SettingsError(f"DESK_MODEL={kind}: бывает {', '.join(KINDS)}")
        settings = cls(
            token=source.get("DESK_BOT_TOKEN", "").strip(),
            database=Path(source.get("DESK_DB", "заявки.sqlite")),
            timezone_name=source.get("DESK_TZ", "Europe/Moscow"),
            model_kind=kind,
            model_name=source.get("DESK_MODEL_NAME", "qwen2.5:7b"),
            model_url=source.get("DESK_MODEL_URL", "http://127.0.0.1:11434"),
            model_key=source.get("DESK_MODEL_KEY", ""),
            poll_timeout=int(source.get("DESK_POLL_TIMEOUT", "25")),
        )
        if kind == "openai" and not settings.model_key:
            raise SettingsError("Для DESK_MODEL=openai нужен ключ в DESK_MODEL_KEY")
        return settings

    def require_token(self) -> str:
        if not self.token:
            raise SettingsError("Не задан DESK_BOT_TOKEN — токен бота берётся только из окружения")
        if not TOKEN.match(self.token):
            raise SettingsError("DESK_BOT_TOKEN не похож на токен Telegram: ожидается «123456:буквы-и-цифры»")
        return self.token

    @property
    def zone(self) -> ZoneInfo | timezone:
        try:
            return ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            return DEFAULT_ZONE

    def now(self) -> datetime:
        return datetime.now(self.zone)

    def build_model(self) -> Model:
        if self.model_kind == "ollama":
            return OllamaModel(self.model_name, self.model_url)
        if self.model_kind == "openai":
            return OpenAIModel(self.model_name, self.model_url, self.model_key)
        return NullModel()
