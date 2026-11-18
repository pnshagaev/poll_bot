"""Manual pull lifecycle: real local Git, mocked dependency tools and activation."""
import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux pull command harness")


@pytest.fixture
def pull(tmp_path):
    base, repo = tmp_path / "bot", tmp_path / "repository"
    (base / "incoming").mkdir(parents=True)
    (base / "releases").mkdir()
    repo.mkdir()
    for directory in ["tests", "deploy"]:
        (repo / directory).mkdir()
        (repo / directory / "placeholder.txt").write_text("fixture")
    for name in ["basketball_poll_bot.py", "game_store.py", "commands.py", "requirements.txt", "pytest.ini"]:
        (repo / name).write_text("")
    for name in [".env", "games.json"]:
        (repo / name).write_text("SENSITIVE_TEST_MARKER")
    env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
    for args in [["init", "-q", "-b", "main"], ["add", "."], ["commit", "-qm", "Test source"]]:
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "pip").write_text(r'''#!/usr/bin/env bash
set -e
echo "$*" >> "$HARNESS_PIP_LOG"
case "$1" in
    install) [[ "$FAILURE" != deps ]] ;;
    freeze) echo 'example==1.0' ;;
    download)
        while [[ "$1" != -d ]]; do shift; done
        : > "$2/example-1.0-py3-none-any.whl" ;;
    *) exit 44 ;;
esac
''')
    (stubs / "python").write_text(r'''#!/usr/bin/env bash
[[ "$FAILURE" != tests ]]
''')
    activate = tmp_path / "activate"
    activate.write_text(r'''#!/usr/bin/env bash
set -e
[[ "$PWD" == "$HARNESS_BASE" ]]
printf '%s\n' "$@" > "$HARNESS_ACTIVATE_LOG"
cp "$HARNESS_BASE/incoming/$2" "$HARNESS_CAPTURE"
''')
    activate.chmod(0o755)
    text = (ROOT / "deploy/pull-release.sh").read_text()
    text = text.replace("/opt/basketball-bot", str(base)).replace("https://github.com/pnshagaev/poll_bot.git", str(repo))
    text = text.replace("/usr/local/lib/basketball-bot/deploy-release", str(activate))
    script = tmp_path / "pull.sh"
    script.write_text(text)
    bootstrap = r'''
id() { if [[ "$1" == -u ]]; then echo 1002; else echo deploy; fi; }
timeout() { shift; "$@"; }
systemctl() { return 0; }
git() {
    if [[ "$FAILURE" == fetch && "$*" == *' fetch '* ]]; then return 41; fi
    if [[ "$FAILURE" == stale && "$1" == ls-remote ]]; then
        printf '%040d\trefs/heads/main\n' 0
        return 0
    fi
    command git "$@"
}
python3() {
    if [[ "$1" == -c ]]; then return 0; fi
    [[ "$1 $2" == '-m venv' ]] || return 46
    mkdir -p "$3/bin"
    cp "$HARNESS_STUBS/"* "$3/bin/"
    chmod +x "$3/bin/"*
}
source "$HARNESS_SCRIPT" "$@"
'''

    def run(*args, failure=""):
        e = dict(env, FAILURE=failure, HARNESS_BASE=str(base), HARNESS_STUBS=str(stubs),
                 HARNESS_SCRIPT=str(script), HARNESS_PIP_LOG=str(tmp_path / "pip.log"),
                 HARNESS_ACTIVATE_LOG=str(tmp_path / "activation.log"), HARNESS_CAPTURE=str(tmp_path / "received.tar.gz"))
        return subprocess.run(["bash", "-c", bootstrap, "harness", *args], cwd=ROOT, env=e,
                              text=True, capture_output=True, timeout=15)

    return base, sha, run, tmp_path


def test_check_fetches_without_install_or_restart(pull):
    base, sha, run, scratch = pull
    result = run("--check")
    assert result.returncode == 0, result.stderr
    assert sha in result.stdout
    assert "No release installed" in result.stdout
    assert not (scratch / "pip.log").exists()
    assert not (scratch / "activation.log").exists()


@pytest.mark.parametrize("failure", ["fetch", "deps", "tests", "stale"])
def test_failure_before_activation_preserves_current_and_removes_work(pull, failure):
    base, sha, run, scratch = pull
    result = run(failure=failure)
    assert result.returncode != 0
    assert not (scratch / "activation.log").exists()
    assert not list(base.glob(".pull.*"))
    assert not list((base / "incoming").iterdir())


def test_pull_packages_only_tested_runtime_and_calls_activation(pull):
    base, sha, run, scratch = pull
    result = run()
    assert result.returncode == 0, result.stderr
    args = (scratch / "activation.log").read_text().splitlines()
    assert args[0] == sha
    archive = scratch / "received.tar.gz"
    assert args[2] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as stream:
        names = {Path(m.name).as_posix() for m in stream.getmembers()}
        assert ".env" not in names and "games.json" not in names
        assert "constraints.txt" in names
        assert stream.extractfile("./ancestors.txt").read().decode().splitlines()[0] == sha
    assert not list(base.glob(".pull.*"))
    assert not list((base / "incoming").iterdir())


def test_installed_active_commit_is_not_restarted(pull):
    base, sha, run, scratch = pull
    release = base / "releases" / sha
    release.mkdir()
    (release / ".ready").touch()
    (base / "current").symlink_to(release)
    result = run()
    assert result.returncode == 0, result.stderr
    assert "already installed" in result.stdout
    assert not (scratch / "activation.log").exists()
    assert not (scratch / "pip.log").exists()
