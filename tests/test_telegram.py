from __future__ import annotations

import pytest
from conftest import CHAT

from desk.telegram import TelegramApi, TelegramError, Update, parse_update
from desk.testing import FakeTelegram


def test_update_parsing() -> None:
    raw = {
        "update_id": 7,
        "message": {"chat": {"id": 5}, "from": {"first_name": "Пётр"}, "text": " /take 12 "},
    }
    update = parse_update(raw)
    assert update == Update(7, 5, "Пётр", "/take 12")
    assert update.command == ("/take", "12")
    assert Update(1, 1, "Пётр", "просто текст").command == ("", "просто текст")
    assert parse_update({"update_id": 8, "message": {"chat": {"id": 5}, "photo": []}}) is None
    assert parse_update({"update_id": 9}) is None


async def test_send_and_receive(fake: FakeTelegram, api: TelegramApi) -> None:
    assert await api.me() == "desk_bot"
    number = fake.push(CHAT, "не печатает принтер")
    updates = await api.updates(0, wait=0)
    assert [update.text for update in updates] == ["не печатает принтер"]
    assert updates[0].id == number
    await api.send(CHAT, "принято")
    assert fake.sent == [(CHAT, "принято")]
    assert await api.updates(number + 1, wait=0) == []


async def test_retry_after_rate_limit(fake: FakeTelegram) -> None:
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    api = TelegramApi("1:тест", client=fake.client, base_url="http://telegram", sleep=sleep)
    fake.failures = [429, 503]
    await api.send(CHAT, "привет")
    assert len(pauses) == 2
    assert fake.sent == [(CHAT, "привет")]


async def test_refusal_is_an_error(fake: FakeTelegram, api: TelegramApi) -> None:
    with pytest.raises(TelegramError, match="нет метода"):
        await api.call("sendPhoto", chat_id=CHAT)
