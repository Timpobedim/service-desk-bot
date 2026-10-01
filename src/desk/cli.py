"""Командная строка: `desk run` — работа с Telegram, `desk demo` — показ без сети, `desk tickets` — список."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import sys
from collections.abc import Sequence
from pathlib import Path

from desk.bot import Bot
from desk.config import Settings, SettingsError
from desk.demo import conversation, show
from desk.llm import NullModel
from desk.scheduler import run as run_reminders
from desk.storage import Storage
from desk.telegram import TelegramApi, TelegramError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk", description="Бот приёма заявок в рабочем чате")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="слушать Telegram и принимать заявки")
    commands.add_parser("demo", help="показать разговор на готовом сценарии, без сети и без модели")
    tickets = commands.add_parser("tickets", help="показать заявки из базы")
    tickets.add_argument("--db", type=Path, help="файл базы (по умолчанию из DESK_DB)")
    tickets.add_argument("--all", action="store_true", help="включая закрытые")
    return parser


async def _serve(settings: Settings) -> int:
    api = TelegramApi(settings.require_token(), timeout=settings.poll_timeout + 10)
    storage = Storage(settings.database)
    model = settings.build_model()
    bot = Bot(api, storage, model=model, clock=settings.now)
    print(f"Бот @{await api.me()} слушает чаты. Модель: {model.name}. База: {settings.database}")
    reminders = asyncio.create_task(run_reminders(api, storage, settings.now))
    try:
        while True:
            updates = await api.updates(storage.offset(), wait=settings.poll_timeout)
            for update in updates:
                storage.set_offset(update.id + 1)
                await bot.handle(update)
    except asyncio.CancelledError:
        raise
    finally:
        reminders.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reminders
        storage.close()
        await api.close()
    return 0


def _tickets(settings: Settings, database: Path | None, everything: bool) -> int:
    storage = Storage(database or settings.database)
    try:
        tickets = storage.tickets(only_open=not everything)
        if not tickets:
            print("Заявок нет")
            return 0
        print(f"{'№':>4}  {'статус':<9} {'срочность':<9} {'срок':<12} что")
        for ticket in tickets:
            print(
                f"{ticket.id:>4}  {ticket.status.value:<9} {ticket.priority.value:<9} "
                f"{ticket.due:%d.%m %H:%M}  {ticket.title}"
            )
    finally:
        storage.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.command == "demo":
        show(asyncio.run(conversation(NullModel())))
        return 0
    if args.command == "tickets":
        return _tickets(settings, args.db, args.all)
    try:
        return asyncio.run(_serve(settings))
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 2
    except TelegramError as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nОстановлен")
        return 0


if __name__ == "__main__":
    sys.exit(main())
