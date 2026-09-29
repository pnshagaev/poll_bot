import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from basketball_poll_bot import (
    ADD_CUSTOM_POLL_AT,
    ADD_CUSTOM_VENUE,
    ADD_OPPONENT,
    UTC,
    add_custom_venue,
    add_match_at,
    add_venue,
    add_game_confirm,
    cancel_conversation,
    delete_game_confirm,
    games,
    help_command,
    post_init,
    schedule,
)
from commands import BOT_COMMANDS
from game_store import GamesStore
from telegram.ext import ConversationHandler


class FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.answered = False
        self.edited_text = None

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs):
        self.edited_text = text


class FakeJobQueue:
    def __init__(self):
        self.created = []
        self.lookups = []

    def run_once(self, **kwargs):
        self.created.append(kwargs)

    def get_jobs_by_name(self, name):
        self.lookups.append(name)
        return []


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append((text, kwargs))


class FakeBot:
    def __init__(self):
        self.commands = None

    async def set_my_commands(self, commands):
        self.commands = commands


def draft(now: datetime):
    return {
        "tour": "Тур 6",
        "match_at": now + timedelta(days=4),
        "poll_at": now + timedelta(days=2),
        "venue": "Площадка №3",
        "opponent": "Авито",
        "question": "Тур 6\nПлощадка №3\nEndorphin Group — Авито",
    }


def test_confirming_interactive_addition_persists_game_and_schedules_all_chats(tmp_path):
    now = datetime.now(UTC).replace(microsecond=0)
    store = GamesStore(tmp_path / "games.json")
    queue = FakeJobQueue()
    application = SimpleNamespace(
        job_queue=queue,
        bot_data={"games_store": store, "chat_ids": [10, 20]},
    )
    query = FakeQuery("add-confirm")
    context = SimpleNamespace(user_data={"game_draft": draft(now)}, application=application)
    update = SimpleNamespace(callback_query=query)

    asyncio.run(add_game_confirm(update, context))

    games = store.load()
    assert len(games) == 1
    assert games[0]["status"] == "scheduled"
    assert len(queue.created) == 2
    assert {job["chat_id"] for job in queue.created} == {10, 20}
    assert "game_draft" not in context.user_data
    assert query.answered
    assert "Игра добавлена" in query.edited_text


def test_addition_requires_a_custom_poll_time_when_default_time_is_already_past():
    match_at = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=1)
    message = FakeMessage(match_at.strftime("%d.%m.%Y %H:%M"))
    context = SimpleNamespace(user_data={"game_draft": {}})
    update = SimpleNamespace(effective_message=message)

    next_state = asyncio.run(add_match_at(update, context))

    assert next_state == ADD_CUSTOM_POLL_AT
    assert "Расчётное время опроса уже прошло" in message.replies[0][0]


def test_games_list_is_available_to_non_owner(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    message = FakeMessage("")
    application = SimpleNamespace(
        bot_data={"games_store": store, "owner_user_id": 41879174},
    )
    context = SimpleNamespace(application=application)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_message=message,
    )

    asyncio.run(games(update, context))

    assert message.replies[0][0] == "В games.json пока нет игр."
    assert message.replies[0][1]["reply_markup"] is None


def test_games_list_with_games_is_available_to_non_owner_without_management_buttons(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    store.save(
        [
            {
                "id": "game-1",
                "match_at": "2026-10-18T21:00:00Z",
                "poll_at": "2026-10-16T08:00:00Z",
                "question": "Тур 5\nПлощадка №3\nEndorphin Group — Авито",
                "status": "scheduled",
                "sent_chat_ids": [],
            }
        ]
    )
    message = FakeMessage("")
    application = SimpleNamespace(
        bot_data={"games_store": store, "owner_user_id": 41879174},
    )
    context = SimpleNamespace(application=application)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_message=message,
    )

    asyncio.run(games(update, context))

    assert "Тур 5" in message.replies[0][0]
    assert message.replies[0][1]["reply_markup"] is None


