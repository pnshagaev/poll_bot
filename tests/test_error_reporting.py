import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, Message, Update
from telegram.error import BadRequest, Forbidden

from basketball_poll_bot import error_handler, send_poll_and_pin
from error_reporting import REDACTED, error_details, journal_error_excerpt, redact_secrets


def test_exception_type_and_actual_message_are_preserved():
    assert error_details(ValueError("Неверная дата игры")) == "ValueError: Неверная дата игры"


def test_tokens_credentials_and_passwords_are_removed(monkeypatch):
    token = "123456789:" + "a" * 35
    monkeypatch.setenv("POLL_BOT_TOKEN", token)
    monkeypatch.setenv("MONITOR_BOT_TOKEN", "different-monitor-credential")
    text = error_details(RuntimeError(
        f"request https://api.telegram.org/bot{token}/getUpdates; "
        f"encoded={token.replace(':', '%3A')}; "
        'password="private password"; Authorization: Bearer arbitrary-credential; '
        "https://user:database-password@example.com; different-monitor-credential"
    ))
    for secret in [token, "a" * 35, "private password", "arbitrary-credential", "database-password", "different-monitor-credential"]:
        assert secret not in text
    assert REDACTED in text


def test_unknown_telegram_token_and_private_key_are_redacted():
    token = "987654321:" + "z" * 35
    text = redact_secrets(f"{token}\n-----BEGIN PRIVATE KEY-----\nsecret-key-material\n-----END PRIVATE KEY-----")
    assert token not in text
    assert "secret-key-material" not in text


def test_redaction_happens_before_clipping(monkeypatch):
    secret = "long-secret-" + "z" * 300
    monkeypatch.setenv("API_KEY", secret)
    result = error_details(RuntimeError("a" * 1100 + secret + "b" * 1000))
    assert len(result) <= 1200
    assert secret[:40] not in result
    assert result.endswith("…")


def test_broken_exception_string_does_not_break_error_handler():
    class BrokenError(Exception):
        def __str__(self):
            raise ValueError("bad exception repr")

    assert error_details(BrokenError()) == "BrokenError"


def test_journal_contains_error_without_traceback_or_request_logs():
    token = "123456789:" + "k" * 35
    log = f'''HTTP Request: https://api.telegram.org/bot{token}/getUpdates 200 OK
Traceback (most recent call last):
  File "/private/script.py", line 42, in operation
    raise RuntimeError("source code should not be sent")
telegram.error.BadRequest: Chat not found; token={token}
'''
    result = journal_error_excerpt(log)
    assert "BadRequest: Chat not found" in result
    assert token not in result
    assert "Traceback" not in result
    assert 'File "' not in result
    assert "source code should not be sent" not in result
    assert "HTTP Request" not in result


def test_job_error_is_sent_to_affected_chat_and_owner():
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot, error=ValueError("Неверная дата"), job=SimpleNamespace(chat_id=-100),
                              application=SimpleNamespace(bot_data={"owner_user_id": 42}))
    asyncio.run(error_handler(None, context))
    assert {call.kwargs["chat_id"] for call in bot.send_message.call_args_list} == {-100, 42}
    assert all("ValueError: Неверная дата" in call.kwargs["text"] for call in bot.send_message.call_args_list)


def test_private_owner_error_is_sent_once():
    bot = SimpleNamespace(send_message=AsyncMock())
    update = Update(1, message=Message(1, datetime.now(timezone.utc), Chat(42, Chat.PRIVATE)))
    context = SimpleNamespace(bot=bot, error=RuntimeError("test failure"), job=None,
                              application=SimpleNamespace(bot_data={"owner_user_id": 42}))
    asyncio.run(error_handler(update, context))
    bot.send_message.assert_awaited_once()
    assert "RuntimeError: test failure" in bot.send_message.call_args.kwargs["text"]


@pytest.mark.parametrize("pin_error", [False, True])
def test_poll_errors_include_the_actual_telegram_reason(pin_error):
    bot = SimpleNamespace(send_message=AsyncMock(), send_poll=AsyncMock(), pin_chat_message=AsyncMock())
    if pin_error:
        bot.send_poll.return_value = SimpleNamespace(message_id=7)
        bot.pin_chat_message.side_effect = Forbidden("Not enough rights to pin")
    else:
        bot.send_poll.side_effect = BadRequest("Chat not found")
    assert asyncio.run(send_poll_and_pin(bot, -100, "Test question")) is pin_error
    notice = bot.send_message.call_args.kwargs["text"]
    assert ("Forbidden: Not enough rights to pin" if pin_error else "BadRequest: Chat not found") in notice
