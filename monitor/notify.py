"""Standalone systemd monitor; uses stdlib even if the application venv is broken."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

# Source checkout and installed monitor each have an adjacent copy of the shared helper.
if not (Path(__file__).parent / "error_reporting.py").exists():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from error_reporting import error_details, journal_error_excerpt, redact_secrets


ENVIRONMENT_FILE = Path("/etc/basketball-bot.env")
SERVICE = "basketball.bot.service"


def read_configuration(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("Некорректная строка env")
        parts = shlex.split(value, comments=True)
        if len(parts) > 1:
            raise ValueError("Значения env с пробелами должны быть в кавычках")
        result[key] = parts[0] if parts else ""
    return result


def service_status() -> dict[str, str]:
    process = subprocess.run(
        ["systemctl", "show", SERVICE, "-p", "ActiveState", "-p", "SubState", "-p", "Result",
         "-p", "ExecMainStatus", "-p", "InvocationID"],
        text=True, capture_output=True, timeout=10,
    )
    if process.returncode:
        raise RuntimeError("Не удалось прочитать состояние systemd-сервиса")
    return dict(line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)


def journal_details(status: dict[str, str], secrets: list[str]) -> str:
    invocation = status.get("InvocationID", "")
    selector = ["_SYSTEMD_INVOCATION_ID=" + invocation] if re.fullmatch(r"[0-9a-f]{32}", invocation) else ["-u", SERVICE]
    try:
        process = subprocess.run(
            ["journalctl", *selector, "-n", "160", "--no-pager", "-o", "cat"],
            text=True, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "Журнал недоступен."
    if process.returncode:
        return "Журнал недоступен."
    return journal_error_excerpt(process.stdout, secrets=secrets)


def build_message(mode: str, status: dict[str, str], *, secrets: list[str]) -> str:
    host = socket.gethostname()
    time = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M МСК")
    if mode == "test":
        return (
            f"✅ Тест мониторинга basketball.bot: {host}, {time}.\n"
            f"Сервис: {status.get('ActiveState', 'unknown')}. Бот не останавливался.\n"
            "Пример подробного уведомления (искусственная ошибка):\n"
            + error_details(RuntimeError("Тестовое исключение для проверки уведомлений"))
        )
    title = "Сбой basketball.bot" if mode == "failure" else "basketball.bot не активен"
    summary = (
        f"⚠️ {title}: {host}, {time}.\n"
        f"Состояние: {status.get('ActiveState', 'unknown')}/{status.get('SubState', 'unknown')}.\n"
        f"Результат: {status.get('Result', 'unknown')}; код завершения: {status.get('ExecMainStatus', 'unknown')}.\n\n"
        + journal_details(status, secrets)
    )
    return redact_secrets(summary, secrets)


def send_message(token: str, chat_id: int, message: str) -> None:
    payload = json.dumps({"chat_id": chat_id, "text": message}, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=15) as response:
        result = json.load(response)
    if result.get("ok") is not True:
        raise RuntimeError("Telegram не подтвердил доставку уведомления")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", action="store_true")
    group.add_argument("--failure", action="store_true")
    args = parser.parse_args(argv)
    secrets: list[str] = []
    try:
        config = read_configuration(ENVIRONMENT_FILE)
        secrets = [value for key, value in config.items() if re.search("TOKEN|PASSWORD|SECRET|PRIVATE_KEY|API_KEY", key, re.I)]
        token = config.get("MONITOR_BOT_TOKEN") or config.get("POLL_BOT_TOKEN")
        if not token:
            raise ValueError("MONITOR_BOT_TOKEN или POLL_BOT_TOKEN не настроен")
        owner = int(config.get("OWNER_USER_ID") or "41879174")
        status = service_status()
        mode = "test" if args.test else "failure" if args.failure else "health"
        if mode == "health" and status.get("ActiveState") == "active":
            return 0
        message = build_message(mode, status, secrets=secrets)
        send_message(token, owner, redact_secrets(message, secrets))
        print("Monitoring alert delivered")
        return 0
    except Exception as error:
        print("Monitoring failed: " + error_details(error, secrets=secrets), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
