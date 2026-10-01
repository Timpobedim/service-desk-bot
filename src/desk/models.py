"""Заявка, черновик заявки и сроки реакции.

Черновик — это то, что удалось понять из сообщения: часть полей может быть пустой, и тогда бот
спрашивает недостающее, а не выдумывает. У каждого заполненного поля записано, откуда оно взялось —
из правил или из модели.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

TITLE_LIMIT = 70


class Category(StrEnum):
    HARDWARE = "техника"
    SOFTWARE = "программы"
    ACCESS = "доступы"
    NETWORK = "сеть"
    FACILITY = "хозяйственное"
    OTHER = "прочее"


class Priority(StrEnum):
    CRITICAL = "критично"
    HIGH = "срочно"
    NORMAL = "обычная"
    LOW = "не горит"


class Status(StrEnum):
    NEW = "новая"
    IN_WORK = "в работе"
    DONE = "закрыта"


REACTION: dict[Priority, timedelta] = {
    Priority.CRITICAL: timedelta(hours=1),
    Priority.HIGH: timedelta(hours=4),
    Priority.NORMAL: timedelta(hours=8),
    Priority.LOW: timedelta(days=3),
}
REQUIRED = ("title", "category", "place")
QUESTIONS = {
    "title": "Опишите одной строкой, что случилось.",
    "category": f"К чему относится заявка? {', '.join(category.value for category in Category)}.",
    "place": "Где это находится — кабинет, этаж или адрес?",
}


@dataclass(frozen=True)
class Draft:
    """Понятое из сообщения. Пустое поле — это «не знаю», а не «ничего страшного»."""

    text: str
    title: str | None = None
    category: Category | None = None
    priority: Priority | None = None
    place: str | None = None
    deadline: datetime | None = None  # срок, названный человеком
    contact: str | None = None
    source: dict[str, str] = field(default_factory=dict)  # поле → «правила» или «модель»

    @property
    def missing(self) -> list[str]:
        return [name for name in REQUIRED if getattr(self, name) is None]

    @property
    def complete(self) -> bool:
        return not self.missing

    def with_value(self, name: str, value: Any, source: str) -> Draft:
        values: dict[str, Any] = {name: value, "source": {**self.source, name: source}}
        return replace(self, **values)


@dataclass(frozen=True)
class Ticket:
    id: int
    chat_id: int
    author: str
    title: str
    category: Category
    priority: Priority
    place: str
    text: str
    created: datetime
    due: datetime
    status: Status = Status.NEW
    assignee: str | None = None
    closed: datetime | None = None

    @property
    def open(self) -> bool:
        return self.status is not Status.DONE


def due_at(created: datetime, priority: Priority, deadline: datetime | None = None) -> datetime:
    """Срок заявки: названный человеком, иначе по сроку реакции для её срочности."""
    by_priority = created + REACTION[priority]
    return min(deadline, by_priority) if deadline is not None else by_priority


def shorten(text: str, limit: int = TITLE_LIMIT) -> str:
    words = " ".join(text.split())
    return words if len(words) <= limit else words[: limit - 1].rstrip(" ,.;:") + "…"
