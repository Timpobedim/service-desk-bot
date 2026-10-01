"""Что видно в сообщении без всякой модели: срок, срочность, категория, место, название.

Правила работают всегда и одинаково. Модель потом дополняет то, что правила не нашли, но найденный
правилами срок не переписывает: «завтра к 15:00» человек сказал точно, а ошибку в сроке заметят не
сразу. Без модели бот всё равно понимает основные запросы.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from desk.models import Category, Draft, Priority, shorten

# Ключи — готовые выражения: «до пятницы» и «в среду» пишут в разных падежах, а «ср» не должно
# находиться внутри «срочно».
WEEKDAYS = {
    r"\bпонедельник\w*": 0, r"\bвторник\w*": 1, r"\bсред[ауые]\b": 2, r"\bчетверг\w*": 3,
    r"\bпятниц[ауые]\b": 4, r"\bсуббот[ауые]\b": 5, r"\bвоскресень[еяю]\b": 6,
    r"\bпн\b": 0, r"\bвт\b": 1, r"\bср\b": 2, r"\bчт\b": 3, r"\bпт\b": 4, r"\bсб\b": 5, r"\bвс\b": 6,
}  # fmt: skip
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}  # fmt: skip
DAY_PARTS = {
    "утром": 9, "утра": 9, "утру": 9, "днем": 14, "дня": 14, "вечером": 18, "вечера": 18, "вечеру": 18, "ночью": 22,
}  # fmt: skip
CATEGORY_WORDS: dict[Category, tuple[str, ...]] = {
    Category.HARDWARE: ("принтер", "картридж", "мфу", "монитор", "ноутбук", "компьютер", "системный блок", "мышк",
                        "мышь", "клавиатур", "сканер", "проектор", "жужжит", "не включается"),
    Category.SOFTWARE: ("1с", "программ", "почт", "outlook", "excel", "эксель", "word", "ворд", "браузер", "сайт",
                        "обновлен", "лицензи", "битрикс", "crm", "приложение"),
    Category.ACCESS: ("пароль", "доступ", "учетк", "учётк", "логин", "права", "не пускает", "заблокирован",
                      "двухфакторн", "vpn-доступ"),
    Category.NETWORK: ("интернет", "сеть", "wi-fi", "wifi", "вай-фай", "вайфай", "vpn", "розетк rj", "кабель",
                       "не подключается к сети"),
    Category.FACILITY: ("свет", "лампа", "кондиционер", "мебель", "стул", "стол", "кран", "розетка", "дверь",
                        "жалюзи", "уборк"),
}  # fmt: skip
CRITICAL_WORDS = ("всё встало", "все встало", "не работает совсем", "критично", "авари", "стоит производство",
                  "никто не может работать")  # fmt: skip
HIGH_WORDS = ("срочно", "горит", "как можно быстрее", "сегодня же", "asap", "очень надо")
LOW_WORDS = ("не срочно", "когда будет время", "не горит", "по возможности", "когда-нибудь")
PLACE_PATTERNS = (
    re.compile(r"(?:каб(?:инет)?\.?\s*№?\s*)(\d{1,4}[а-я]?)", re.IGNORECASE),
    re.compile(r"(\d{1,4}[а-я]?)\s*(?:каб(?:инет)?\.?|кабинете)", re.IGNORECASE),
    re.compile(
        r"(?:на|в)\s+(склад\w*|ресепшен\w*|переговорк\w*|кухн\w*|бухгалтери\w*|серверн\w*|приемн\w*|приёмн\w*)",
        re.IGNORECASE,
    ),
    re.compile(r"(\d{1,2})\s*(?:-?й)?\s*этаж\w*", re.IGNORECASE),
)
PLACES = {
    "склад": "склад", "ресепшен": "ресепшен", "переговорк": "переговорка", "кухн": "кухня",
    "бухгалтери": "бухгалтерия", "серверн": "серверная", "приемн": "приёмная", "приёмн": "приёмная",
}  # fmt: skip
CONTACT = re.compile(
    r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b|(?:доб(?:авочный)?\.?\s*)(\d{2,5})\b",
    re.IGNORECASE,
)
RELATIVE = re.compile(r"через\s+(\d{1,3})\s*(минут\w*|час\w*|дн\w*|недел\w*)", re.IGNORECASE)
CLOCK = re.compile(r"(?:в|к|до)\s*(\d{1,2})[:.](\d{2})", re.IGNORECASE)
HOUR_ONLY = re.compile(r"(?:в|к|до)\s*(\d{1,2})\s*(утра|дня|вечера|ночи)?(?!\d)", re.IGNORECASE)
DATE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")", re.IGNORECASE)
GREETING = r"^(?:привет\w*|здравствуйте|добрый день|добрый вечер|доброе утро|коллеги|всем привет)[,!\s]*"
WORK_START, WORK_END = time(9, 0), time(18, 0)


def normalized(text: str) -> str:
    return " ".join(text.replace("ё", "е").lower().split())


def _clock(text: str) -> time | None:
    match = CLOCK.search(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        return time(hour, minute) if hour < 24 and minute < 60 else None
    match = HOUR_ONLY.search(text)
    if match:
        hour, part = int(match.group(1)), match.group(2)
        if hour > 24:
            return None
        if part in ("вечера", "ночи") and hour < 12:
            hour += 12
        return time(hour % 24, 0)
    for word, hour in DAY_PARTS.items():
        if re.search(rf"\b{word}\b", text):  # «дня» не должно находиться внутри «сегодня»
            return time(hour, 0)
    return None


def _with_time(day: datetime, clock: time | None, default: time = WORK_END) -> datetime:
    return day.replace(hour=(clock or default).hour, minute=(clock or default).minute, second=0, microsecond=0)


def find_deadline(text: str, now: datetime) -> datetime | None:
    """«через 2 часа», «завтра к 15:00», «до пятницы», «15 марта», «к 18». Ничего не нашли — None."""
    lowered = normalized(text)
    relative = RELATIVE.search(lowered)
    if relative:
        amount, unit = int(relative.group(1)), relative.group(2)
        if unit.startswith("минут"):
            return now + timedelta(minutes=amount)
        if unit.startswith("час"):
            return now + timedelta(hours=amount)
        if unit.startswith("дн"):
            return now + timedelta(days=amount)
        return now + timedelta(weeks=amount)
    clock = _clock(lowered)
    date = DATE.search(lowered)
    if date:
        day, month = int(date.group(1)), MONTHS[date.group(2)]
        year = now.year + (1 if (month, day) < (now.month, now.day) else 0)
        return _with_time(now.replace(year=year, month=month, day=day), clock)
    if "послезавтра" in lowered:
        return _with_time(now + timedelta(days=2), clock)
    if "завтра" in lowered:
        return _with_time(now + timedelta(days=1), clock)
    for pattern, weekday in WEEKDAYS.items():
        if re.search(pattern, lowered):
            ahead = (weekday - now.weekday()) % 7 or 7
            return _with_time(now + timedelta(days=ahead), clock)
    if "сегодня" in lowered or clock is not None:
        today = _with_time(now, clock)
        return today if today > now else _with_time(now + timedelta(days=1), clock)
    return None


def find_priority(text: str) -> Priority | None:
    lowered = normalized(text)
    if any(word in lowered for word in CRITICAL_WORDS):
        return Priority.CRITICAL
    if any(word in lowered for word in LOW_WORDS):  # «не срочно» смотрим раньше, чем «срочно»
        return Priority.LOW
    if any(word in lowered for word in HIGH_WORDS):
        return Priority.HIGH
    return None


def find_category(text: str) -> Category | None:
    lowered = normalized(text)
    hits = {
        category: sum(word in lowered for word in words)
        for category, words in CATEGORY_WORDS.items()
        if any(word in lowered for word in words)
    }
    return max(hits, key=lambda category: hits[category]) if hits else None


def find_place(text: str) -> str | None:
    for pattern in PLACE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        found = match.group(1)
        if found.isdigit():
            return f"{found}-й этаж" if "этаж" in match.group(0).lower() else f"кабинет {found}"
        word = normalized(found)
        return next((name for stem, name in PLACES.items() if word.startswith(stem)), word)
    return None


def find_contact(text: str) -> str | None:
    match = CONTACT.search(text)
    if match is None:
        return None
    return f"доб. {match.group(1)}" if match.group(1) else " ".join(match.group(0).split())


def make_title(text: str) -> str | None:
    """Первое предложение без приветствия. Приветствие снимаем до разбора, иначе им всё и закончится."""
    cleaned = re.sub(GREETING, "", text.strip(), flags=re.IGNORECASE).strip()
    first = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    return shorten(first) if len(first.split()) >= 2 else None


def extract(text: str, now: datetime) -> Draft:
    """Черновик заявки по правилам: заполнено только то, что действительно нашлось в тексте."""
    draft = Draft(text=text)
    for name, value in (
        ("title", make_title(text)),
        ("category", find_category(text)),
        ("priority", find_priority(text)),
        ("place", find_place(text)),
        ("deadline", find_deadline(text, now)),
        ("contact", find_contact(text)),
    ):
        if value is not None:
            draft = draft.with_value(name, value, "правила")
    return draft
