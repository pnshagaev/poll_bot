#!/usr/bin/env bash
# One-time guest preparation. Run as root inside the target VM, never on Proxmox.
# Does not start the bot, create its production env, or import production games.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run inside the target VM as root' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info[:2] == (3, 13), "Python 3.13 required"'
[[ $# -le 1 ]] || { echo 'Usage: prepare-target.sh [optional-deploy-public-key]' >&2; exit 1; }
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if systemctl is-active --quiet basketball.bot.service; then
    echo 'Bot is already running: review preparation changes before reinstalling units' >&2
    exit 1
fi
id deploy >/dev/null 2>&1 || useradd --create-home --user-group --shell /bin/bash deploy
id basketball-bot >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/basketball-bot --shell /usr/sbin/nologin basketball-bot
[[ "$(id -Gn deploy)" == deploy ]] || { echo 'deploy has unexpected groups: review existing privileges' >&2; exit 1; }
[[ "$(id -u deploy)" != 0 && "$(id -u basketball-bot)" != 0 ]]
install -d -o deploy -g deploy -m 0755 /opt/basketball-bot /opt/basketball-bot/releases
install -d -o deploy -g deploy -m 0700 /opt/basketball-bot/incoming /home/deploy/.ssh
install -d -o basketball-bot -g basketball-bot -m 0700 /var/lib/basketball-bot
if [[ $# == 1 ]]; then
    key="$(cat "$1")"
    [[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || { echo 'Expected Ed25519 public key' >&2; exit 1; }
    authorized=/home/deploy/.ssh/authorized_keys
    read -r _key_type key_blob _key_comment <<< "$key"
    if ! grep -Fq -- " $key_blob" "$authorized" 2>/dev/null; then
        printf 'restrict %s\n' "$key" >> "$authorized"
    fi
    chown deploy:deploy "$authorized"
    chmod 0600 "$authorized"
fi
install -D -o root -g root -m 0755 "$source_dir/deploy/deploy-release.sh" /usr/local/lib/basketball-bot/deploy-release
install -D -o root -g root -m 0755 "$source_dir/deploy/pull-release.sh" /usr/local/bin/basketball-bot-pull
visudo -cf "$source_dir/deploy/basketball-bot.sudoers"
install -o root -g root -m 0440 "$source_dir/deploy/basketball-bot.sudoers" /etc/sudoers.d/basketball-bot
visudo -c
install -o root -g root -m 0644 "$source_dir/deploy/basketball.bot.service" /etc/systemd/system/basketball.bot.service
install -D -o root -g root -m 0755 "$source_dir/monitor/check-basketball-bot.sh" /usr/local/lib/basketball-bot-monitor/check-basketball-bot.sh
for unit in basketball-bot-alert.service basketball-bot-healthcheck.service basketball-bot-healthcheck.timer; do
    install -o root -g root -m 0644 "$source_dir/monitor/$unit" "/etc/systemd/system/$unit"
done
cat > /etc/ssh/sshd_config.d/60-basketball-deploy.conf <<'SSH'
Match User deploy
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding no
    X11Forwarding no
    PermitTTY no
Match all
SSH
/usr/sbin/sshd -t
systemctl reload ssh
systemctl daemon-reload
echo 'Prepared deploy user, release directories, service and monitoring units. Bot remains stopped.'
