from __future__ import annotations

from datetime import datetime

import pytest
from conftest import NOW

from desk import rules
from desk.models import Category, Priority


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("почините через 2 часа", NOW.replace(hour=12, minute=30)),
        ("нужно через 30 минут", NOW.replace(hour=11, minute=0)),
        ("завтра к 15:00", NOW.replace(day=15, hour=15, minute=0)),
        ("сегодня вечером", NOW.replace(hour=18, minute=0)),
        ("в 9 утра", NOW.replace(day=15, hour=9, minute=0)),  # 9:00 уже прошло — значит завтра
        ("к 17:30", NOW.replace(hour=17, minute=30)),
        ("до пятницы", NOW.replace(day=18, hour=18, minute=0)),
        ("15 октября в 10:00", NOW.replace(month=10, day=15, hour=10, minute=0)),
        ("послезавтра", NOW.replace(day=16, hour=18, minute=0)),
        ("не печатает принтер", None),
    ],
)
def test_deadlines(text: str, expected: datetime | None) -> None:
    assert rules.find_deadline(text, NOW) == expected


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("не печатает принтер, замялась бумага", Category.HARDWARE),
        ("не открывается 1С", Category.SOFTWARE),
        ("забыл пароль от учётки", Category.ACCESS),
        ("пропал вайфай в переговорке", Category.NETWORK),
        ("сломался кондиционер", Category.FACILITY),
        ("что-то не так", None),
    ],
)
def test_categories(text: str, category: Category | None) -> None:
    assert rules.find_category(text) == category


@pytest.mark.parametrize(
    ("text", "priority"),
    [
        ("срочно почините", Priority.HIGH),
        ("всё встало, никто не может работать", Priority.CRITICAL),
        ("не срочно, когда будет время", Priority.LOW),
        ("нужно посмотреть принтер", None),
    ],
)
def test_priority(text: str, priority: Priority | None) -> None:
    assert rules.find_priority(text) == priority


@pytest.mark.parametrize(
    ("text", "place"),
    [
        ("в 312 кабинете не печатает", "кабинет 312"),
        ("каб. 45 — нет света", "кабинет 45"),
        ("на складе не ловит вайфай", "склад"),
        ("в переговорке душно", "переговорка"),
        ("на 3 этаже течёт кран", "3-й этаж"),
        ("где-то у нас", None),
    ],
)
def test_place(text: str, place: str | None) -> None:
    assert rules.find_place(text) == place


@pytest.mark.parametrize(
    ("text", "contact"),
    [
        ("звоните на +7 999 123-45-67", "+7 999 123-45-67"),
        ("мой добавочный 4512", "доб. 4512"),
        ("без телефона", None),
    ],
)
def test_contact(text: str, contact: str | None) -> None:
    assert rules.find_contact(text) == contact


def test_title_skips_greeting() -> None:
    assert (
        rules.make_title("Привет! В 312 кабинете не печатает принтер. Помогите")
        == "В 312 кабинете не печатает принтер."
    )
    assert rules.make_title("сломалось") is None


def test_draft_from_one_message() -> None:
    draft = rules.extract("В 312 кабинете не печатает принтер, срочно, нужно к 17:30", NOW)
    assert draft.category is Category.HARDWARE
    assert draft.priority is Priority.HIGH
    assert draft.place == "кабинет 312"
    assert draft.deadline == NOW.replace(hour=17, minute=30)
    assert draft.complete
    assert draft.source == dict.fromkeys(("title", "category", "priority", "place", "deadline"), "правила")


def test_draft_knows_what_is_missing() -> None:
    draft = rules.extract("Не открывается 1С, ошибка при входе", NOW)
    assert draft.missing == ["place"]
    assert not draft.complete
