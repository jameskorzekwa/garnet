"""Select a release version, conservatively recovering a pushed bot bump."""

import json
import os
from pathlib import Path
import re
import subprocess

MANIFEST = "custom_components/garnet/manifest.json"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def git(*args):
    """Run Git in the checkout, failing closed on errors."""
    return subprocess.check_output(["git", *args], text=True).strip()


def stable_version(value):
    """Require canonical stable semantic versioning."""
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise ValueError("Expected stable major.minor.patch manifest version")
    return tuple(map(int, value.split(".")))


def select_checkout(trigger):
    """Accept only the trigger or its exact, version-only bot bump child."""
    trigger = git("rev-parse", "--verify", f"{trigger}^{{commit}}")
    tip = git("rev-parse", "origin/main")
    if tip != trigger:
        if git("show", "-s", "--format=%P", tip).split() != [trigger]:
            raise ValueError("origin/main is not the immediate single-parent child")
        before = json.loads(git("show", f"{trigger}:{MANIFEST}"))
        after = json.loads(git("show", f"{tip}:{MANIFEST}"))
        old = stable_version(before["version"])
        new = stable_version(after["version"])
        expected = dict(before, version=after["version"])
        # Compare canonical JSON too: Python equality conflates bool/int values.
        if (
            new[:2] != old[:2]
            or new[2] <= old[2]
            or json.dumps(expected, sort_keys=True) != json.dumps(after, sort_keys=True)
            or git("diff", "--name-only", trigger, tip).splitlines() != [MANIFEST]
            or git("show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", tip)
            != f"{BOT_NAME}\n{BOT_EMAIL}\n{BOT_NAME}\n{BOT_EMAIL}\nBump version to {after['version']}"
            or git("ls-tree", trigger, "--", MANIFEST).split()[0]
            != git("ls-tree", tip, "--", MANIFEST).split()[0]
        ):
            raise ValueError("origin/main is not a trusted version-only patch bump")
    git("checkout", "--detach", tip)
    return tip != trigger


def select_version(*, recovered=False):
    """Reuse a HEAD tag on retry; otherwise find the next unused patch."""
    path = Path(MANIFEST)
    manifest = json.loads(path.read_text())
    version = original = manifest["version"]
    major, minor, patch = stable_version(version)
    head = git("rev-parse", "HEAD")
    while git("tag", "--list", f"v{version}"):
        if git("rev-list", "-n", "1", f"v{version}") == head:
            break
        if recovered:
            raise ValueError("Recovered bump tag points to a different commit")
        patch += 1
        version = f"{major}.{minor}.{patch}"
    if version != original:
        manifest["version"] = version
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    return version


if __name__ == "__main__":
    recovered = select_checkout(os.environ["GITHUB_SHA"])
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        print(f"version={select_version(recovered=recovered)}", file=output)
