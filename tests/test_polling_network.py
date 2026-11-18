import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Bot
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import Application
from telegram.request import HTTPXRequest

import basketball_poll_bot as bot_module
from basketball_poll_bot import (
    PollingNetworkState,
    PollingRequest,
    check_polling_health,
    error_handler,
)


@pytest.fixture
def health(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(bot_module, "monotonic", lambda: clock.now)
    state = PollingNetworkState()
    context = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock()),
        application=SimpleNamespace(bot_data={"owner_user_id": 42, "polling_network_state": state}),
        job=None,
    )
    return clock, state, context


def check(context):
    asyncio.run(check_polling_health(context))


def test_short_separate_outages_do_not_accumulate(health):
    clock, state, context = health
    state.record_failure(NetworkError("connection failed"))
    clock.now = 3599
    check(context)
    state.record_success()
    clock.now = 4000
    state.record_failure(NetworkError("another failure"))
    clock.now = 4200
    check(context)
    state.record_success()
    check(context)
    context.bot.send_message.assert_not_awaited()
    assert state.current is None
    assert not state.pending_recoveries


def test_hour_outage_sends_warning_once_then_recovery_once(health):
    clock, state, context = health
    state.record_failure(NetworkError("connection failed"))
    clock.now = 1800
    state.record_failure(NetworkError("still failing"))
    clock.now = 3600
    check(context)
    check(context)
    context.bot.send_message.assert_awaited_once()
    warning = context.bot.send_message.call_args.kwargs
    assert warning["chat_id"] == 42
    assert "1 ч 0 мин" in warning["text"]
    assert "still failing" in warning["text"]
    clock.now = 4320
    state.record_success()
    clock.now = 4500
    check(context)
    check(context)
    assert context.bot.send_message.await_count == 2
    assert "восстановлена" in context.bot.send_message.call_args.kwargs["text"]
    assert "1 ч 12 мин" in context.bot.send_message.call_args.kwargs["text"]
    assert not state.pending_recoveries


def test_failed_warning_is_retried_without_marking_it_delivered(health):
    clock, state, context = health
    state.record_failure(NetworkError("offline"))
    clock.now = 3600
    context.bot.send_message.side_effect = NetworkError("cannot send")
    check(context)
    assert not state.current.warning_sent
    context.bot.send_message.side_effect = None
    clock.now = 3660
    check(context)
    check(context)
    assert context.bot.send_message.await_count == 2
    assert state.current.warning_sent


def test_failed_warning_followed_by_recovery_sends_only_summary(health):
    clock, state, context = health
    state.record_failure(NetworkError("offline"))
    clock.now = 3600
    context.bot.send_message.side_effect = TimedOut()
    check(context)
    clock.now = 4320
    state.record_success()
    context.bot.send_message.side_effect = None
    check(context)
    check(context)
    assert context.bot.send_message.await_count == 2
    assert "восстановлена" in context.bot.send_message.call_args.kwargs["text"]


def test_recovery_between_checks_still_reports_a_long_outage(health):
    clock, state, context = health
    state.record_failure(NetworkError("offline"))
    clock.now = 3601
    state.record_success()
    check(context)
    context.bot.send_message.assert_awaited_once()
    assert "восстановлена" in context.bot.send_message.call_args.kwargs["text"]


def test_pending_recovery_survives_failed_send_and_a_new_outage(health):
    clock, state, context = health
    state.record_failure(NetworkError("first outage"))
    clock.now = 4320
    state.record_success()
    context.bot.send_message.side_effect = NetworkError("cannot send recovery")
    check(context)
    assert len(state.pending_recoveries) == 1
    clock.now = 4400
    state.record_failure(NetworkError("second outage"))
    context.bot.send_message.side_effect = None
    check(context)
    assert "1 ч 12 мин" in context.bot.send_message.call_args.kwargs["text"]
    assert not state.pending_recoveries
    assert state.current.started_at == 4400


def test_recovery_during_warning_delivery_is_not_lost(health):
    clock, state, context = health
    state.record_failure(NetworkError("offline"))
    clock.now = 3600

    async def send_and_recover(**kwargs):
        clock.now = 3660
        state.record_success()

    context.bot.send_message.side_effect = send_and_recover
    check(context)
    context.bot.send_message.side_effect = None
    check(context)
    assert context.bot.send_message.await_count == 2
    assert "восстановлена" in context.bot.send_message.call_args.kwargs["text"]


