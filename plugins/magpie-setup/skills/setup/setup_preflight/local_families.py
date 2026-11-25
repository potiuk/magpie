# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Families enabled for this user everywhere, but not adopted by this project.

A family enabled in the user-scope Claude Code settings
(`~/.claude/settings.json` → `enabledPlugins`) loads its skills in every
project, including an adopted one whose committed `.apache-magpie.lock`
floor does not list it.  That is not always wrong: `adopt.md` recommends
keeping `magpie-contributor-growth` off the floor and installing it per
maintainer at user scope.  So absence from the floor never means
*disable*; it means *ask once*.

`setup update` → *Reconcile the local agent settings* asks, per family,
whether to keep it here or hide it in this worktree with
`"enabledPlugins": {"<plugin>@apache-magpie": false}` in
`.claude/settings.local.json`.  The answer is kept in the personal
layer's `reconciled.json` under `local_families`:

    "local_families": {
      "magpie-contributor-growth": {"choice": "keep", "at": "2026-11-25"},
      "magpie-pr-management": {"choice": "disable", "at": "2026-11-25",
                               "worktrees": ["/path/to/worktree"]},
      "magpie-repo-health": {"choice": "hinted", "at": "2026-11-25"}
    }

`keep` is never asked again.  `disable` is not asked again either; a
worktree it was not yet applied in gets it as a ready answer to apply
(`.claude/settings.local.json` is per worktree, the personal layer is
not).  `hinted` only records that the pre-flight's one-line hint was
shown, so it does not repeat; the question itself is still open.

Nothing here writes a settings file: Claude Code does not let an agent
write its own settings, so the caller stages the merged file and hands
the user the copy command.  An `enabledPlugins` entry for the plugin in
any project settings file that Magpie did not record is the user's own,
and is never proposed for change.

Run as a module:

    python3 -m setup_preflight.local_families candidates --project-root <worktree>
    python3 -m setup_preflight.local_families record --project-root <worktree> \\
        --plugin magpie-pr-management --choice keep|disable|hinted
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from .isolated import _load_stamp, _stamp_path
from .layers import LOCK_NAME, project_settings
from .lockfile import MalformedLock, load

MARKETPLACE = "apache-magpie"
BLOCK = "local_families"
CHOICES = ("keep", "disable", "hinted")
#: Decisions that close the question; `hinted` leaves it open.
DECIDED = frozenset({"keep", "disable"})
#: Families that are always on the floor of an adopted project.
ALWAYS_ADOPTED = frozenset({"setup"})
LOCAL_METHOD = "local"


def family_plugins() -> frozenset[str]:
    """Every family's plugin name, `magpie-<family>`, from the generated family map.

    Only skill families: substrate plugins (`magpie-agent-guard`,
    `magpie-vetted-ops`, `magpie-security-tools`, …) are not in the map,
    so they can never be offered for hiding.
    """
    try:
        from .families import SKILL_FAMILIES
    except ImportError:
        return frozenset()
    return frozenset(f"magpie-{family}" for family in SKILL_FAMILIES.values() if family not in ALWAYS_ADOPTED)


def user_settings_path(env: Mapping[str, str] | None = None) -> Path:
    """The user-scope Claude Code settings file, honouring `CLAUDE_CONFIG_DIR`."""
    environ = os.environ if env is None else env
    base = environ.get("CLAUDE_CONFIG_DIR") or str(Path(environ.get("HOME", str(Path.home()))) / ".claude")
    return Path(base) / "settings.json"


