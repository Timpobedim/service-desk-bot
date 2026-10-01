"""Демо без сети и без модели: сценарий переписки прогоняется через эмулятор Telegram.

Видно главное: что бот понял сам, о чём спросил, какие сроки поставил и как напомнил.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from desk.bot import Bot
from desk.llm import Model
from desk.scheduler import tick
from desk.storage import Storage
from desk.telegram import TelegramApi
from desk.testing import FakeTelegram

MSK = timezone(timedelta(hours=3), "МСК")
START = datetime(2026, 9, 14, 10, 30, tzinfo=MSK)  # понедельник
CHAT = 4210
SCRIPT: tuple[tuple[str, str], ...] = (
    ("Пётр", "В 312 кабинете не печатает принтер, срочно"),
    ("Пётр", "/my"),
    ("Ольга", "Не могу зайти в 1С, пишет «неверный пароль», нужно к завтрашнему утру"),
    ("Ольга", "на складе"),
    ("Мастер", "/take 1"),
    ("Мастер", "/done 1"),
)


async def conversation(
    model: Model | None = None, database: Path | None = None, start: datetime = START
) -> list[tuple[str, str]]:
    """Возвращает переписку: (кто сказал, что сказал)."""
    fake = FakeTelegram()
    api = TelegramApi("1:демо", client=fake.client, base_url="http://telegram")
    storage = Storage(database or Path(":memory:"))
    moment = start
    bot = Bot(api, storage, model=model, clock=lambda: moment)
    talk: list[tuple[str, str]] = []
    try:
        for step, (user, text) in enumerate(SCRIPT):
            moment = start + timedelta(minutes=step * 3)
            update_id = fake.push(CHAT, text, user=user)
            updates = await api.updates(update_id, wait=0)
            talk.append((user, text))
            for update in updates:
                for answer in await bot.handle(update):
                    talk.append(("бот", answer))
        moment = start + timedelta(hours=5)  # прошло время — наступили сроки
        sent = await tick(api, storage, moment)
        talk.extend(("бот", text) for _, text in fake.sent[-sent:] if sent)
    finally:
        storage.close()
        await fake.client.aclose()
    return talk


def show(talk: Sequence[tuple[str, str]]) -> None:
    for who, text in talk:
        first, *rest = text.splitlines()
        print(f"{who:>6}: {first}")
        for line in rest:
            print(f"        {line}")
