#!/usr/bin/env bash
# Installed by an administrator; executed as deploy, never as root.
set -Eeuo pipefail
umask 022
base=/opt/basketball-bot
service=basketball.bot.service
activated=false
committed=false
old=''
candidate=''
upload=''
staging=''

fail() { echo "ERROR: $*" >&2; exit 1; }
valid_release() { [[ "$1" =~ ^/opt/basketball-bot/releases/[0-9a-f]{40}$ && -f "$1/.ready" ]]; }
switch_to() {
    ln -s -- "$1" "$base/.current.$$"
    mv -Tf -- "$base/.current.$$" "$base/current"
}
health() {
    local pid restarts current_pid current_restarts
    pid="$(systemctl show "$service" -p MainPID --value)" || return 1
    restarts="$(systemctl show "$service" -p NRestarts --value)" || return 1
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    for ((i=0; i<15; i++)); do
        sleep 2
        systemctl is-active --quiet "$service" || return 1
        current_pid="$(systemctl show "$service" -p MainPID --value)" || return 1
        current_restarts="$(systemctl show "$service" -p NRestarts --value)" || return 1
        [[ "$current_pid" == "$pid" && "$current_restarts" == "$restarts" ]] || return 1
    done
}
restart() {
    # reset-failed does not load a never-started unit and fails on first install.
    if systemctl is-failed --quiet "$service"; then
        sudo -n /usr/bin/systemctl reset-failed "$service" || return 1
    fi
    sudo -n /usr/bin/systemctl restart "$service"
}
finish() {
    local result=$?
    trap - EXIT
    set +e
    if [[ "$activated" == true && "$committed" != true ]]; then
        echo 'Activation failed. Restoring previous release.' >&2
        if [[ -n "$old" ]]; then
            if switch_to "$old" && restart && health; then
                echo 'Rollback healthy. Deployment still failed.' >&2
            else
                echo 'CRITICAL: rollback failed; inspect the target journal locally.' >&2
            fi
        else
            sudo -n /usr/bin/systemctl stop "$service"
            rm -f -- "$base/current"
            echo 'No previous release: stopped failed first installation.' >&2
        fi
        result=1
    fi
    [[ -z "$staging" ]] || rm -rf -- "$staging"
    [[ -z "$upload" ]] || rm -f -- "$upload"
    rm -f -- "$base/.current.$$" "$base/.previous.$$"
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

[[ "$(id -un)" == deploy ]] || fail 'Run as deploy'
cd "$base"
exec 9>"$base/deploy.lock"
flock -w 60 9 || fail 'Another deployment holds the target lock'
if [[ -e "$base/current" || -L "$base/current" ]]; then
    old="$(readlink -f "$base/current")"
    valid_release "$old" || fail 'Invalid current release'
fi

if [[ "${1:-}" == --rollback && $# == 1 ]]; then
    [[ -n "$old" && -L "$base/previous" ]] || fail 'No previous release'
    candidate="$(readlink -f "$base/previous")"
    valid_release "$candidate" || fail 'Invalid previous release'
else
    [[ $# == 3 ]] || fail 'Expected SHA, upload filename and SHA256'
    sha="$1"
    [[ "$sha" =~ ^[0-9a-f]{40}$ && "$2" =~ ^$sha-[0-9]+-[0-9]+\.tar\.gz$ && "$3" =~ ^[0-9a-f]{64}$ ]] || fail 'Invalid arguments'
    upload="$base/incoming/$2"
    [[ -f "$upload" && ! -L "$upload" ]] || fail 'Upload missing'
    printf '%s  %s\n' "$3" "$upload" | sha256sum --check --status || fail 'Artifact checksum mismatch'
    staging="$(mktemp -d "$base/releases/.stage.XXXXXXXX")"
    # Reject path traversal, links and unexpected files before extraction.
    python3 - "$upload" <<'PY'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1], "r:gz") as archive:
    required = {"basketball_poll_bot.py", "game_store.py", "commands.py", "requirements.txt", "constraints.txt", "ancestors.txt"}
    found = set()
    for member in archive:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
            sys.exit("Unsafe archive member")
        name = str(path)
        if member.isdir() and name in {".", "wheelhouse"}:
            continue
        if not member.isfile() or not (name in required or (len(path.parts) == 2 and path.parts[0] == "wheelhouse" and name.endswith("-none-any.whl"))):
            sys.exit("Unexpected archive member")
        if name in found:
            sys.exit("Duplicate archive member")
        found.add(name)
    if not required <= found:
        sys.exit("Incomplete release")
PY
    tar -xzf "$upload" -C "$staging" --no-same-owner --no-same-permissions
    [[ "$(head -n 1 "$staging/ancestors.txt")" == "$sha" ]] || fail 'Artifact SHA mismatch'
    if [[ -n "$old" && "${old##*/}" != "$sha" ]]; then
        grep -Fxq -- "${old##*/}" "$staging/ancestors.txt" || fail 'Stale or divergent release: current SHA is not an ancestor'
    fi
    candidate="$base/releases/$sha"
    if [[ "$candidate" == "$old" ]]; then
        health || fail 'Already active release is unhealthy; inspect it before retrying'
        echo "Already deployed and healthy: $sha"
        exit 0
    fi
    if [[ -e "$candidate" ]]; then
        valid_release "$candidate" || fail 'Incomplete release directory; investigate before retrying'
        [[ "$(cat "$candidate/.artifact-sha256")" == "$3" ]] || fail 'SHA release already exists with different dependencies/artifact; use a new commit'
    else
        # Create the venv at its final path: its shebangs contain absolute paths.
        chmod 0755 "$staging"
        mv -- "$staging" "$candidate"
        staging="$candidate"
        python3 -c 'import sys; assert sys.version_info[:2] == (3, 13), "Target requires Python 3.13"'
        python3 -m venv "$candidate/venv"
        "$candidate/venv/bin/pip" install --no-index --find-links "$candidate/wheelhouse" \
            -r "$candidate/requirements.txt" -c "$candidate/constraints.txt"
        (cd "$candidate" && PYTHON_DOTENV_DISABLED=1 "$candidate/venv/bin/python" -c 'import basketball_poll_bot')
        printf '%s\n' "$3" > "$candidate/.artifact-sha256"
        touch "$candidate/.ready"
        staging=''
    fi
fi

echo "Activating ${candidate##*/}"
activated=true
switch_to "$candidate"
restart
health || fail 'New process stopped or restarted during the 30-second observation'
if [[ -n "$old" ]]; then
    ln -s -- "$old" "$base/.previous.$$"
    mv -Tf -- "$base/.previous.$$" "$base/previous"
fi
committed=true
echo "Healthy release: ${candidate##*/}"

# Retain current, previous and newest other releases, at most five total.
previous=''
if [[ -L "$base/previous" ]]; then
    previous="$(readlink -f "$base/previous")"
fi
kept=0
[[ -z "$candidate" ]] || ((kept+=1))
[[ -z "$previous" || "$previous" == "$candidate" ]] || ((kept+=1))
while IFS= read -r entry; do
    path="$base/releases/${entry#* }"
    valid_release "$path" || continue
    [[ "$path" != "$candidate" && "$path" != "$previous" ]] || continue
    if ((kept < 5)); then
        ((kept+=1))
    else
        rm -rf -- "$path"
    fi
done < <(find "$base/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' | sort -rn)
