from __future__ import annotations

from conftest import NOW

from desk.extract import understand
from desk.llm import NullModel
from desk.models import Category, Priority
from desk.testing import BrokenModel, ScriptedModel, json_answer

MESSAGE = "Не открывается 1С, ошибка при входе"


async def test_without_model_everything_from_rules() -> None:
    draft, trouble = await understand("В 312 кабинете не печатает принтер, срочно", NOW, NullModel())
    assert trouble is None
    assert (draft.category, draft.priority, draft.place) == (Category.HARDWARE, Priority.HIGH, "кабинет 312")
    assert set(draft.source.values()) == {"правила"}


async def test_model_fills_only_the_gaps() -> None:
    model = ScriptedModel(json_answer(title="Другое название", place="склад", priority="срочно"))
    draft, trouble = await understand(MESSAGE, NOW, model)
    assert trouble is None
    assert draft.place == "склад"
    assert draft.source["place"] == "модель"
    assert draft.priority is Priority.HIGH
    assert draft.title == "Не открывается 1С, ошибка при входе"  # название нашли правила — модель его не переписывает
    assert draft.source["title"] == "правила"
    assert "Нужно определить" in model.asked[0]


async def test_model_deadline_is_checked() -> None:
    good = ScriptedModel(json_answer(place="склад", deadline="2026-09-15 09:00"))
    draft, _ = await understand(MESSAGE, NOW, good)
    assert draft.deadline == NOW.replace(day=15, hour=9, minute=0)

    far = ScriptedModel(json_answer(place="склад", deadline="2030-01-01 09:00"))
    draft, _ = await understand(MESSAGE, NOW, far)
    assert draft.deadline is None

    nonsense = ScriptedModel(json_answer(place="склад", deadline="когда-нибудь"))
    draft, _ = await understand(MESSAGE, NOW, nonsense)
    assert draft.deadline is None


async def test_model_does_not_override_named_deadline() -> None:
    model = ScriptedModel(json_answer(place="склад", deadline="2026-09-20 09:00"))
    draft, _ = await understand("Не открывается 1С, нужно завтра к 15:00", NOW, model)
    assert draft.deadline == NOW.replace(day=15, hour=15, minute=0)
    assert draft.source["deadline"] == "правила"


async def test_unknown_category_from_model_is_ignored() -> None:
    model = ScriptedModel(json_answer(category="космос", place="склад"))
    draft, _ = await understand("Что-то странное происходит", NOW, model)
    assert draft.category is None


async def test_broken_model_does_not_break_the_bot() -> None:
    draft, trouble = await understand("В 312 кабинете не печатает принтер", NOW, BrokenModel())
    assert trouble is not None
    assert "JSON" in trouble
    assert draft.category is Category.HARDWARE  # правила отработали
