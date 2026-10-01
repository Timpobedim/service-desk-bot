"""Правила плюс модель: модель дополняет то, что правила не нашли, и не переписывает найденное.

Так поведение остаётся предсказуемым. Срок, который человек назвал прямо («завтра к 15:00»), берётся
из правил; модель может предложить срок, только если правила его не нашли, и он проверяется на
разумность. Всё, что модель прислала сверх схемы, выбрасывается.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from desk import rules
from desk.llm import Model, ModelError, NullModel, ask_json
from desk.models import Category, Draft, Priority, shorten

KEYS = ("title", "category", "priority", "place", "deadline", "contact")
MAX_AHEAD = timedelta(days=60)
SYSTEM = (
    "Ты помощник службы поддержки в офисе. Из сообщения сотрудника выдели поля заявки. "
    "Отвечай ТОЛЬКО объектом JSON без пояснений. Поля: "
    "title — краткое описание проблемы одной строкой; "
    f"category — одно из: {', '.join(category.value for category in Category)}; "
    f"priority — одно из: {', '.join(priority.value for priority in Priority)}; "
    "place — где находится (кабинет, этаж, помещение); "
    "deadline — срок в формате ГГГГ-ММ-ДД ЧЧ:ММ, если он назван; "
    "contact — телефон или добавочный, если он указан. "
    "Поле, которого в сообщении нет, не придумывай — просто не включай его в ответ."
)


def user_prompt(text: str, now: datetime, missing: tuple[str, ...]) -> str:
    fields = ", ".join(missing) if missing else "все поля"
    return f"Сейчас {now:%Y-%m-%d %H:%M} ({now:%A}).\nСообщение сотрудника: «{text}»\nНужно определить: {fields}."


def _category(value: object) -> Category | None:
    text = rules.normalized(str(value))
    for category in Category:
        if text == rules.normalized(category.value):
            return category
    return rules.find_category(text)


def _priority(value: object) -> Priority | None:
    text = rules.normalized(str(value))
    for priority in Priority:
        if text == rules.normalized(priority.value):
            return priority
    return rules.find_priority(text)


def _deadline(value: object, now: datetime) -> datetime | None:
    text = str(value).strip().replace("/", "-")
    for shape in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d"):
        try:
            moment = datetime.strptime(text, shape).replace(tzinfo=now.tzinfo)
        except ValueError:
            continue
        return moment if now < moment <= now + MAX_AHEAD else None
    return None


def apply(draft: Draft, data: Mapping[str, Any], now: datetime) -> Draft:
    """Значения модели поверх черновика — только в пустые поля и только разумные."""
    parsers: dict[str, Callable[[Any], Any]] = {
        "title": lambda value: shorten(str(value)) if str(value).strip() else None,
        "category": _category,
        "priority": _priority,
        "place": lambda value: shorten(str(value), 60) if str(value).strip() else None,
        "deadline": lambda value: _deadline(value, now),
        "contact": lambda value: rules.find_contact(str(value)) or None,
    }
    for name, parse in parsers.items():
        if getattr(draft, name) is not None or name not in data:
            continue
        value = parse(data[name])
        if value is not None:
            draft = draft.with_value(name, value, "модель")
    return draft


async def understand(text: str, now: datetime, model: Model | None = None) -> tuple[Draft, str | None]:
    """Черновик заявки и, если модель подвела, причина — её видно в логе и в демо."""
    draft = rules.extract(text, now)
    if model is None or isinstance(model, NullModel):
        return draft, None
    gaps = tuple(name for name in KEYS if getattr(draft, name) is None)
    if not gaps:
        return draft, None
    try:
        answer = await ask_json(model, SYSTEM, user_prompt(text, now, gaps), KEYS)
    except ModelError as exc:
        return draft, str(exc)
    return apply(draft, answer.data, now), None
