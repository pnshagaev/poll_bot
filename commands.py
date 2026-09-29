from __future__ import annotations

from telegram import BotCommand


COMMAND_DEFINITIONS = [
    ("start", "Начать работу с ботом"),
    ("help", "Список команд"),
    ("ping", "Проверить, что бот отвечает"),
    ("schedule", "Показать запланированные задачи"),
    ("games", "Список игр"),
    ("addgame", "Добавить игру — только администратор"),
    ("deletegame", "Удалить игру — только администратор"),
    ("test", "Создать тестовый опрос — только администратор"),
    ("cancel", "Отменить добавление или удаление игры"),
]

BOT_COMMANDS = [BotCommand(command, description) for command, description in COMMAND_DEFINITIONS]


def help_text() -> str:
    return "Доступные команды:\n\n" + "\n".join(
        f"/{command} — {description}" for command, description in COMMAND_DEFINITIONS
    )
