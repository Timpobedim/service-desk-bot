from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from conftest import CHAT, NOW

from desk.bot import Bot
from desk.cli import main
from desk.demo import conversation
from desk.llm import NullModel
from desk.scheduler import tick
from desk.storage import Storage
from desk.telegram import TelegramApi, Update
from desk.testing import FakeTelegram


async def test_reminders_are_sent_once(bot: Bot, storage: Storage, api: TelegramApi, fake: FakeTelegram) -> None:
    await bot.handle(Update(1, CHAT, "Пётр", "В 312 кабинете не печатает принтер, срочно"))
    fake.sent.clear()
    assert await tick(api, storage, NOW) == 0
    assert await tick(api, storage, NOW + timedelta(hours=2)) == 1
    assert "ещё не закрыта" in fake.texts[0]
    assert await tick(api, storage, NOW + timedelta(hours=2)) == 0
    assert await tick(api, storage, NOW + timedelta(hours=4)) == 1
    assert "Подошёл срок" in fake.texts[1]


async def test_closed_ticket_stops_reminding(bot: Bot, storage: Storage, api: TelegramApi, fake: FakeTelegram) -> None:
    await bot.handle(Update(1, CHAT, "Пётр", "В 312 кабинете не печатает принтер, срочно"))
    await bot.handle(Update(2, CHAT, "Мастер", "/done 1"))
    fake.sent.clear()
    assert await tick(api, storage, NOW + timedelta(hours=5)) == 0
    assert fake.sent == []


async def test_demo_conversation_tells_the_story() -> None:
    talk = await conversation(NullModel())
    text = "\n".join(f"{who}: {what}" for who, what in talk)
    assert "Заявка №1 принята" in text
    assert "Где это находится" in text  # у второго сообщения не было места
    assert "Заявка №2 принята" in text
    assert "в работе у Мастер" in text
    assert "закрыта" in text
    assert "Подошёл срок" in text or "ещё не закрыта" in text


def test_demo_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo"]) == 0
    printed = capsys.readouterr().out
    assert "бот:" in printed
    assert "Заявка №1 принята" in printed


def test_tickets_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    database = tmp_path / "заявки.sqlite"
    assert main(["tickets", "--db", str(database)]) == 0
    assert "Заявок нет" in capsys.readouterr().out


def test_run_without_token_explains(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("DESK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DESK_MODEL", "none")
    assert main(["run"]) == 2
    assert "DESK_BOT_TOKEN" in capsys.readouterr().err


def test_bad_model_setting_explains(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DESK_MODEL", "магия")
    assert main(["demo"]) == 2
    assert "DESK_MODEL" in capsys.readouterr().err