def test_schedule_is_available_to_every_user():
    message = FakeMessage("")
    context = SimpleNamespace(
        job_queue=SimpleNamespace(jobs=lambda: [SimpleNamespace(name="daily-training")]),
    )
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=1))

    asyncio.run(schedule(update, context))

    assert message.replies[0][0] == "daily-training"


def test_help_command_uses_the_shared_command_registry():
    message = FakeMessage("")
    update = SimpleNamespace(effective_message=message)

    asyncio.run(help_command(update, SimpleNamespace()))

    assert "/games — Список игр" in message.replies[0][0]


def test_post_init_publishes_the_shared_command_registry():
    bot = FakeBot()

    asyncio.run(post_init(SimpleNamespace(bot=bot)))

    assert bot.commands == BOT_COMMANDS


def test_standard_venue_button_advances_the_add_game_conversation():
    query = FakeQuery("add-venue:2")
    context = SimpleNamespace(user_data={"game_draft": {}})
    update = SimpleNamespace(callback_query=query)

    next_state = asyncio.run(add_venue(update, context))

    assert next_state == ADD_OPPONENT
    assert context.user_data["game_draft"]["venue"] == "Площадка №3"
    assert query.edited_text == "Введите соперника:"


def test_other_venue_button_requests_a_custom_venue():
    query = FakeQuery("add-venue:other")
    context = SimpleNamespace(user_data={"game_draft": {}})
    update = SimpleNamespace(callback_query=query)

    next_state = asyncio.run(add_venue(update, context))

    assert next_state == ADD_CUSTOM_VENUE
    assert query.edited_text == "Введите название площадки:"


def test_custom_venue_is_saved_before_requesting_an_opponent():
    context = SimpleNamespace(user_data={"game_draft": {}})
    update = SimpleNamespace(effective_message=FakeMessage("Зал школы №42"))

    next_state = asyncio.run(add_custom_venue(update, context))

    assert next_state == ADD_OPPONENT
    assert context.user_data["game_draft"]["venue"] == "Зал школы №42"


def test_cancel_clears_an_interactive_draft():
    context = SimpleNamespace(user_data={"game_draft": {"tour": "Тур 6"}})
    update = SimpleNamespace(effective_message=FakeMessage(""), callback_query=None)

    next_state = asyncio.run(cancel_conversation(update, context))

    assert next_state == ConversationHandler.END
    assert "game_draft" not in context.user_data


def test_deleting_a_past_game_only_updates_json_without_job_queue_lookup(tmp_path):
    now = datetime.now(UTC).replace(microsecond=0)
    store = GamesStore(tmp_path / "games.json")
    store.save(
        [
            {
                "id": "past-game",
                "match_at": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "poll_at": (now - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
                "question": "Прошедшая игра",
                "status": "expired",
                "sent_chat_ids": [],
            }
        ]
    )
    queue = FakeJobQueue()
    application = SimpleNamespace(job_queue=queue, bot_data={"games_store": store, "chat_ids": [10]})
    query = FakeQuery("delete-confirm:past-game:0")
    context = SimpleNamespace(application=application)
    update = SimpleNamespace(callback_query=query)

    asyncio.run(delete_game_confirm(update, context))

    assert store.load() == []
    assert queue.lookups == []
    assert query.edited_text == "Игра удалена из games.json."


def test_deleting_a_future_game_cancels_its_jobs_before_removing_json(tmp_path):
    now = datetime.now(UTC).replace(microsecond=0)
    store = GamesStore(tmp_path / "games.json")
    store.save(
        [
            {
                "id": "future-game",
                "match_at": (now + timedelta(days=3)).isoformat().replace("+00:00", "Z"),
                "poll_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "question": "Будущая игра",
                "status": "scheduled",
                "sent_chat_ids": [],
            }
        ]
    )
    queue = FakeJobQueue()
    application = SimpleNamespace(job_queue=queue, bot_data={"games_store": store, "chat_ids": [10, 20]})
    query = FakeQuery("delete-confirm:future-game:0")
    context = SimpleNamespace(application=application)
    update = SimpleNamespace(callback_query=query)

    asyncio.run(delete_game_confirm(update, context))

    assert store.load() == []
    assert queue.lookups == ["game:future-game:10", "game:future-game:20"]
