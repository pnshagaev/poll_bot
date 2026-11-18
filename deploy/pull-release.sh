#!/usr/bin/env bash
# Manually invoked on VM 110. Never scheduled and never reads the production env.
set -Eeuo pipefail
umask 022
if [[ "$(id -u)" == 0 ]]; then
    exec /usr/sbin/runuser -u deploy -- "$0" "$@"
fi
[[ "$(id -un)" == deploy ]] || { echo 'Run as root or deploy' >&2; exit 1; }
mode="${1:---apply}"
[[ $# -le 1 && ( "$mode" == --apply || "$mode" == --check ) ]] || {
    echo 'Usage: basketball-bot-pull [--check]' >&2; exit 2;
}
base=/opt/basketball-bot
repo=https://github.com/pnshagaev/poll_bot.git
cache="$base/source.git"
work=''
archive=''
cleanup() {
    local result=$?
    trap - EXIT
    [[ -z "$work" ]] || rm -rf -- "$work"
    [[ -z "$archive" ]] || rm -f -- "$archive"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
export GIT_TERMINAL_PROMPT=0
exec 8>"$base/pull.lock"
flock -n 8 || { echo 'Another manual pull is running' >&2; exit 1; }

echo '[1/5] Fetching main from GitHub'
[[ -d "$cache" ]] || git init --bare -q "$cache"
timeout 120 git --git-dir="$cache" fetch --quiet --no-tags "$repo" refs/heads/main
sha="$(git --git-dir="$cache" rev-parse FETCH_HEAD)"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]]
echo "Available commit: $sha"
current="$(readlink -f "$base/current" 2>/dev/null || true)"
if [[ "$mode" == --check ]]; then
    echo "Installed commit: $([[ -f "$current/.ready" ]] && basename "$current" || echo none)"
    echo 'Check finished. No release installed or service restarted.'
    exit 0
fi
if [[ "$current" == "$base/releases/$sha" && -f "$current/.ready" ]]; then
    systemctl is-active --quiet basketball.bot.service || { echo 'Installed release is not active; inspect its journal' >&2; exit 1; }
    echo 'This commit is already installed and active.'
    exit 0
fi

work="$(mktemp -d "$base/.pull.XXXXXXXX")"
mkdir "$work/source" "$work/release" "$work/release/wheelhouse"
# Explicit allowlist: the tracked .env and games.json never enter the work tree.
git --git-dir="$cache" archive "$sha" basketball_poll_bot.py game_store.py commands.py \
    requirements.txt pytest.ini tests deploy | tar -x -C "$work/source"
echo '[2/5] Installing test dependencies in an isolated venv'
python3 -c 'import sys; assert sys.version_info[:2] == (3, 13), "Python 3.13 required"'
python3 -m venv "$work/test-venv"
"$work/test-venv/bin/pip" install --disable-pip-version-check --only-binary=:all: \
    --timeout 20 --retries 2 -r "$work/source/requirements.txt"
echo '[3/5] Testing the candidate (current bot keeps running)'
(
    cd "$work/source"
    export PYTHON_DOTENV_DISABLED=1
    "$work/test-venv/bin/python" -B -m pytest
    GAMES_FILE=/var/lib/basketball-bot/games.json "$work/test-venv/bin/python" -c \
        'import basketball_poll_bot as bot; from pathlib import Path; assert bot.GAMES_STORE.path == Path("/var/lib/basketball-bot/games.json"), "Publish external games path support to main first"'
)
echo '[4/5] Packing the tested dependencies and source'
"$work/test-venv/bin/pip" freeze > "$work/release/constraints.txt"
"$work/test-venv/bin/pip" download --disable-pip-version-check --only-binary=:all: \
    --timeout 20 --retries 2 -r "$work/source/requirements.txt" \
    -c "$work/release/constraints.txt" -d "$work/release/wheelhouse"
for wheel in "$work/release/wheelhouse/"*.whl; do
    [[ "$wheel" == *-none-any.whl ]] || { echo 'Unexpected platform-specific dependency' >&2; exit 1; }
done
cp "$work/source/"{basketball_poll_bot.py,game_store.py,commands.py,requirements.txt} "$work/release/"
git --git-dir="$cache" rev-list "$sha" > "$work/release/ancestors.txt"
latest="$(timeout 30 git ls-remote --exit-code "$repo" refs/heads/main)"
[[ "${latest%%[[:space:]]*}" == "$sha" ]] || { echo 'main changed during preparation; run the command again' >&2; exit 1; }
archive="$base/incoming/$sha-$(date +%s)-$$.tar.gz"
tar -czf "$archive" -C "$work/release" .
digest="$(sha256sum "$archive")"
echo '[5/5] Activating release and observing process for 30 seconds'
/usr/local/lib/basketball-bot/deploy-release "$sha" "${archive##*/}" "${digest%% *}"
