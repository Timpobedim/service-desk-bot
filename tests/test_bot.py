from __future__ import annotations

from datetime import datetime, timedelta

from conftest import CHAT, NOW

from desk.bot import Bot
from desk.models import Category, Priority, Status
from desk.storage import Storage
from desk.telegram import TelegramApi, Update
from desk.testing import ScriptedModel, json_answer


def message(text: str, number: int, user: str = "Пётр") -> Update:
    return Update(number, CHAT, user, text)


async def test_complete_message_becomes_a_ticket(bot: Bot, storage: Storage) -> None:
    answers = await bot.handle(message("В 312 кабинете не печатает принтер, срочно", 1))
    assert "Заявка №1 принята" in answers[0]
    ticket = storage.ticket(1)
    assert ticket is not None
    assert (ticket.category, ticket.priority, ticket.place) == (Category.HARDWARE, Priority.HIGH, "кабинет 312")
    assert ticket.due == NOW + timedelta(hours=4)
    assert storage.pending(1) == ["половина срока", "срок"]
    assert "срочность: срочно" in answers[0]


async def test_missing_place_is_asked_and_remembered(bot: Bot, storage: Storage) -> None:
    first = await bot.handle(message("Не открывается 1С, ошибка при входе", 1))
    assert "Где это находится" in first[0]
    assert storage.tickets(CHAT) == []
    second = await bot.handle(message("на складе", 2))
    assert "Заявка №1 принята" in second[0]
    ticket = storage.ticket(1)
    assert ticket is not None
    assert ticket.place == "склад"
    assert storage.dialog(CHAT) is None


async def test_repeated_update_does_not_double_the_ticket(bot: Bot, storage: Storage) -> None:
    await bot.handle(message("В 312 кабинете не печатает принтер", 5))
    assert await bot.handle(message("В 312 кабинете не печатает принтер", 5)) == []
    assert len(storage.tickets(CHAT)) == 1


async def test_commands(bot: Bot, storage: Storage) -> None:
    await bot.handle(message("В 312 кабинете не печатает принтер, срочно", 1))
    listed = await bot.handle(message("/my", 2))
    assert "№1" in listed[0]
    taken = await bot.handle(message("/take 1", 3, user="Мастер"))
    assert "в работе у Мастер" in taken[0]
    closed = await bot.handle(message("/done 1", 4, user="Мастер"))
    assert "закрыта" in closed[0]
    ticket = storage.ticket(1)
    assert ticket is not None
    assert ticket.status is Status.DONE
    assert storage.pending(1) == []
    assert "Открытых заявок нет" in (await bot.handle(message("/my", 5)))[0]
    assert "Укажите номер" in (await bot.handle(message("/take", 6)))[0]
    assert "Заявки №42 нет" in (await bot.handle(message("/done 42", 7)))[0]
    assert "Не знаю команду" in (await bot.handle(message("/dance", 8)))[0]


async def test_cancel_forgets_the_question(bot: Bot, storage: Storage) -> None:
    await bot.handle(message("Не открывается 1С", 1))
    assert storage.dialog(CHAT) is not None
    assert "отменил" in (await bot.handle(message("/cancel", 2)))[0]
    assert storage.dialog(CHAT) is None


async def test_model_fills_what_rules_could_not(api: TelegramApi, storage: Storage, clock: list[datetime]) -> None:
    model = ScriptedModel(json_answer(place="переговорка", category="сеть"))
    bot = Bot(api, storage, model=model, clock=lambda: clock[0])
    answers = await bot.handle(message("Всё висит, ничего не грузится", 1))
    assert "Заявка №1 принята" in answers[0]
    ticket = storage.ticket(1)
    assert ticket is not None
    assert (ticket.place, ticket.category) == ("переговорка", Category.NETWORK)
    assert "модель" in answers[0]


async def test_answers_go_to_the_chat_that_asked(bot: Bot, fake: object) -> None:
    await bot.handle(Update(1, 111, "Анна", "В 312 кабинете не печатает принтер"))
    await bot.handle(Update(2, 222, "Борис", "В 45 кабинете не работает телефон"))
    assert [chat for chat, _ in fake.sent] == [111, 222]  # type: ignore[attr-defined]
