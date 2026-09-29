# Agent context: Basketball Telegram Poll Bot

## Purpose

This repository contains a Telegram bot that creates and pins basketball availability polls.
It has two kinds of polls:

- recurring polls for Monday training and Wednesday game training;
- one-off game polls managed interactively by the bot owner and stored in `games.json`.

The primary runtime entry point is `basketball_poll_bot.py`.

## Repository layout

- `basketball_poll_bot.py` — Telegram handlers, JobQueue scheduling, conversations, and error handling.
- `game_store.py` — validated, atomic JSON storage for one-off games.
- `games.json` — persisted one-off games. It may be absent; the bot starts with an empty game list and creates it after the first `/addgame`.
- `commands.py` — single command registry. It drives `/help` and the native Telegram command menu.
- `requirements.txt` — runtime and test dependencies.
- `pytest.ini` — makes repository-root modules importable during `pytest` and sets `tests/` as the test directory.
- `tests/` — pytest test suite.
- `basketball.bot.service` — production systemd service template.
- `monitor/` — systemd monitoring units and Telegram alert script.
- `README.md` — operator-facing deployment and monitoring notes.

## Runtime configuration and secrets

Production uses `/etc/basketball-bot.env`, not the repository `.env` file. The tracked `.env`
is not a safe place for a real token and must not be used by the production systemd service.

Required production variables:

```env
POLL_BOT_TOKEN=...
TG_CHAT_IDS='["-100...", "-100..."]'
OWNER_USER_ID=41879174
```

Optional monitoring variable:

```env
MONITOR_BOT_TOKEN=...
```

`MONITOR_BOT_TOKEN` is preferred over `POLL_BOT_TOKEN` for alerts because it can belong to a
separate monitoring bot. Never commit real tokens or overwrite `/etc/basketball-bot.env` from
Git.

## Deployment

The expected production checkout is `/home/ubuntu/basketball_bot`, executed by the `ubuntu`
user. The service installed at `/etc/systemd/system/basketball.bot.service` uses:

```ini
WorkingDirectory=/home/ubuntu/basketball_bot
EnvironmentFile=/etc/basketball-bot.env
ExecStart=/home/ubuntu/basketball_bot/venv/bin/python -u /home/ubuntu/basketball_bot/basketball_poll_bot.py
```

After changing a unit file, run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart basketball.bot
```

Normal server update workflow:

```bash
cd /home/ubuntu/basketball_bot
git pull --ff-only
./venv/bin/pip install -r requirements.txt
./venv/bin/pytest
sudo systemctl restart basketball.bot
```

Do not use `git reset --hard` to work around local configuration changes. Keep production
configuration outside the checkout instead.

## Scheduling decisions

All schedule times are explicit UTC.

- The recurring training job deliberately uses `days=(0,)` at 09:00 UTC. In
  `python-telegram-bot` v20+, day `0` is Sunday, so this sends the poll one day before Monday
  training.
- The recurring game-training job deliberately uses `days=(2,)` at 09:00 UTC. Day `2` is
  Tuesday, so it sends the poll one day before Wednesday game training.

Do not change those day indices to Monday/Wednesday unless the business rule changes.

One-off games have `match_at`, `poll_at`, `status`, and per-chat delivery tracking.

- `scheduled` — future poll waiting to be sent;
- `sent` — delivered to every configured target chat;
- `expired` — poll time passed without a complete delivery.

On startup the bot marks overdue scheduled games as `expired` and schedules only future
scheduled games. A game is sent once per chat; `sent_chat_ids` prevents duplicate polls after a
restart or a partial delivery.

## Commands and access rules

The complete command list belongs only in `commands.py`. When adding, removing, or renaming a
command, update that registry first. `post_init()` publishes it through `set_my_commands()` and
`/help` uses the same registry.

- `/start`, `/help`, `/ping` — public, stateless commands.
- `/schedule` — public technical list of currently scheduled JobQueue jobs.
- `/games` — public list of games. The owner additionally sees inline buttons for management.
- `/addgame`, `/deletegame`, `/test` — only `OWNER_USER_ID` may execute them.
- `/cancel` — ends an active add/delete conversation.

Use the numeric owner ID, not a Telegram username. Usernames are changeable.

## Interactive game management

`/addgame` is a `ConversationHandler` flow:

1. Tour name.
2. Match date/time in UTC.
3. Suggested poll date: two days before the match at 08:00 UTC, with an option to override it.
4. Venue selection or custom venue.
5. First team selection.
6. Second team selection.
7. Preview, confirmation, restart, or cancellation.

The first and second teams are intentionally entered separately because their order affects the
team's jersey color. Each position offers `Авито` and `Другая команда`; validation requires
exactly one `Авито` team and rejects identical teams. New JSON games persist `first_team` and
`second_team` as well as the rendered Telegram question.

`/deletegame` shows every JSON record, including `sent` and `expired` history, with pagination.
Deleting a future `scheduled` game cancels its JobQueue jobs. Deleting a past/sent game changes
only JSON and must not query or alter JobQueue.

When adding a new ConversationHandler state, update the state tuple and the number passed to
`range(...)` together. A past bug declared 13 state variables but used `range(12)`, causing an
import-time `ValueError` and a systemd restart loop. Prefer an enum/automatic state allocation
in a future refactor.

## Poll and error behavior

Poll options are always `Буду`, `Не буду`, `50/50`, and polls are non-anonymous.

The bot tries to pin every sent poll. If sending or pinning fails, it logs the exception and
sends a concise error message to the affected chat. Unexpected handler/job failures are logged,
reported to the affected chat when possible, and reported to the owner.

Do not expose tokens, tracebacks, or private configuration in Telegram messages.

## Tests

Run on the server:

```bash
./venv/bin/pytest
```

The local Codex Windows environment used during prior work did not have Python or pytest in
`PATH`; that is an environment limitation, not a reason to remove tests. The server test suite
has been run successfully after adding `pytest.ini`.

Tests cover JSON validation/storage, job cancellation rules, public-vs-owner command behavior,
command-menu registration, and key interactive management transitions. When changing handlers,
add/update focused tests rather than relying only on manual Telegram checks.

## Monitoring

`monitor/check-basketball-bot.sh` reads `/etc/basketball-bot.env` and sends alerts to Telegram
ID `41879174`.

- `basketball-bot-alert.service` is triggered by `OnFailure=` after the main service ultimately
  fails.
- `basketball-bot-healthcheck.timer` runs `basketball-bot-healthcheck.service` hourly, including
  after boot (`Persistent=true`). It alerts if `basketball.bot` is not active.
- `check-basketball-bot.sh --test` sends a real test alert without stopping the bot.

Install monitoring files with the commands in `README.md`. A server-wide outage or loss of
network cannot be reported by a local timer; use an independent external heartbeat monitor as a
second layer if that risk matters.

## Git workflow

The remote is `https://github.com/pnshagaev/poll_bot.git`, branch `main`. The repository uses
the author identity `Pavel Shagaev <pnshagaev@gmail.com>` locally.

Before committing:

```bash
git diff --check
./venv/bin/pytest  # when Python is available
```

Commit only task-related files. Do not commit a production token, a virtual environment,
`pyvenv.cfg`, `__pycache__`, or `.pytest_cache`.
