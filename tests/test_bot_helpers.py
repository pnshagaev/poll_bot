from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telegram.ext import Application

from basketball_poll_bot import (
    MOSCOW,
    cancel_game_jobs,
    delete_keyboard,
    format_game_question,
    game_job_name,
    is_owner,
    localize_stored_question,
    parse_user_datetime,
    set_daily_polls,
    should_cancel_game_job,
    suggested_poll_at,
)


UTC = timezone.utc


def scheduled_game(poll_at: datetime):
    return {
        "id": "game-1",
        "match_at": "2026-10-18T21:00:00Z",
        "poll_at": poll_at.isoformat().replace("+00:00", "Z"),
        "question": "Тур 5\nПлощадка №3\nАвито — Соперник",
        "status": "scheduled",
        "sent_chat_ids": [],
    }


def test_entered_datetime_is_interpreted_in_moscow():
    match_at = parse_user_datetime("19.10.2026 00:30")

    assert match_at.tzinfo is MOSCOW
    assert match_at.astimezone(UTC) == datetime(2026, 10, 18, 21, 30, tzinfo=UTC)


def test_suggested_poll_is_two_local_dates_before_match_at_1000_moscow():
    match_at = datetime(2026, 10, 18, 21, tzinfo=UTC)

    assert suggested_poll_at(match_at) == datetime(2026, 10, 17, 10, tzinfo=MOSCOW)


def test_format_game_question_uses_russian_weekday_and_expected_layout():
    question = format_game_question(
        "Тур 5",
        datetime(2026, 10, 18, 21, tzinfo=UTC),
        "Площадка №3",
        "Авито",
        "Соперник",
    )

    assert question == "Тур 5\nПлощадка №3\n(Пн) 19.10.2026 00:00\nАвито (Белый) - Соперник (Черный)"


@pytest.mark.parametrize(
    ("layout", "stored_time"),
    [
        ("Тур 5 {match_time}\nПлощадка №3\nАвито - Соперник", "(Вс) 18.10.2026\t21:00"),
        ("Тур 5\n{match_time}\nПлощадка №3\nАвито - Соперник", "(Вс) 18.10.2026 21:00"),
        ("Тур 5\nПлощадка №3\n{match_time}\nАвито - Соперник", "(Вс) 18.10.2026 21:00"),
        ("Тур 5\nПлощадка №3\n{match_time}\nАвито - Соперник", "(Пн) 19.10.2026 00:00"),
    ],
)
def test_stored_question_displays_moscow_time(layout, stored_time):
    match_at = datetime(2026, 10, 18, 21, tzinfo=UTC)
    question = layout.format(match_time=stored_time)

    assert localize_stored_question(question, match_at) == layout.format(match_time="(Пн) 19.10.2026 00:00")


def test_recurring_polls_run_sunday_and_tuesday_at_1000_moscow():
    application = Application.builder().token("123:test").build()
    set_daily_polls(application, 10)
    reference = datetime(2026, 10, 17, tzinfo=UTC)

    next_runs = {
        job.data["question"]: job.job.trigger.get_next_fire_time(None, reference)
        for job in application.job_queue.jobs()
    }
    assert next_runs == {
        "Тренировка завтра": datetime(2026, 10, 18, 7, tzinfo=UTC),
        "Игровая завтра": datetime(2026, 10, 20, 7, tzinfo=UTC),
    }


def test_only_owner_id_can_use_admin_commands():
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"owner_user_id": 41879174}))

    assert is_owner(SimpleNamespace(effective_user=SimpleNamespace(id=41879174)), context)
    assert not is_owner(SimpleNamespace(effective_user=SimpleNamespace(id=1)), context)


def test_only_future_scheduled_games_need_job_queue_cancellation():
    now = datetime(2026, 10, 1, tzinfo=UTC)

    assert should_cancel_game_job(scheduled_game(now + timedelta(days=1)), now)
    assert not should_cancel_game_job(scheduled_game(now - timedelta(days=1)), now)
    assert not should_cancel_game_job({**scheduled_game(now + timedelta(days=1)), "status": "sent"}, now)


def test_cancel_game_jobs_removes_a_job_for_every_target_chat():
    jobs = {chat_id: SimpleNamespace(removed=False) for chat_id in [10, 20]}
    for job in jobs.values():
        job.schedule_removal = lambda job=job: setattr(job, "removed", True)

    queue = SimpleNamespace(
        get_jobs_by_name=lambda name: [jobs[int(name.rsplit(":", 1)[1])]]
    )
    application = SimpleNamespace(job_queue=queue)

    cancel_game_jobs(application, "game-1", [10, 20])

    assert all(job.removed for job in jobs.values())
    assert game_job_name("game-1", 10) == "game:game-1:10"


def test_delete_keyboard_contains_all_games_on_the_requested_page():
    games = [scheduled_game(datetime(2026, 10, 16, 8, tzinfo=UTC)) for _ in range(7)]
    for index, game in enumerate(games):
        game["id"] = f"game-{index}"

    keyboard = delete_keyboard(games, page=1)
    callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert "delete-select:game-6:1" in callback_data
    assert "delete-page:0" in callback_data
