import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import basketball_poll_bot
from basketball_poll_bot import (
    ADD_CUSTOM_POLL_AT,
    ADD_CUSTOM_VENUE,
    ADD_CUSTOM_FIRST_TEAM,
    ADD_CUSTOM_SECOND_TEAM,
    ADD_CONFIRM,
    ADD_FIRST_TEAM,
    ADD_POLL_AT,
    ADD_SECOND_TEAM,
    ADD_VENUE,
    MOSCOW,
    add_custom_first_team,
    add_custom_poll_at,
    add_custom_second_team,
    add_custom_venue,
    add_first_team,
    add_match_at,
    add_poll_method,
    add_second_team,
    add_venue,
    add_game_confirm,
    cancel_conversation,
    delete_game_confirm,
    games,
    help_command,
    post_init,
    restore_games,
    schedule,
    send_game_poll,
    show_game_preview,
    suggested_poll_at,
)
from commands import BOT_COMMANDS
from game_store import GamesStore, format_utc_datetime, parse_utc_datetime
from telegram.error import TelegramError
from telegram.ext import ConversationHandler


class FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.answered = False
        self.edited_text = None
        self.reply_markup = None

    async def answer(self, *args, **kwargs):
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs):
        self.edited_text = text
        self.reply_markup = kwargs.get("reply_markup")


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
    match_at = now + timedelta(days=4)
    return {
        "tour": "Тур 6",
        "match_at": match_at,
        "poll_at": suggested_poll_at(match_at),
        "venue": "Площадка №3",
        "first_team": "Авито",
        "second_team": "Соперник",
    }


def test_confirming_interactive_addition_persists_game_and_schedules_all_chats(tmp_path):
    now = datetime.now(MOSCOW).replace(microsecond=0)
    store = GamesStore(tmp_path / "games.json")
    queue = FakeJobQueue()
    application = SimpleNamespace(
        job_queue=queue,
        bot_data={"games_store": store, "chat_ids": [10, 20]},
    )
    query = FakeQuery("add-confirm")
    game_draft = draft(now)
    context = SimpleNamespace(user_data={"game_draft": game_draft}, application=application)
    update = SimpleNamespace(callback_query=query)

    assert asyncio.run(show_game_preview(update, context)) == ADD_CONFIRM
    asyncio.run(add_game_confirm(update, context))

    games = store.load()
    assert len(games) == 1
    assert games[0]["status"] == "scheduled"
    assert games[0]["question"] == game_draft["question"]
    assert games[0]["match_at"] == format_utc_datetime(game_draft["match_at"])
    assert games[0]["poll_at"] == format_utc_datetime(game_draft["poll_at"])
    assert len(queue.created) == 2
    assert {job["chat_id"] for job in queue.created} == {10, 20}
    assert all(job["when"].tzinfo is MOSCOW and job["when"] == game_draft["poll_at"] for job in queue.created)
    assert "game_draft" not in context.user_data
    assert query.answered
    assert query.edited_text == (
        f"Игра добавлена. Опрос назначен на {game_draft['poll_at']:%d.%m.%Y %H:%M}."
    )


def test_addition_offers_immediate_delivery_first_when_suggested_time_has_passed():
    match_at = datetime.now(MOSCOW).replace(second=0, microsecond=0) + timedelta(days=1)
    message = FakeMessage(match_at.strftime("%d.%m.%Y %H:%M"))
    context = SimpleNamespace(user_data={"game_draft": {}})
    update = SimpleNamespace(effective_message=message)

    next_state = asyncio.run(add_match_at(update, context))

    assert next_state == ADD_POLL_AT
    keyboard = message.replies[0][1]["reply_markup"].inline_keyboard
    assert [row[0].callback_data for row in keyboard] == ["add-send-now", "add-custom-poll", "cancel"]
    assert keyboard[0][0].text == "Отправить сейчас"


@pytest.mark.parametrize("fail_second_chat", [False, True])
def test_immediate_poll_is_sent_after_preview_confirmation(tmp_path, fail_second_chat):
    now = datetime.now(MOSCOW)
    game_draft = draft(now)
    game_draft["match_at"] = now + timedelta(days=1)
    game_draft["poll_at"] = suggested_poll_at(game_draft["match_at"])
    store = GamesStore(tmp_path / "games.json")
    queue = FakeJobQueue()
    application = SimpleNamespace(
        job_queue=queue,
        bot_data={"games_store": store, "chat_ids": [10, 20]},
    )

    async def send_poll(*, chat_id, **kwargs):
        if fail_second_chat and chat_id == 20:
            raise TelegramError("Cannot send to chat 20")
        return SimpleNamespace(message_id=1)

    bot = SimpleNamespace(
        send_poll=AsyncMock(side_effect=send_poll),
        pin_chat_message=AsyncMock(),
        send_message=AsyncMock(),
    )
    context = SimpleNamespace(user_data={"game_draft": game_draft}, application=application, bot=bot)
    query = FakeQuery("add-send-now")
    update = SimpleNamespace(callback_query=query)

    assert asyncio.run(add_poll_method(update, context)) == ADD_VENUE
    assert asyncio.run(show_game_preview(update, context)) == ADD_CONFIRM
    assert query.edited_text.endswith("Опрос: сразу после подтверждения")
    assert query.reply_markup.inline_keyboard[0][0].text == "Добавить и отправить"
    bot.send_poll.assert_not_called()
    query.data = "add-confirm"

    assert asyncio.run(add_game_confirm(update, context)) == ConversationHandler.END

    game, = store.load()
    expected_chats = [10] if fail_second_chat else [10, 20]
    assert game["send_now"] is True
    assert game["sent_chat_ids"] == expected_chats
    assert game["status"] == ("expired" if fail_second_chat else "sent")
    assert now <= parse_utc_datetime(game["poll_at"]) <= datetime.now(MOSCOW)
    assert [call.kwargs["chat_id"] for call in bot.send_poll.await_args_list] == [10, 20]
    assert [call.kwargs["chat_id"] for call in bot.pin_chat_message.await_args_list] == expected_chats
    assert query.edited_text == f"Игра добавлена. Опрос отправлен в {len(expected_chats)} из 2 чатов."
    assert queue.created == []


