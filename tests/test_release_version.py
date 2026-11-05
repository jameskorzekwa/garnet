"""Exercise release recovery against real, isolated Git histories."""

import json
import subprocess

import pytest

from scripts.release_version import (
    BOT_EMAIL,
    BOT_NAME,
    MANIFEST,
    git,
    select_checkout,
    select_version,
)


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-q")
    path = tmp_path / MANIFEST
    path.parent.mkdir(parents=True)

    def commit(version="1.5.0", *, extra=None, author=BOT_NAME, message=None):
        path.write_text(
            json.dumps({"domain": "garnet", "version": version, **(extra or {})})
        )
        git("add", ".")
        git(
            "-c",
            f"user.name={author}",
            "-c",
            f"user.email={BOT_EMAIL}",
            "commit",
            "-qm",
            message or f"Bump version to {version}",
        )
        tip = git("rev-parse", "HEAD")
        git("update-ref", "refs/remotes/origin/main", tip)
        return tip

    trigger = commit()
    return trigger, commit, tmp_path


def test_initial_release_and_patch_collision(history):
    trigger, _, _ = history
    select_checkout(trigger)
    assert select_version() == "1.5.0"
    # An older tag forces a patch bump; a tag on HEAD is an idempotent retry.
    git("tag", "v1.5.0")
    assert select_version() == "1.5.0"
    _, commit, _ = history
    trigger = commit(extra={"name": "Garnet"})
    select_checkout(trigger)
    assert select_version() == "1.5.1"


@pytest.mark.parametrize("tagged", [False, True])
def test_recover_pushed_bump_before_or_after_tag(history, tagged):
    trigger, commit, path = history
    git("tag", "v1.5.0", trigger)
    bump = commit("1.5.1")
    if tagged:
        git("tag", "v1.5.1")
    git("checkout", "--detach", trigger)
    recovered = select_checkout(trigger)
    assert git("rev-parse", "HEAD") == bump
    assert recovered
    assert select_version(recovered=recovered) == "1.5.1"
    assert json.loads((path / MANIFEST).read_text())["version"] == "1.5.1"
    assert git("status", "--porcelain") == ""


@pytest.mark.parametrize(
    "change",
    [
        {"author": "someone else"},
        {"message": "Unrelated advance"},
        {"extra": {"requirements": ["unexpected"]}},
        {"extra": {"domain": "other"}},
        {"version": "1.6.0"},
        {"version": "2.5.1"},
        {"version": "1.5.0", "extra": {"name": "changed"}},
        {"version": "1.4.9"},
        {"version": "1.5.1rc1"},
        {"version": "01.5.1"},
    ],
)
def test_reject_untrusted_child(history, change):
    trigger, commit, _ = history
    commit(**{"version": "1.5.1", **change})
    git("checkout", "--detach", trigger)
    with pytest.raises(ValueError):
        select_checkout(trigger)
    assert git("rev-parse", "HEAD") == trigger


def test_reject_other_file_change(history):
    trigger, commit, path = history
    (path / "unrelated.txt").write_text("unrelated")
    commit("1.5.1")
    with pytest.raises(ValueError):
        select_checkout(trigger)


def test_recovered_bump_rejects_conflicting_tag(history):
    trigger, commit, _ = history
    commit("1.5.1")
    git("tag", "v1.5.1", trigger)
    recovered = select_checkout(trigger)
    with pytest.raises(ValueError, match="different commit"):
        select_version(recovered=recovered)
    assert git("status", "--porcelain") == ""


def test_reject_manifest_mode_change(history):
    trigger, commit, path = history
    (path / MANIFEST).chmod(0o755)
    commit("1.5.1")
    with pytest.raises(ValueError):
        select_checkout(trigger)


def test_reject_multiple_advances(history):
    trigger, commit, _ = history
    commit("1.5.1")
    commit("1.5.2")
    with pytest.raises(ValueError, match="immediate single-parent"):
        select_checkout(trigger)


def test_reject_merge_child(history):
    trigger, commit, _ = history
    other = commit("1.5.1")
    merge = subprocess.check_output(
        [
            "git",
            "-c",
            f"user.name={BOT_NAME}",
            "-c",
            f"user.email={BOT_EMAIL}",
            "commit-tree",
            "HEAD^{tree}",
            "-p",
            trigger,
            "-p",
            other,
            "-m",
            "Bump version to 1.5.1",
        ],
        text=True,
    ).strip()
    git("update-ref", "refs/remotes/origin/main", merge)
    with pytest.raises(ValueError, match="immediate single-parent"):
        select_checkout(trigger)
