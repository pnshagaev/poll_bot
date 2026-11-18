import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bot_monitor", ROOT / "monitor/notify.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


@pytest.fixture
def configured(monkeypatch, tmp_path):
    path = tmp_path / "monitor.env"
    path.write_text("POLL_BOT_TOKEN=synthetic-bot-token\nMONITOR_BOT_TOKEN=synthetic-monitor-token\nOWNER_USER_ID=42\nTG_CHAT_IDS='[\"-100\"]'\n")
    monkeypatch.setattr(monitor, "ENVIRONMENT_FILE", path)
    monkeypatch.setattr(monitor, "service_status", lambda: {"ActiveState": "active"})
    sent = []
    monkeypatch.setattr(monitor, "send_message", lambda *args: sent.append(args))
    return sent


def test_healthy_service_does_not_send_alerts(configured):
    assert monitor.main([]) == 0
    assert configured == []


def test_test_alert_demonstrates_exception_and_prefers_monitor_token(configured, capsys):
    assert monitor.main(["--test"]) == 0
    token, owner, message = configured[0]
    assert token == "synthetic-monitor-token"
    assert owner == 42
    assert "RuntimeError: Тестовое исключение" in message
    assert "искусственная ошибка" in message
    assert "synthetic-monitor-token" not in capsys.readouterr().out


@pytest.mark.parametrize("mode", [[], ["--failure"]])
def test_failure_alert_includes_status_and_sanitized_exception(configured, monkeypatch, mode):
    monkeypatch.setattr(monitor, "service_status", lambda: {
        "ActiveState": "failed", "SubState": "failed", "Result": "exit-code", "ExecMainStatus": "1",
        "InvocationID": "a" * 32,
    })
    log = 'Traceback (most recent call last):\nValueError: Invalid game; token=synthetic-bot-token\n'
    monkeypatch.setattr(monitor.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=log))
    assert monitor.main(mode) == 0
    message = configured[0][2]
    assert "failed/failed" in message and "exit-code" in message
    assert "код завершения: 1" in message
    assert "ValueError: Invalid game" in message
    assert "synthetic-bot-token" not in message
    assert "Traceback" not in message


def test_notification_error_returns_failure_without_leaking_token(configured, monkeypatch, capsys):
    def fail(*args):
        raise URLError("https://api.telegram.org/botsynthetic-monitor-token/sendMessage")

    monkeypatch.setattr(monitor, "send_message", fail)
    assert monitor.main(["--test"]) == 1
    assert "synthetic-monitor-token" not in capsys.readouterr().err


def test_send_uses_json_body_and_checks_telegram_confirmation(monkeypatch):
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr(monitor, "urlopen", open_request)
    monitor.send_message("synthetic-token", 42, "RuntimeError: example")
    assert json.loads(requests[0].data) == {"chat_id": 42, "text": "RuntimeError: example"}
    monkeypatch.setattr(monitor, "urlopen", lambda *a, **k: io.BytesIO(b'{"ok":false}'))
    with pytest.raises(RuntimeError, match="Telegram"):
        monitor.send_message("synthetic-token", 42, "test")
