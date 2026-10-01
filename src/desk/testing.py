"""Эмулятор Telegram Bot API и модели-заглушки: всё проверяется без сети и без нейросети."""

from __future__ import annotations

import json
from typing import Any

import httpx

from desk.llm import Model, ModelError


class FakeTelegram:
    """Отвечает на getUpdates очередью сообщений, запоминает отправленное."""

    def __init__(self, username: str = "desk_bot") -> None:
        self.username = username
        self.queue: list[dict[str, Any]] = []
        self.sent: list[tuple[int, str]] = []
        self.calls: list[str] = []
        self.failures: list[int] = []  # коды, которые вернуть перед нормальным ответом
        self._next_update = 1
        self._next_message = 100
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle), base_url="http://telegram")

    def push(self, chat_id: int, text: str, user: str = "Пётр", update_id: int | None = None) -> int:
        number = update_id if update_id is not None else self._next_update
        self._next_update = max(self._next_update, number) + 1
        self.queue.append(
            {
                "update_id": number,
                "message": {
                    "message_id": number,
                    "chat": {"id": chat_id, "type": "group"},
                    "from": {"id": 500 + chat_id, "first_name": user},
                    "text": text,
                },
            }
        )
        return number

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(method)
        if self.failures:
            status = self.failures.pop(0)
            return httpx.Response(status, json={"ok": False, "description": "попробуйте позже"})
        payload = json.loads(request.content or b"{}")
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": self.username}})
        if method == "sendMessage":
            self._next_message += 1
            self.sent.append((int(payload["chat_id"]), str(payload["text"])))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self._next_message}})
        if method == "getUpdates":
            offset = int(payload.get("offset", 0))
            batch = [item for item in self.queue if item["update_id"] >= offset]
            self.queue = [item for item in self.queue if item["update_id"] < offset]
            return httpx.Response(200, json={"ok": True, "result": batch})
        return httpx.Response(404, json={"ok": False, "description": f"нет метода {method}"})


class ScriptedModel:
    """Модель, которая отдаёт заранее заданные ответы по очереди."""

    name = "сценарий"

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    async def complete(self, system: str, user: str) -> str:
        self.asked.append(user)
        if not self.answers:
            raise ModelError("сценарий закончился")
        return self.answers.pop(0)


class BrokenModel:
    """Модель, которая всегда отвечает не по делу — проверка запасного пути."""

    name = "сломанная"

    def __init__(self, answer: str = "Конечно! Вот заявка: она про принтер.") -> None:
        self.answer = answer
        self.asked: list[str] = []

    async def complete(self, system: str, user: str) -> str:
        self.asked.append(user)
        return self.answer


def json_answer(**fields: object) -> str:
    return json.dumps(fields, ensure_ascii=False)


__all__ = ["BrokenModel", "FakeTelegram", "Model", "ScriptedModel", "json_answer"]
