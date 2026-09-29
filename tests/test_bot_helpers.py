from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from basketball_poll_bot import (
    cancel_game_jobs,
    delete_keyboard,
    format_game_question,
    game_job_name,
    is_owner,
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


def test_suggested_poll_is_two_days_before_match_at_0800_utc():
    match_at = datetime(2026, 10, 18, 21, tzinfo=UTC)

    assert suggested_poll_at(match_at) == datetime(2026, 10, 16, 8, tzinfo=UTC)


def test_format_game_question_uses_russian_weekday_and_expected_layout():
    question = format_game_question(
        "Тур 5",
        datetime(2026, 10, 18, 21, tzinfo=UTC),
        "Площадка №3",
        "Авито",
        "Соперник",
    )

    assert question == "Тур 5 (Вс) 18.10.2026\t21:00\nПлощадка №3\nАвито\t-\tСоперник"


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
