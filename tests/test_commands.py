from commands import BOT_COMMANDS, COMMAND_DEFINITIONS, help_text


def test_command_registry_contains_each_current_command_once():
    commands = [command for command, _ in COMMAND_DEFINITIONS]

    assert commands == [
        "start",
        "help",
        "ping",
        "schedule",
        "games",
        "addgame",
        "deletegame",
        "test",
        "cancel",
    ]
    assert [command.command for command in BOT_COMMANDS] == commands


def test_help_text_is_generated_from_the_command_registry():
    text = help_text()

    assert "/start — Начать работу с ботом" in text
    assert "/games — Список игр" in text
    assert "/addgame — Добавить игру — только администратор" in text
    assert "/cancel — Отменить добавление или удаление игры" in text
