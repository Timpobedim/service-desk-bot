from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from desk.bot import Bot
from desk.storage import Storage
from desk.telegram import TelegramApi
from desk.testing import FakeTelegram

MSK = timezone(timedelta(hours=3), "МСК")
NOW = datetime(2026, 9, 14, 10, 30, tzinfo=MSK)  # понедельник, рабочий день
CHAT = 777


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[Storage]:
    store = Storage(tmp_path / "заявки.sqlite")
    yield store
    store.close()


@pytest.fixture
async def fake() -> AsyncIterator[FakeTelegram]:
    telegram = FakeTelegram()
    yield telegram
    await telegram.client.aclose()


@pytest.fixture
def api(fake: FakeTelegram) -> TelegramApi:
    return TelegramApi("1:тест", client=fake.client, base_url="http://telegram")


@pytest.fixture
def clock() -> list[datetime]:
    """Изменяемое «сейчас»: тесты двигают время, подменяя clock[0]."""
    return [NOW]


@pytest.fixture
def bot(api: TelegramApi, storage: Storage, clock: list[datetime]) -> Bot:
    return Bot(api, storage, clock=lambda: clock[0])
