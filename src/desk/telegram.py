"""Клиент Telegram Bot API: длинный опрос, таймауты, ограниченные повторы.

Токен подставляется в адрес, но ни в сообщения об ошибках, ни в логи не попадает: в них видно только
название метода. Смещение (offset) хранится в базе, поэтому после перезапуска бот не обрабатывает
заново уже полученные сообщения.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRIES = 3
BACKOFF = 1.0


class TelegramError(RuntimeError):
    pass


@dataclass(frozen=True)
class Update:
    id: int
    chat_id: int
    user: str
    text: str

    @property
    def command(self) -> tuple[str, str]:
        """«/take 12» → («/take», «12»); обычный текст → («», текст)."""
        if not self.text.startswith("/"):
            return "", self.text
        head, _, rest = self.text.partition(" ")
        return head.split("@")[0].lower(), rest.strip()


def parse_update(raw: dict[str, Any]) -> Update | None:
    message = raw.get("message") or raw.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text = message.get("text")
    if not isinstance(text, str) or not text.strip() or "id" not in chat:
        return None
    name = str(sender.get("first_name") or sender.get("username") or "сотрудник")
    return Update(int(raw["update_id"]), int(chat["id"]), name, text.strip())


class TelegramApi:
    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.telegram.org",
        timeout: float = 30.0,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._token = token
        self._base = f"{base_url.rstrip('/')}/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0))
        self._own = client is None
        self._sleep = sleep

    async def close(self) -> None:
        if self._own:
            await self._client.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        for attempt in range(1, RETRIES + 2):
            try:
                response = await self._client.post(f"{self._base}/{method}", json=params)
            except httpx.HTTPError as exc:
                if attempt > RETRIES:
                    raise TelegramError(f"{method}: нет связи с Telegram — {exc}") from exc
                await self._sleep(BACKOFF * 2 ** (attempt - 1) + random.random() / 4)  # noqa: S311 — джиттер, не криптография
                continue
            if response.status_code in RETRY_STATUSES and attempt <= RETRIES:
                pause = float((response.json().get("parameters") or {}).get("retry_after", BACKOFF * attempt))
                await self._sleep(pause)
                continue
            data = response.json()
            if not data.get("ok"):
                raise TelegramError(f"{method}: Telegram отказал — {data.get('description', response.status_code)}")
            return data.get("result")
        raise TelegramError(f"{method}: не удалось за {RETRIES + 1} попыток")

    async def me(self) -> str:
        result = await self.call("getMe")
        return str(result.get("username", "бот"))

    async def send(self, chat_id: int, text: str) -> int:
        result = await self.call("sendMessage", chat_id=chat_id, text=text)
        return int(result.get("message_id", 0))

    async def updates(self, offset: int, wait: int = 25) -> list[Update]:
        """`wait` — сколько секунд Telegram держит запрос, пока нет новых сообщений (длинный опрос)."""
        raw = await self.call("getUpdates", offset=offset, timeout=wait, allowed_updates=["message"])
        found = [parse_update(item) for item in raw or []]
        return [update for update in found if update is not None]
