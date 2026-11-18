"""Exercise deployment failure paths with fake systemd/sudo, never a real service."""
import hashlib
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OLD, NEW, OTHER = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture
def remote(tmp_path):
    if os.name != "posix":
        pytest.skip("Linux release/filesystem integration harness (runs in CI)")
    base = tmp_path / "bot"
    (base / "releases").mkdir(parents=True)
    (base / "incoming").mkdir()
    script = (ROOT / "deploy/deploy-release.sh").read_text().replace("/opt/basketball-bot", str(base))
    script = script.replace("flock -w 60", "flock -w 0")
    (tmp_path / "deploy.sh").write_text(script)
    # Only process control is mocked. Archive verification, filesystem switching,
    # flock, SHA/ancestry checks and failure/EXIT traps use the real script/tools.
    bootstrap = r'''
id() { printf 'deploy\n'; }
sleep() { :; }
sudo() {
    shift 2
    printf '%s\n' "$*" >> "$HARNESS_LOG"
    return 0
}
systemctl() {
    if [[ "$1" == is-failed ]]; then
        [[ "$HARNESS_INITIAL_FAILED" == true ]]
        return
    fi
    local current
    current="$(readlink -f "$HARNESS_BASE/current")"
    if [[ "${current##*/}" == "$HARNESS_BAD_SHA" ]]; then
        if [[ "$*" == *MainPID* ]]; then printf '0\n'; return 0; fi
        return 1
    fi
    case "$*" in
        *MainPID*) printf '1234\n' ;;
        *NRestarts*) printf '0\n' ;;
        is-active*) return 0 ;;
        *) return 1 ;;
    esac
}
source "$HARNESS_SCRIPT" "$@"
'''
    env = dict(os.environ, HARNESS_BASE=str(base), HARNESS_LOG=str(tmp_path / "service.log"),
               HARNESS_SCRIPT=str(tmp_path / "deploy.sh"), HARNESS_BAD_SHA="")

    def ready(sha, digest="0" * 64):
        release = base / "releases" / sha
        release.mkdir(exist_ok=True)
        (release / ".ready").touch()
        (release / ".artifact-sha256").write_text(digest + "\n")
        return release

    def archive(sha=NEW, ancestors=None, extra=None):
        upload = base / "incoming" / f"{sha}-1-1.tar.gz"
        files = {name: b"" for name in ["basketball_poll_bot.py", "game_store.py", "commands.py", "error_reporting.py", "requirements.txt", "constraints.txt"]}
        files["ancestors.txt"] = ("\n".join(ancestors or [sha, OLD]) + "\n").encode()
        files.update(extra or {})
        with tarfile.open(upload, "w:gz") as stream:
            for name, data in files.items():
                member = tarfile.TarInfo("./" + name)
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
        digest = hashlib.sha256(upload.read_bytes()).hexdigest()
        return [sha, upload.name, digest]

    def run(*args, bad="", failed=False):
        result = subprocess.run(["bash", "-c", bootstrap, "harness", *args],
                                env={**env, "HARNESS_BAD_SHA": bad, "HARNESS_INITIAL_FAILED": str(failed).lower()},
                                text=True, capture_output=True, timeout=30)
        return result

    return base, ready, archive, run, tmp_path / "service.log"


def test_healthy_release_switches_current_and_keeps_previous(remote):
    base, ready, archive, run, log = remote
    old = ready(OLD)
    (base / "current").symlink_to(old)
    args = archive()
    new = ready(NEW, args[2])
    result = run(*args)
    assert result.returncode == 0, result.stderr
    assert (base / "current").resolve() == new
    assert (base / "previous").resolve() == old
    assert "restart basketball.bot.service" in log.read_text()


def test_new_release_installs_offline_in_readable_final_directory(remote):
    base, ready, archive, run, log = remote
    result = run(*archive())
    assert result.returncode == 0, result.stderr
    release = (base / "current").resolve()
    assert (release / ".ready").is_file()
    assert (release / "venv/bin/python").is_file()
    assert stat.S_IMODE(release.stat().st_mode) == 0o755
    assert "reset-failed" not in log.read_text()


def test_previously_failed_service_is_reset_before_restart(remote):
    base, ready, archive, run, log = remote
    (base / "current").symlink_to(ready(OLD))
    args = archive()
    ready(NEW, args[2])
    result = run(*args, failed=True)
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert calls.index("reset-failed basketball.bot.service") < calls.index("restart basketball.bot.service")


def test_deploy_lock_prevents_concurrent_activation(remote):
    import fcntl

    base, ready, archive, run, log = remote
    with (base / "deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run(*archive())
    assert result.returncode != 0
    assert "Another deployment" in result.stderr
    assert not log.exists()


def test_unhealthy_release_rolls_back_and_still_fails(remote):
    base, ready, archive, run, log = remote
    old = ready(OLD)
    (base / "current").symlink_to(old)
    args = archive()
    ready(NEW, args[2])
    result = run(*args, bad=NEW)
    assert result.returncode != 0
    assert "Rollback healthy" in result.stderr
    assert (base / "current").resolve() == old
    assert log.read_text().count("restart basketball.bot.service") == 2


def test_first_failed_release_is_stopped(remote):
    base, ready, archive, run, log = remote
    args = archive()
    ready(NEW, args[2])
    result = run(*args, bad=NEW)
    assert result.returncode != 0
    assert not (base / "current").exists()
    assert "stop basketball.bot.service" in log.read_text()


def test_stale_release_does_not_restart_current(remote):
    base, ready, archive, run, log = remote
    old = ready(OTHER)
    (base / "current").symlink_to(old)
    result = run(*archive())
    assert result.returncode != 0
    assert "not an ancestor" in result.stderr
    assert (base / "current").resolve() == old
    assert not log.exists()


@pytest.mark.parametrize("extra", [{"../outside": b"bad"}, {".env": b"bad"}, {"games.json": b"[]"}])
def test_archive_paths_and_configuration_are_rejected(remote, extra):
    base, ready, archive, run, log = remote
    (base / "current").symlink_to(ready(OLD))
    result = run(*archive(extra=extra))
    assert result.returncode != 0
    assert not log.exists()
    assert not (base / "releases" / NEW).exists()


def test_checksum_mismatch_does_not_restart_current(remote):
    base, ready, archive, run, log = remote
    (base / "current").symlink_to(ready(OLD))
    args = archive()
    args[2] = "f" * 64
    result = run(*args)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not log.exists()


def test_retry_active_sha_does_not_restart_bot(remote):
    base, ready, archive, run, log = remote
    args = archive(ancestors=[NEW, OLD])
    (base / "current").symlink_to(ready(NEW, args[2]))
    result = run(*args)
    assert result.returncode == 0, result.stderr
    assert "Already deployed" in result.stdout
    assert not log.exists()


def test_manual_rollback_preserves_forward_release(remote):
    base, ready, archive, run, log = remote
    (base / "current").symlink_to(ready(NEW))
    (base / "previous").symlink_to(ready(OLD))
    result = run("--rollback")
    assert result.returncode == 0, result.stderr
    assert (base / "current").resolve().name == OLD
    assert (base / "previous").resolve().name == NEW


def test_retention_keeps_current_previous_and_five_total(remote):
    base, ready, archive, run, log = remote
    for number in range(8):
        ready(f"{number:040x}")
    (base / "current").symlink_to(ready(OLD))
    args = archive()
    ready(NEW, args[2])
    result = run(*args)
    assert result.returncode == 0, result.stderr
    assert (base / "current").resolve().is_dir()
    assert (base / "previous").resolve().is_dir()
    assert len(list((base / "releases").iterdir())) == 5
