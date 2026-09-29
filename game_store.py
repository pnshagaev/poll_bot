from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc
GAME_STATUSES = {"scheduled", "sent", "expired"}


class GamesStoreError(ValueError):
    """Raised when the games file has an invalid structure."""


def parse_utc_datetime(value: str) -> datetime:
    if not isinstance(value, str):
        raise GamesStoreError("Date must be an ISO 8601 string")

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GamesStoreError(f"Invalid ISO 8601 date: {value}") from error

    if parsed.tzinfo is None:
        raise GamesStoreError("Date must include a timezone")

    return parsed.astimezone(UTC)


def format_utc_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_game(game: dict[str, Any]) -> dict[str, Any]:
    required_fields = {"id", "match_at", "poll_at", "question", "status"}
    missing_fields = required_fields - game.keys()
    if missing_fields:
        raise GamesStoreError(f"Game is missing fields: {', '.join(sorted(missing_fields))}")

    game_id = game["id"]
    question = game["question"]
    status = game["status"]
    if not isinstance(game_id, str) or not game_id:
        raise GamesStoreError("Game id must be a non-empty string")
    if not isinstance(question, str) or not question.strip():
        raise GamesStoreError("Game question must be a non-empty string")
    if len(question) > 300:
        raise GamesStoreError("Game question must be at most 300 characters")
    if status not in GAME_STATUSES:
        raise GamesStoreError(f"Unknown game status: {status}")

    match_at = parse_utc_datetime(game["match_at"])
    poll_at = parse_utc_datetime(game["poll_at"])
    if poll_at >= match_at:
        raise GamesStoreError("Poll date must be earlier than match date")

    sent_chat_ids = game.get("sent_chat_ids", [])
    if not isinstance(sent_chat_ids, list) or any(
        isinstance(chat_id, bool) or not isinstance(chat_id, int)
        for chat_id in sent_chat_ids
    ):
        raise GamesStoreError("sent_chat_ids must be an array of integer chat IDs")

    return {
        "id": game_id,
        "match_at": format_utc_datetime(match_at),
        "poll_at": format_utc_datetime(poll_at),
        "question": question,
        "status": status,
        "sent_chat_ids": list(dict.fromkeys(sent_chat_ids)),
    }


class GamesStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        try:
            raw_games = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise GamesStoreError(f"Could not parse {self.path.name}") from error

        if not isinstance(raw_games, list):
            raise GamesStoreError("Games file must contain a JSON array")

        games = [validate_game(game) for game in raw_games if isinstance(game, dict)]
        if len(games) != len(raw_games):
            raise GamesStoreError("Every game must be a JSON object")

        game_ids = [game["id"] for game in games]
        if len(game_ids) != len(set(game_ids)):
            raise GamesStoreError("Game IDs must be unique")

        return sorted(games, key=lambda game: game["poll_at"])

    def save(self, games: list[dict[str, Any]]) -> None:
        validated_games = [validate_game(game) for game in games]
        game_ids = [game["id"] for game in validated_games]
        if len(game_ids) != len(set(game_ids)):
            raise GamesStoreError("Game IDs must be unique")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump(validated_games, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            Path(temporary_path).unlink(missing_ok=True)
            raise

    def get(self, game_id: str) -> dict[str, Any] | None:
        return next((game for game in self.load() if game["id"] == game_id), None)

    def add(self, game: dict[str, Any]) -> dict[str, Any]:
        games = self.load()
        validated_game = validate_game(game)
        if any(existing_game["id"] == validated_game["id"] for existing_game in games):
            raise GamesStoreError(f"Game {validated_game['id']} already exists")
        games.append(validated_game)
        self.save(games)
        return validated_game

    def delete(self, game_id: str) -> dict[str, Any] | None:
        games = self.load()
        game = next((game for game in games if game["id"] == game_id), None)
        if game is None:
            return None
        self.save([game for game in games if game["id"] != game_id])
        return game

    def mark_delivery(
        self,
        game_id: str,
        chat_id: int,
        target_chat_ids: list[int],
    ) -> dict[str, Any] | None:
        games = self.load()
        updated_game = None
        for game in games:
            if game["id"] != game_id:
                continue
            sent_chat_ids = set(game["sent_chat_ids"])
            sent_chat_ids.add(chat_id)
            game["sent_chat_ids"] = sorted(sent_chat_ids)
            if set(target_chat_ids).issubset(sent_chat_ids):
                game["status"] = "sent"
            updated_game = game
            break

        if updated_game is not None:
            self.save(games)
        return updated_game

    def mark_expired(self, now: datetime) -> list[dict[str, Any]]:
        now = now.astimezone(UTC)
        games = self.load()
        expired_games = []
        for game in games:
            if game["status"] == "scheduled" and parse_utc_datetime(game["poll_at"]) <= now:
                game["status"] = "expired"
                expired_games.append(game)

        if expired_games:
            self.save(games)
        return expired_games
