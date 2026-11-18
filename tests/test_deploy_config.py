import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_games_path_can_live_outside_release(monkeypatch, tmp_path):
    path = tmp_path / "shared" / "games.json"
    monkeypatch.setenv("GAMES_FILE", str(path))
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    module = runpy.run_path(str(ROOT / "basketball_poll_bot.py"))
    assert module["GAMES_STORE"].path == path
    module["GAMES_STORE"].save([])
    assert path.read_text(encoding="utf-8").strip() == "[]"
    assert runpy.run_path(str(ROOT / "basketball_poll_bot.py"))["GAMES_STORE"].load() == []


def test_games_path_keeps_legacy_default(monkeypatch):
    monkeypatch.delenv("GAMES_FILE", raising=False)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    assert runpy.run_path(str(ROOT / "basketball_poll_bot.py"))["GAMES_STORE"].path == ROOT / "games.json"
