#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="basketball.bot"
ENVIRONMENT_FILE="/etc/basketball-bot.env"
ALERT_CHAT_ID="41879174"

log_error() {
    logger --tag basketball-bot-monitor "$1"
    echo "$1" >&2
}

if [[ ! -r "$ENVIRONMENT_FILE" ]]; then
    log_error "Cannot read $ENVIRONMENT_FILE"
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENVIRONMENT_FILE"
set +a

BOT_TOKEN="${MONITOR_BOT_TOKEN:-${POLL_BOT_TOKEN:-}}"
if [[ -z "$BOT_TOKEN" ]]; then
    log_error "POLL_BOT_TOKEN or MONITOR_BOT_TOKEN is not configured"
    exit 1
fi

send_message() {
    local message="$1"
    curl --fail --silent --show-error --max-time 15 \
        --request POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${ALERT_CHAT_ID}" \
        --data-urlencode "text=${message}" >/dev/null
}

host_name="$(hostname -f 2>/dev/null || hostname)"
checked_at="$(date --utc '+%Y-%m-%d %H:%M UTC')"

case "${1:-}" in
    --test)
        send_message "✅ Тест мониторинга basketball.bot: ${host_name}, ${checked_at}. Сервис не останавливался."
        ;;
    --failure)
        send_message "⚠️ basketball.bot остановлен на ${host_name}, ${checked_at}. Проверьте: sudo journalctl -u basketball.bot -n 100 --no-pager"
        ;;
    "")
        if ! systemctl is-active --quiet "$SERVICE_NAME"; then
            current_status="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
            send_message "⚠️ basketball.bot не активен (${current_status}) на ${host_name}, ${checked_at}. Проверьте: sudo systemctl status basketball.bot"
        fi
        ;;
    *)
        log_error "Unknown argument: $1"
        exit 2
        ;;
esac
