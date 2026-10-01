"""Заявки, незаконченные разговоры и напоминания в SQLite.

Один файл базы вместо JSON, переписываемого целиком на каждое изменение. Обработанные обновления Telegram запоминаются:
повтор одного и того же update_id не создаёт
вторую заявку.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from desk.models import Category, Draft, Priority, Status, Ticket

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    place TEXT NOT NULL,
    text TEXT NOT NULL,
    created TEXT NOT NULL,
    due TEXT NOT NULL,
    status TEXT NOT NULL,
    assignee TEXT,
    closed TEXT
);
CREATE TABLE IF NOT EXISTS dialogs (
    chat_id INTEGER PRIMARY KEY,
    draft TEXT NOT NULL,
    asked TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    UNIQUE (ticket_id, kind)
);
CREATE TABLE IF NOT EXISTS updates (update_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tickets_chat ON tickets(chat_id, status);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(sent, at);
"""


@dataclass(frozen=True)
class Reminder:
    id: int
    kind: str
    at: datetime
    ticket: Ticket


def draft_to_json(draft: Draft) -> str:
    return json.dumps(
        {
            "text": draft.text,
            "title": draft.title,
            "category": draft.category.value if draft.category else None,
            "priority": draft.priority.value if draft.priority else None,
            "place": draft.place,
            "deadline": draft.deadline.isoformat() if draft.deadline else None,
            "contact": draft.contact,
            "source": draft.source,
        },
        ensure_ascii=False,
    )


def draft_from_json(raw: str) -> Draft:
    data = json.loads(raw)
    return Draft(
        text=str(data["text"]),
        title=data["title"],
        category=Category(data["category"]) if data["category"] else None,
        priority=Priority(data["priority"]) if data["priority"] else None,
        place=data["place"],
        deadline=datetime.fromisoformat(data["deadline"]) if data["deadline"] else None,
        contact=data["contact"],
        source=dict(data.get("source") or {}),
    )


def _ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        author=str(row["author"]),
        title=str(row["title"]),
        category=Category(row["category"]),
        priority=Priority(row["priority"]),
        place=str(row["place"]),
        text=str(row["text"]),
        created=datetime.fromisoformat(str(row["created"])),
        due=datetime.fromisoformat(str(row["due"])),
        status=Status(row["status"]),
        assignee=row["assignee"],
        closed=datetime.fromisoformat(str(row["closed"])) if row["closed"] else None,
    )


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    # --- заявки ------------------------------------------------------------------------------------

    def create(self, ticket: Ticket) -> Ticket:
        cursor = self.connection.execute(
            "INSERT INTO tickets (chat_id, author, title, category, priority, place, text, created, due, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket.chat_id,
                ticket.author,
                ticket.title,
                ticket.category.value,
                ticket.priority.value,
                ticket.place,
                ticket.text,
                ticket.created.isoformat(),
                ticket.due.isoformat(),
                ticket.status.value,
            ),
        )
        found = self.ticket(int(cursor.lastrowid or 0))
        if found is None:  # pragma: no cover — вставка только что прошла
            raise RuntimeError("Заявка не сохранилась")
        return found

    def ticket(self, ticket_id: int) -> Ticket | None:
        row = self.connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return _ticket(row) if row else None

    def tickets(self, chat_id: int | None = None, *, only_open: bool = True) -> list[Ticket]:
        query = "SELECT * FROM tickets"
        conditions: list[str] = []
        parameters: list[object] = []
        if chat_id is not None:
            conditions.append("chat_id = ?")
            parameters.append(chat_id)
        if only_open:
            conditions.append("status != ?")
            parameters.append(Status.DONE.value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        rows = self.connection.execute(query + " ORDER BY id", parameters).fetchall()
        return [_ticket(row) for row in rows]

    def set_status(
        self, ticket_id: int, status: Status, *, assignee: str | None = None, when: datetime | None = None
    ) -> Ticket | None:
        ticket = self.ticket(ticket_id)
        if ticket is None:
            return None
        closed = when.isoformat() if (status is Status.DONE and when) else None
        self.connection.execute(
            "UPDATE tickets SET status = ?, assignee = COALESCE(?, assignee), closed = ? WHERE id = ?",
            (status.value, assignee, closed, ticket_id),
        )
        if status is Status.DONE:
            self.connection.execute("DELETE FROM reminders WHERE ticket_id = ? AND sent = 0", (ticket_id,))
        return self.ticket(ticket_id)

    # --- разговор ----------------------------------------------------------------------------------

    def save_dialog(self, chat_id: int, draft: Draft, asked: str, now: datetime) -> None:
        self.connection.execute(
            "INSERT INTO dialogs (chat_id, draft, asked, updated) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET draft = excluded.draft, asked = excluded.asked,"
            " updated = excluded.updated",
            (chat_id, draft_to_json(draft), asked, now.isoformat()),
        )

    def dialog(self, chat_id: int) -> tuple[Draft, str] | None:
        row = self.connection.execute("SELECT draft, asked FROM dialogs WHERE chat_id = ?", (chat_id,)).fetchone()
        return (draft_from_json(str(row["draft"])), str(row["asked"])) if row else None

    def drop_dialog(self, chat_id: int) -> None:
        self.connection.execute("DELETE FROM dialogs WHERE chat_id = ?", (chat_id,))

    # --- напоминания -------------------------------------------------------------------------------

    def plan(self, ticket_id: int, moments: Mapping[str, datetime]) -> None:
        self.connection.executemany(
            "INSERT OR REPLACE INTO reminders (ticket_id, kind, at, sent) VALUES (?, ?, ?, 0)",
            [(ticket_id, kind, at.isoformat()) for kind, at in moments.items()],
        )

    def due(self, now: datetime) -> list[Reminder]:
        rows = self.connection.execute(
            "SELECT r.id, r.kind, r.at, t.id AS ticket_id FROM reminders r JOIN tickets t ON t.id = r.ticket_id"
            " WHERE r.sent = 0 AND r.at <= ? AND t.status != ? ORDER BY r.at",
            (now.isoformat(), Status.DONE.value),
        ).fetchall()
        reminders = []
        for row in rows:
            ticket = self.ticket(int(row["ticket_id"]))
            if ticket is not None:
                reminders.append(
                    Reminder(int(row["id"]), str(row["kind"]), datetime.fromisoformat(str(row["at"])), ticket)
                )
        return reminders

    def mark_sent(self, reminder_id: int) -> None:
        self.connection.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))

    def pending(self, ticket_id: int) -> list[str]:
        rows = self.connection.execute(
            "SELECT kind FROM reminders WHERE ticket_id = ? AND sent = 0 ORDER BY at", (ticket_id,)
        ).fetchall()
        return [str(row["kind"]) for row in rows]

    # --- служебное ---------------------------------------------------------------------------------

    def seen(self, update_id: int) -> bool:
        """True, если это обновление уже обрабатывали: Telegram повторяет их после сбоя."""
        cursor = self.connection.execute("INSERT OR IGNORE INTO updates (update_id) VALUES (?)", (update_id,))
        return cursor.rowcount == 0

    def offset(self) -> int:
        row = self.connection.execute("SELECT value FROM meta WHERE key = 'offset'").fetchone()
        return int(row["value"]) if row else 0

    def set_offset(self, value: int) -> None:
        self.connection.execute(
            "INSERT INTO meta (key, value) VALUES ('offset', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(value),),
        )

    def statistics(self) -> Sequence[tuple[str, int]]:
        rows = self.connection.execute("SELECT status, COUNT(*) AS count FROM tickets GROUP BY status").fetchall()
        return [(str(row["status"]), int(row["count"])) for row in rows]