def test_custom_poll_date_uses_1000_moscow():
    poll_at = (datetime.now(MOSCOW) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    game_draft = {"match_at": poll_at + timedelta(days=1)}
    context = SimpleNamespace(user_data={"game_draft": game_draft})
    update = SimpleNamespace(effective_message=FakeMessage(f"{poll_at:%d.%m.%Y} 18:00"))

    assert asyncio.run(add_custom_poll_at(update, context)) == ADD_CUSTOM_POLL_AT
    assert "poll_at" not in game_draft

    update.effective_message = FakeMessage(f"{poll_at:%d.%m.%Y}")
    assert asyncio.run(add_custom_poll_at(update, context)) == ADD_VENUE
    assert game_draft["poll_at"] == poll_at
    assert game_draft["poll_at"].tzinfo is MOSCOW


@pytest.mark.parametrize(("hours_until_match", "poll_day_offset"), [(12, 0), (1, -1)])
def test_restore_moves_pending_poll_to_1000_before_match(tmp_path, hours_until_match, poll_day_offset):
    old_poll_at = (datetime.now(MOSCOW) + timedelta(days=4)).replace(hour=8, minute=0, second=0, microsecond=0)
    store = GamesStore(tmp_path / "games.json")
    store.add({
        "id": "game-1",
        "match_at": format_utc_datetime(old_poll_at + timedelta(hours=hours_until_match)),
        "poll_at": format_utc_datetime(old_poll_at),
        "question": "Тур 5\nПлощадка №3\nАвито - Соперник",
        "status": "scheduled",
        "sent_chat_ids": [10],
    })
    expected_poll_at = old_poll_at.replace(hour=10) + timedelta(days=poll_day_offset)
    queue = FakeJobQueue()
    application = SimpleNamespace(job_queue=queue)

    restore_games(application, store, [10, 20])

    assert store.get("game-1")["poll_at"] == format_utc_datetime(expected_poll_at)
    assert [(job["chat_id"], job["when"]) for job in queue.created] == [(20, expected_poll_at)]


def test_restore_expires_an_interrupted_immediate_poll(tmp_path, monkeypatch):
    now = datetime(2026, 11, 17, 9, tzinfo=MOSCOW)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(basketball_poll_bot, "datetime", FixedDatetime)
    store = GamesStore(tmp_path / "games.json")
    poll_at = format_utc_datetime(now - timedelta(minutes=1))
    store.add({
        "id": "game-1",
        "match_at": format_utc_datetime(now + timedelta(days=1)),
        "poll_at": poll_at,
        "question": "Тур 5\nПлощадка №3\nАвито - Соперник",
        "status": "scheduled",
        "sent_chat_ids": [10],
        "send_now": True,
    })
    queue = FakeJobQueue()

    restore_games(SimpleNamespace(job_queue=queue), store, [10, 20])

    game = store.get("game-1")
    assert game["status"] == "expired"
    assert game["poll_at"] == poll_at
    assert game["sent_chat_ids"] == [10]
    assert queue.created == []


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
                "question": "Тур 5 (Вс) 18.10.2026\t21:00\nПлощадка №3\nАвито - Соперник",
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

    assert message.replies[0][0].startswith("Тур 5 (Пн) 19.10.2026 00:00\n")
    assert "Матч: 19.10.2026 00:00\nОпрос: 16.10.2026 11:00\n" in message.replies[0][0]
    assert message.replies[0][1]["reply_markup"] is None


def test_sending_a_preexisting_poll_displays_moscow_time(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    store.add({
        "id": "game-1",
        "match_at": "2026-10-18T21:00:00Z",
        "poll_at": "2026-10-16T08:00:00Z",
        "question": "Тур 5\nПлощадка №3\n(Вс) 18.10.2026 21:00\nАвито (Белый) - Соперник (Черный)",
        "status": "scheduled",
    })
    bot = SimpleNamespace(send_poll=AsyncMock(), pin_chat_message=AsyncMock())
    context = SimpleNamespace(
        bot=bot,
        job=SimpleNamespace(chat_id=10, data={"game_id": "game-1"}),
        application=SimpleNamespace(bot_data={"games_store": store, "chat_ids": [10]}),
    )

    asyncio.run(send_game_poll(context))

    bot.send_poll.assert_awaited_once_with(
        chat_id=10,
        question="Тур 5\nПлощадка №3\n(Пн) 19.10.2026 00:00\nАвито (Белый) - Соперник (Черный)",
        options=["Буду", "Не буду", "50/50"],
        is_anonymous=False,
    )
    assert store.get("game-1")["status"] == "sent"


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

    assert next_state == ADD_FIRST_TEAM
    assert context.user_data["game_draft"]["venue"] == "Площадка №3"
    assert query.edited_text == "Выберите первую команду. Порядок команд влияет на цвет формы:"


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

    assert next_state == ADD_FIRST_TEAM
    assert context.user_data["game_draft"]["venue"] == "Зал школы №42"


def test_avito_can_be_selected_as_the_first_team():
    query = FakeQuery("add-first-team:avito")
    context = SimpleNamespace(user_data={"game_draft": {}})
    update = SimpleNamespace(callback_query=query)

    next_state = asyncio.run(add_first_team(update, context))

    assert next_state == ADD_SECOND_TEAM
    assert context.user_data["game_draft"]["first_team"] == "Авито"
    assert query.edited_text == "Выберите вторую команду. Порядок команд влияет на цвет формы:"


def test_custom_first_team_requires_avito_as_the_second_team():
    message = FakeMessage("Соперник")
    context = SimpleNamespace(user_data={"game_draft": {}})
    update = SimpleNamespace(effective_message=message)

    next_state = asyncio.run(add_custom_first_team(update, context))

    assert next_state == ADD_SECOND_TEAM
    assert context.user_data["game_draft"]["first_team"] == "Соперник"


def test_avito_can_be_selected_as_the_second_team_after_a_custom_first_team():
    query = FakeQuery("add-second-team:avito")
    context = SimpleNamespace(
        user_data={
            "game_draft": {
                "tour": "Тур 6",
                "match_at": datetime(2026, 10, 18, 21, tzinfo=MOSCOW),
                "poll_at": datetime(2026, 10, 16, 10, tzinfo=MOSCOW),
                "venue": "Площадка №3",
                "first_team": "Соперник",
            }
        }
    )
    update = SimpleNamespace(callback_query=query, effective_message=FakeMessage(""))

    next_state = asyncio.run(add_second_team(update, context))

    assert next_state == ADD_CONFIRM
    assert context.user_data["game_draft"]["second_team"] == "Авито"
    question = context.user_data["game_draft"]["question"]
    assert question == "Тур 6\nПлощадка №3\n(Вс) 18.10.2026 21:00\nСоперник (Белый) - Авито (Черный)"
    assert query.edited_text == f"Предпросмотр:\n\n{question}\n\nОпрос: 16.10.2026 10:00"


def test_manual_second_team_requires_avito_when_it_is_not_the_first_team():
    message = FakeMessage("Другая команда")
    context = SimpleNamespace(user_data={"game_draft": {"first_team": "Соперник"}})
    update = SimpleNamespace(effective_message=message)

    next_state = asyncio.run(add_custom_second_team(update, context))

    assert next_state == ADD_SECOND_TEAM
    assert "Одна из команд должна быть Авито" in message.replies[0][0]


def test_second_team_can_offer_avito_but_rejects_duplicate_avito():
    query = FakeQuery("add-second-team:avito")
    context = SimpleNamespace(user_data={"game_draft": {"first_team": "Авито"}})
    update = SimpleNamespace(callback_query=query)

    next_state = asyncio.run(add_second_team(update, context))

    assert next_state == ADD_SECOND_TEAM
    assert "second_team" not in context.user_data["game_draft"]


def test_cancel_clears_an_interactive_draft():
    context = SimpleNamespace(user_data={"game_draft": {"tour": "Тур 6"}})
    update = SimpleNamespace(effective_message=FakeMessage(""), callback_query=None)

    next_state = asyncio.run(cancel_conversation(update, context))

    assert next_state == ConversationHandler.END
    assert "game_draft" not in context.user_data


def test_deleting_a_past_game_only_updates_json_without_job_queue_lookup(tmp_path):
    now = datetime.now(MOSCOW).replace(microsecond=0)
    store = GamesStore(tmp_path / "games.json")
    store.save(
        [
            {
                "id": "past-game",
                "match_at": format_utc_datetime(now - timedelta(days=1)),
                "poll_at": format_utc_datetime(now - timedelta(days=3)),
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
    now = datetime.now(MOSCOW).replace(microsecond=0)
    store = GamesStore(tmp_path / "games.json")
    store.save(
        [
            {
                "id": "future-game",
                "match_at": format_utc_datetime(now + timedelta(days=3)),
                "poll_at": format_utc_datetime(now + timedelta(days=1)),
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