def _enabled_plugins(path: Path) -> dict[str, object] | None:
    """`enabledPlugins` from one settings file; `None` when it cannot be read."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    block = loaded.get("enabledPlugins")
    return block if isinstance(block, dict) else {}


def _key(plugin: str) -> str:
    return f"{plugin}@{MARKETPLACE}"


def user_enabled(settings: Mapping[str, object], plugins: Iterable[str]) -> list[str]:
    """The family plugins `settings` (an `enabledPlugins` block) turns on from our marketplace."""
    return sorted(plugin for plugin in plugins if settings.get(_key(plugin)) is True)


def adopted_floor(root: Path) -> list[str] | None:
    """The committed floor of an adopted project, or `None` when there is none to compare with.

    `None` for no lock, an unreadable one, or the framework checkout's own
    `method: local` lock, whose skills are the working tree.
    """
    try:
        lock = load(root / LOCK_NAME)
    except (MalformedLock, OSError):
        return None
    if lock is None or lock.method == LOCAL_METHOD:
        return None
    return list(lock.plugins)


def decisions(root: Path) -> dict[str, dict[str, object]]:
    """The recorded `local_families` block, entries that are not objects dropped."""
    raw = _load_stamp(root).get(BLOCK)
    if not isinstance(raw, dict):
        return {}
    return {name: entry for name, entry in raw.items() if isinstance(entry, dict)}


def _worktree_id(root: Path) -> str:
    return str(root.resolve())


def classify(
    *,
    user_settings: Mapping[str, object],
    floor: Iterable[str],
    recorded: Mapping[str, Mapping[str, object]],
    project_entries: Mapping[str, object],
    worktree: str,
    plugins: Iterable[str],
) -> dict[str, list[str]]:
    """Split the user-enabled, non-adopted families into what to ask and what to apply.

    * `ask` — no `keep` / `disable` recorded: put the question to the user.
    * `apply` — `disable` recorded, but not yet in this worktree: carry the
      answer over without asking again.

    A family on the floor, recorded as `keep`, already disabled here, or
    named in a project settings file without a Magpie record is in neither.
    """
    on_floor = set(floor)
    ask: list[str] = []
    apply: list[str] = []
    for plugin in user_enabled(user_settings, plugins):
        if plugin in on_floor:
            continue
        entry = recorded.get(plugin, {})
        choice = entry.get("choice")
        if choice == "keep":
            continue
        if choice == "disable":
            worktrees = entry.get("worktrees")
            done = isinstance(worktrees, list) and worktree in worktrees
            if not done and project_entries.get(_key(plugin)) is not False:
                apply.append(plugin)
            continue
        if _key(plugin) in project_entries:
            # Set by the user in a project settings file, not by Magpie.
            continue
        ask.append(plugin)
    return {"ask": ask, "apply": apply}


def _project_entries(root: Path) -> dict[str, object] | None:
    """Every project-scope `enabledPlugins` entry, highest precedence first wins."""
    merged: dict[str, object] = {}
    for path in reversed(project_settings(root)):
        block = _enabled_plugins(path)
        if block is None:
            return None
        merged.update(block)
    return merged


def candidates(root: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The `candidates` answer for the worktree `root`.

    `{"applicable": false}` when the project is not adopted or a settings
    file cannot be read — unknown is never reported as nothing enabled.
    """
    floor = adopted_floor(root)
    if floor is None:
        return {"applicable": False, "reason": "not-adopted"}
    user = _enabled_plugins(user_settings_path(env))
    project = _project_entries(root)
    if user is None or project is None:
        return {"applicable": False, "reason": "settings-unreadable"}
    split = classify(
        user_settings=user,
        floor=floor,
        recorded=decisions(root),
        project_entries=project,
        worktree=_worktree_id(root),
        plugins=family_plugins(),
    )
    return {
        "applicable": True,
        **split,
        "settings_entries": {plugin: _key(plugin) for plugin in [*split["ask"], *split["apply"]]},
    }


def hint_due(root: Path, family: str | None, env: Mapping[str, str] | None = None) -> str | None:
    """The plugin to hint about when a skill of `family` runs here, else `None`.

    Due once: the family is enabled at user scope, the project is adopted
    without it, and nothing — not even an earlier hint — is recorded for it.
    """
    if family is None or family in ALWAYS_ADOPTED:
        return None
    plugin = f"magpie-{family}"
    if plugin not in family_plugins():
        return None
    floor = adopted_floor(root)
    if floor is None or plugin in floor or plugin in decisions(root):
        return None
    user = _enabled_plugins(user_settings_path(env))
    if not user or plugin not in user_enabled(user, [plugin]):
        return None
    project = _project_entries(root)
    if project is None or _key(plugin) in project:
        return None
    return plugin


def record(root: Path, plugin: str, choice: str, today: date | None = None) -> dict[str, object]:
    """Merge one answer into `local_families.<plugin>`; every other key is kept.

    `disable` adds this worktree to the entry's `worktrees`.  `hinted`
    never replaces a `keep` or `disable` already recorded.
    """
    if choice not in CHOICES:
        raise ValueError(f"unknown choice {choice!r}; expected one of {CHOICES}")
    if plugin not in family_plugins():
        raise ValueError(f"not a Magpie family plugin: {plugin!r}")
    stamp = _load_stamp(root)
    raw = stamp.get(BLOCK)
    block: dict[str, object] = dict(raw) if isinstance(raw, dict) else {}
    previous = block.get(plugin)
    previous = previous if isinstance(previous, dict) else {}
    if choice == "hinted" and previous.get("choice") in DECIDED:
        return previous
    entry: dict[str, object] = {"choice": choice, "at": (today or date.today()).isoformat()}
    if choice == "disable":
        worktrees = previous.get("worktrees") if previous.get("choice") == "disable" else None
        listed = [w for w in worktrees if isinstance(w, str)] if isinstance(worktrees, list) else []
        here = _worktree_id(root)
        entry["worktrees"] = sorted({*listed, here})
    block[plugin] = entry
    stamp[BLOCK] = block
    path = _stamp_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stamp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup_preflight.local_families", description=__doc__.splitlines()[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("candidates", "record"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--project-root", type=Path, default=Path.cwd())
        if name == "record":
            cmd.add_argument("--plugin", required=True)
            cmd.add_argument("--choice", required=True, choices=CHOICES)
    args = parser.parse_args(argv)
    try:
        if args.command == "candidates":
            print(json.dumps(candidates(args.project_root), indent=2, sort_keys=True))
        else:
            print(json.dumps(record(args.project_root, args.plugin, args.choice), indent=2, sort_keys=True))
    except (ValueError, OSError) as exc:
        print(f"local_families: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
