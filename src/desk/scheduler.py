"""Напоминания о сроке — в том же цикле, что и опрос Telegram.

Не отдельный вечный поток с общим списком без блокировки: цикл один и умеет останавливаться.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from desk.storage import Storage
from desk.telegram import TelegramApi, TelegramError

TEXTS = {
    "половина срока": "⏳ Заявка №{id} ещё не закрыта: «{title}». Срок — {due:%d.%m в %H:%M}.",
    "срок": "🔔 Подошёл срок заявки №{id}: «{title}» ({place}). Срочность: {priority}.",
}


async def tick(api: TelegramApi, storage: Storage, now: datetime) -> int:
    """Отправляет наступившие напоминания. Возвращает сколько отправлено."""
    sent = 0
    for reminder in storage.due(now):
        ticket = reminder.ticket
        text = TEXTS.get(reminder.kind, "Напоминание по заявке №{id}: «{title}»").format(
            id=ticket.id, title=ticket.title, due=ticket.due, place=ticket.place, priority=ticket.priority.value
        )
        try:
            await api.send(ticket.chat_id, text)
        except TelegramError:
            continue  # не отмечаем отправленным — попробуем в следующий раз
        storage.mark_sent(reminder.id)
        sent += 1
    return sent


async def run(
    api: TelegramApi,
    storage: Storage,
    clock: Callable[[], datetime],
    *,
    period: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    stop: Callable[[], bool] = lambda: False,
) -> None:
    while not stop():
        await tick(api, storage, clock())
        await sleep(period)