@pytest.mark.parametrize("error", [NetworkError("connection failed"), TimedOut()])
def test_only_observed_polling_errors_skip_immediate_notifications(health, error):
    _, state, context = health
    state.record_failure(error)
    context.error = error
    asyncio.run(error_handler(None, context))
    context.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("is_job", [False, True])
def test_unrelated_network_errors_are_reported_immediately(health, is_job):
    _, _, context = health
    context.error = NetworkError("handler or job failed")
    if is_job:
        context.job = SimpleNamespace(chat_id=-100)
    asyncio.run(error_handler(None, context))
    assert context.bot.send_message.await_count == (2 if is_job else 1)


def test_request_tracks_failures_and_empty_successful_updates(health, monkeypatch):
    clock, state, context = health
    transport = AsyncMock(side_effect=[NetworkError("TLS failed"), (200, b'{"ok":true,"result":[]}')])
    monkeypatch.setattr(HTTPXRequest, "do_request", transport)

    async def scenario():
        request = PollingRequest(state)
        telegram_bot = Bot("123:test", get_updates_request=request)
        with pytest.raises(NetworkError) as raised:
            await telegram_bot.get_updates()
        context.error = raised.value
        await error_handler(None, context)
        assert state.current is not None
        clock.now = 10
        assert await telegram_bot.get_updates() == ()
        assert state.current is None
        await request.shutdown()

    asyncio.run(scenario())
    context.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("status", [400, 401, 403, 409, 429])
def test_api_errors_do_not_count_as_network_outage(health, monkeypatch, status):
    _, state, _ = health
    monkeypatch.setattr(HTTPXRequest, "do_request", AsyncMock(return_value=(status, b'{}')))

    async def scenario():
        request = PollingRequest(state)
        assert await request.do_request("https://example.test/getUpdates", "POST") == (status, b'{}')
        await request.shutdown()

    asyncio.run(scenario())
    assert state.current is None


def test_server_failure_is_tracked(health, monkeypatch):
    _, state, _ = health
    monkeypatch.setattr(HTTPXRequest, "do_request", AsyncMock(return_value=(502, b'Bad Gateway')))

    async def scenario():
        request = PollingRequest(state)
        with pytest.raises(NetworkError, match="HTTP 502"):
            await request.do_request("https://example.test/getUpdates", "POST")
        await request.shutdown()

    asyncio.run(scenario())
    assert state.current is not None


def test_bad_request_exception_is_not_marked_for_delayed_reporting(health, monkeypatch):
    _, state, _ = health
    monkeypatch.setattr(HTTPXRequest, "do_request", AsyncMock(side_effect=BadRequest("invalid request")))

    async def scenario():
        request = PollingRequest(state)
        with pytest.raises(BadRequest):
            await request.do_request("https://example.test/getUpdates", "POST")
        await request.shutdown()

    asyncio.run(scenario())
    assert state.current is None


def test_other_api_requests_do_not_reset_polling_outage(health, monkeypatch):
    _, state, _ = health
    state.record_failure(NetworkError("polling offline"))
    outage = state.current
    monkeypatch.setattr(HTTPXRequest, "do_request", AsyncMock(return_value=(200, b'{"ok":true}')))

    async def scenario():
        request = PollingRequest(state)
        await request.do_request("https://example.test/getMe", "POST")
        await request.shutdown()

    asyncio.run(scenario())
    assert state.current is outage


def test_main_connects_polling_request_and_five_minute_health_job(monkeypatch, tmp_path):
    applications = []
    monkeypatch.setattr(bot_module, "POLL_BOT_TOKEN", "123:test")
    monkeypatch.setattr(bot_module, "load_chat_ids", lambda: [10])
    monkeypatch.setattr(bot_module, "load_owner_user_id", lambda: 42)
    monkeypatch.setattr(bot_module, "GAMES_STORE", bot_module.GamesStore(tmp_path / "games.json"))
    monkeypatch.setattr(Application, "run_polling", lambda app: applications.append(app))
    bot_module.main()
    app, = applications
    state = app.bot_data["polling_network_state"]
    assert app.bot._request[0].network_state is state
    job, = app.job_queue.get_jobs_by_name("polling-network-health")
    assert job.callback is check_polling_health
    assert job.job.trigger.interval.total_seconds() == 300
