"""Разговор с сотрудником: сообщение → черновик → уточнения → заявка.

Ответ и напоминания всегда уходят в тот чат, откуда пришло сообщение, а не одному владельцу.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from desk import rules
from desk.extract import understand
from desk.llm import Model
from desk.models import QUESTIONS, Category, Draft, Priority, Status, Ticket, due_at, shorten
from desk.storage import Storage
from desk.telegram import TelegramApi, Update

HELP = (
    "Напишите, что случилось, обычными словами — я заведу заявку.\n"
    "Например: «В 312 кабинете не печатает принтер, срочно».\n\n"
    "Команды:\n"
    "/my — мои открытые заявки\n"
    "/take 12 — взять заявку в работу\n"
    "/done 12 — закрыть заявку\n"
    "/cancel — отменить незаконченный вопрос"
)
HALF = "половина срока"
DEADLINE = "срок"


def describe(ticket: Ticket, draft: Draft) -> str:
    found = ", ".join(f"{name} — {source}" for name, source in sorted(draft.source.items())) or "ничего"
    return (
        f"Заявка №{ticket.id} принята.\n"
        f"Что: {ticket.title}\n"
        f"Категория: {ticket.category.value}, срочность: {ticket.priority.value}\n"
        f"Где: {ticket.place}\n"
        f"Срок: {ticket.due:%d.%m в %H:%M}\n"
        f"Как понял: {found}"
    )


class Bot:
    def __init__(
        self,
        api: TelegramApi,
        storage: Storage,
        *,
        model: Model | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.api = api
        self.storage = storage
        self.model = model
        self._clock = clock or (lambda: datetime.now().astimezone())

    async def handle(self, update: Update) -> list[str]:
        """Обрабатывает одно обновление и возвращает отправленные ответы."""
        if self.storage.seen(update.id):
            return []  # Telegram повторил обновление после сбоя — заявку не дублируем
        command, rest = update.command
        if command in ("/start", "/help"):
            return await self._reply(update, HELP)
        if command == "/cancel":
            self.storage.drop_dialog(update.chat_id)
            return await self._reply(update, "Хорошо, отменил. Напишите, когда будет удобно.")
        if command == "/my":
            return await self._reply(update, self._my(update.chat_id))
        if command in ("/take", "/done"):
            return await self._reply(update, self._change(command, rest, update.user))
        if command:
            return await self._reply(update, f"Не знаю команду {command}. {HELP}")
        return await self._message(update)

    async def _message(self, update: Update) -> list[str]:
        now = self._clock()
        waiting = self.storage.dialog(update.chat_id)
        if waiting is not None:
            draft, asked = waiting
            draft = self._answer(draft, asked, update.text)
        else:
            draft, trouble = await understand(update.text, now, self.model)
            if trouble:
                return await self._reply(
                    update, f"Модель не ответила ({trouble}), разобрал сам.\n" + self._ask(update, draft, now)
                )
        return await self._reply(update, self._ask(update, draft, now))

    def _ask(self, update: Update, draft: Draft, now: datetime) -> str:
        """Либо вопрос про недостающее поле, либо готовая заявка."""
        if draft.missing:
            field = draft.missing[0]
            self.storage.save_dialog(update.chat_id, draft, field, now)
            return QUESTIONS[field]
        self.storage.drop_dialog(update.chat_id)
        return describe(self._create(update, draft, now), draft)

    def _answer(self, draft: Draft, asked: str, text: str) -> Draft:
        """Ответ на уточняющий вопрос: разбираем в том поле, о котором спрашивали."""
        if asked == "category":
            category = rules.find_category(text) or self._category_by_name(text)
            return draft.with_value("category", category, "ответ") if category else draft
        if asked == "place":
            return draft.with_value("place", rules.find_place(text) or shorten(text, 60), "ответ")
        return draft.with_value("title", shorten(text), "ответ")

    @staticmethod
    def _category_by_name(text: str) -> Category | None:
        wanted = rules.normalized(text)
        return next((category for category in Category if rules.normalized(category.value) == wanted), None)

    def _create(self, update: Update, draft: Draft, now: datetime) -> Ticket:
        priority = draft.priority or Priority.NORMAL
        due = due_at(now, priority, draft.deadline)
        ticket = self.storage.create(
            Ticket(
                id=0,
                chat_id=update.chat_id,
                author=update.user,
                title=draft.title or shorten(draft.text),
                category=draft.category or Category.OTHER,
                priority=priority,
                place=draft.place or "не указано",
                text=draft.text,
                created=now,
                due=due,
                status=Status.NEW,
            )
        )
        self.storage.plan(ticket.id, {HALF: now + (due - now) / 2, DEADLINE: due})
        return ticket

    def _my(self, chat_id: int) -> str:
        tickets = self.storage.tickets(chat_id)
        if not tickets:
            return "Открытых заявок нет."
        lines = [
            f"№{ticket.id} · {ticket.title} · {ticket.priority.value} · до {ticket.due:%d.%m %H:%M}"
            f"{' · ' + ticket.assignee if ticket.assignee else ''}"
            for ticket in tickets
        ]
        return "Открытые заявки:\n" + "\n".join(lines)

    def _change(self, command: str, rest: str, user: str) -> str:
        if not rest.strip().isdigit():
            return f"Укажите номер заявки: {command} 12"
        status = Status.IN_WORK if command == "/take" else Status.DONE
        ticket = self.storage.set_status(int(rest), status, assignee=user, when=self._clock())
        if ticket is None:
            return f"Заявки №{rest} нет."
        if status is Status.DONE:
            return f"Заявка №{ticket.id} закрыта. Напоминания по ней сняты."
        return f"Заявка №{ticket.id} в работе у {user}. Срок — {ticket.due:%d.%m %H:%M}."

    async def _reply(self, update: Update, text: str) -> list[str]:
        await self.api.send(update.chat_id, text)
        return [text]
