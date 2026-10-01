from __future__ import annotations

from datetime import timedelta

from conftest import CHAT, NOW

from desk.models import Category, Draft, Priority, Status, Ticket
from desk.storage import Storage


def make(storage: Storage, title: str = "Принтер", chat: int = CHAT) -> Ticket:
    return storage.create(
        Ticket(
            id=0,
            chat_id=chat,
            author="Пётр",
            title=title,
            category=Category.HARDWARE,
            priority=Priority.HIGH,
            place="кабинет 312",
            text="не печатает",
            created=NOW,
            due=NOW + timedelta(hours=4),
        )
    )


def test_tickets_are_saved_and_read(storage: Storage) -> None:
    ticket = make(storage)
    assert ticket.id == 1
    assert storage.ticket(1) == ticket
    assert storage.tickets(CHAT) == [ticket]
    assert storage.tickets(999) == []


def test_status_changes_and_reminders_are_dropped(storage: Storage) -> None:
    ticket = make(storage)
    storage.plan(ticket.id, {"половина срока": NOW + timedelta(hours=2), "срок": ticket.due})
    assert storage.pending(ticket.id) == ["половина срока", "срок"]
    in_work = storage.set_status(ticket.id, Status.IN_WORK, assignee="Мастер")
    assert in_work is not None
    assert (in_work.status, in_work.assignee) == (Status.IN_WORK, "Мастер")
    done = storage.set_status(ticket.id, Status.DONE, when=NOW + timedelta(hours=1))
    assert done is not None
    assert done.closed == NOW + timedelta(hours=1)
    assert storage.pending(ticket.id) == []
    assert storage.tickets(CHAT) == []
    assert len(storage.tickets(CHAT, only_open=False)) == 1
    assert storage.set_status(99, Status.DONE) is None


def test_reminders_come_due_once(storage: Storage) -> None:
    ticket = make(storage)
    storage.plan(ticket.id, {"половина срока": NOW + timedelta(hours=2), "срок": ticket.due})
    assert storage.due(NOW) == []
    ready = storage.due(NOW + timedelta(hours=2))
    assert [reminder.kind for reminder in ready] == ["половина срока"]
    storage.mark_sent(ready[0].id)
    assert storage.due(NOW + timedelta(hours=2)) == []
    assert [reminder.kind for reminder in storage.due(NOW + timedelta(hours=5))] == ["срок"]


def test_dialog_is_saved_and_dropped(storage: Storage) -> None:
    draft = Draft(text="Не открывается 1С", category=Category.SOFTWARE, source={"category": "правила"})
    storage.save_dialog(CHAT, draft, "place", NOW)
    saved = storage.dialog(CHAT)
    assert saved is not None
    restored, asked = saved
    assert (restored.text, restored.category, asked) == (draft.text, Category.SOFTWARE, "place")
    storage.drop_dialog(CHAT)
    assert storage.dialog(CHAT) is None


def test_updates_are_processed_once(storage: Storage) -> None:
    assert storage.seen(15) is False
    assert storage.seen(15) is True
    assert storage.offset() == 0
    storage.set_offset(16)
    assert storage.offset() == 16
