from datetime import datetime, timedelta, timezone

import pytest

from game_store import GamesStore, GamesStoreError


UTC = timezone.utc


def game(game_id: str = "game-1", **changes):
    result = {
        "id": game_id,
        "match_at": "2026-10-18T21:00:00Z",
        "poll_at": "2026-10-16T08:00:00Z",
        "question": "Тур 5\nПлощадка №3\nАвито — Соперник",
        "status": "scheduled",
        "sent_chat_ids": [],
    }
    result.update(changes)
    return result


def test_save_and_load_normalizes_games_atomically(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    store.save([game()])

    assert store.load() == [game()]
    assert not list(tmp_path.glob("*.tmp"))


def test_load_rejects_invalid_json(tmp_path):
    games_path = tmp_path / "games.json"
    games_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(GamesStoreError, match="Could not parse"):
        GamesStore(games_path).load()


def test_load_rejects_invalid_game_structure(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    store.path.write_text('[{"id": "missing-fields"}]', encoding="utf-8")

    with pytest.raises(GamesStoreError, match="missing fields"):
        store.load()


def test_mark_delivery_marks_game_as_sent_only_after_all_target_chats(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    store.save([game()])

    partially_sent = store.mark_delivery("game-1", 10, [10, 20])
    assert partially_sent["status"] == "scheduled"
    assert partially_sent["sent_chat_ids"] == [10]

    sent = store.mark_delivery("game-1", 20, [10, 20])
    assert sent["status"] == "sent"
    assert sent["sent_chat_ids"] == [10, 20]


def test_mark_expired_only_changes_overdue_scheduled_games(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    now = datetime(2026, 10, 17, tzinfo=UTC)
    overdue = game("overdue", poll_at="2026-10-16T08:00:00Z")
    future = game(
        "future",
        match_at="2026-10-20T21:00:00Z",
        poll_at="2026-10-18T08:00:00Z",
    )
    store.save([overdue, future])

    expired = store.mark_expired(now)

    assert [item["id"] for item in expired] == ["overdue"]
    assert store.get("overdue")["status"] == "expired"
    assert store.get("future")["status"] == "scheduled"


def test_delete_removes_past_or_sent_game_without_special_handling(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    store.save([game(status="sent", sent_chat_ids=[10])])

    deleted = store.delete("game-1")

    assert deleted["status"] == "sent"
    assert store.load() == []


def test_question_cannot_exceed_telegram_limit(tmp_path):
    store = GamesStore(tmp_path / "games.json")

    with pytest.raises(GamesStoreError, match="at most 300"):
        store.save([game(question="x" * 301)])


def test_new_game_with_teams_must_include_avito_and_preserves_team_order(tmp_path):
    store = GamesStore(tmp_path / "games.json")
    store.save([game(first_team="Соперник", second_team="Авито")])

    saved_game = store.load()[0]
    assert saved_game["first_team"] == "Соперник"
    assert saved_game["second_team"] == "Авито"

    with pytest.raises(GamesStoreError, match="One team must be Авито"):
        store.save([game(first_team="Команда 1", second_team="Команда 2")])
