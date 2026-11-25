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

"""User-enabled families a project has not adopted: what is asked, applied, and hinted."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from setup_preflight import local_families, sections
from setup_preflight.core import family_not_adopted_findings

from .conftest import personal, write_lock, write_stamp

FLOOR_LOCK = """
    method: marketplace
    url: https://github.com/apache/magpie
    plugins:
      - magpie-setup
      - magpie-utilities
      - magpie-agent-guard
      - magpie-vetted-ops
      - magpie-security
      - magpie-security-tools
"""
PR_SKILL = "pr-triage"  # pr-management
SECURITY_SKILL = "issue-sync"  # security, on the floor


def user_settings(home: Path, enabled: dict[str, object]) -> None:
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabledPlugins": enabled}), encoding="utf-8")


def local_settings(root: Path, enabled: dict[str, object]) -> None:
    path = root / ".claude" / "settings.local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabledPlugins": enabled}), encoding="utf-8")


def stamp(root: Path) -> dict:
    return json.loads((personal(root) / "reconciled.json").read_text(encoding="utf-8"))


@pytest.fixture
def adopted(project: Path, isolated_home: Path) -> Path:
    write_lock(project, FLOOR_LOCK)
    user_settings(
        isolated_home,
        {
            "magpie-pr-management@apache-magpie": True,
            "magpie-release-management@apache-magpie": True,
            "magpie-contributor-growth@apache-magpie": True,
            "magpie-security@apache-magpie": True,
            "magpie-agent-guard@apache-magpie": True,
            "magpie-adversarial-review@apache-magpie": True,
            "magpie-repo-health@apache-magpie": False,
            "magpie-issue@some-other-marketplace": True,
        },
    )
    return project


# --- the candidate list -----------------------------------------------------------


def test_candidates_are_user_enabled_families_minus_the_floor(adopted: Path) -> None:
    answer = local_families.candidates(adopted)
    assert answer["applicable"] is True
    # On the floor (security), disabled (repo-health), another marketplace
    # (issue) and substrate plugins (agent-guard, adversarial-review) are out.
    assert answer["ask"] == [
        "magpie-contributor-growth",
        "magpie-pr-management",
        "magpie-release-management",
    ]
    assert answer["apply"] == []
    assert answer["settings_entries"]["magpie-pr-management"] == "magpie-pr-management@apache-magpie"


def test_substrate_plugins_are_never_family_plugins() -> None:
    plugins = local_families.family_plugins()
    assert "magpie-pr-management" in plugins
    for substrate in ("magpie-agent-guard", "magpie-vetted-ops", "magpie-security-tools", "magpie-setup"):
        assert substrate not in plugins


def test_not_applicable_without_an_adopted_lock(project: Path, isolated_home: Path) -> None:
    user_settings(isolated_home, {"magpie-pr-management@apache-magpie": True})
    assert local_families.candidates(project) == {"applicable": False, "reason": "not-adopted"}
    write_lock(project, "method: local\nplugins:\n  - magpie-setup\n")
    assert local_families.candidates(project)["applicable"] is False


def test_unreadable_user_settings_are_unknown_not_empty(adopted: Path, isolated_home: Path) -> None:
    (isolated_home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    assert local_families.candidates(adopted) == {"applicable": False, "reason": "settings-unreadable"}


def test_claude_config_dir_is_honoured(tmp_path: Path) -> None:
    assert (
        local_families.user_settings_path({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path / "settings.json"
    )


# --- filtering by recorded decisions ------------------------------------------------


def test_keep_is_never_asked_again(adopted: Path) -> None:
    local_families.record(adopted, "magpie-contributor-growth", "keep")
    assert "magpie-contributor-growth" not in local_families.candidates(adopted)["ask"]


def test_hinted_leaves_the_question_open(adopted: Path) -> None:
    local_families.record(adopted, "magpie-pr-management", "hinted")
    assert "magpie-pr-management" in local_families.candidates(adopted)["ask"]


def test_disable_is_applied_not_asked_in_a_new_worktree(adopted: Path, tmp_path_factory) -> None:
    other = tmp_path_factory.mktemp("other-worktree").resolve()
    write_stamp(
        adopted,
        {"local_families": {"magpie-pr-management": {"choice": "disable", "worktrees": [str(other)]}}},
    )
    answer = local_families.candidates(adopted)
    assert "magpie-pr-management" not in answer["ask"]
    assert answer["apply"] == ["magpie-pr-management"]


def test_disable_already_in_this_worktree_is_done(adopted: Path) -> None:
    local_families.record(adopted, "magpie-pr-management", "disable")
    answer = local_families.candidates(adopted)
    assert "magpie-pr-management" not in answer["ask"]
    assert answer["apply"] == []


def test_a_disable_already_present_in_local_settings_is_not_reapplied(adopted: Path) -> None:
    write_stamp(adopted, {"local_families": {"magpie-pr-management": {"choice": "disable", "worktrees": []}}})
    local_settings(adopted, {"magpie-pr-management@apache-magpie": False})
    assert local_families.candidates(adopted)["apply"] == []


def test_an_entry_the_user_set_without_magpie_is_never_touched(adopted: Path) -> None:
    local_settings(adopted, {"magpie-release-management@apache-magpie": True})
    answer = local_families.candidates(adopted)
    assert "magpie-release-management" not in answer["ask"]
    assert "magpie-release-management" not in answer["apply"]


# --- recording -------------------------------------------------------------------------


def test_record_merges_and_keeps_other_keys(adopted: Path) -> None:
    write_stamp(adopted, {"version": "0.9.0", "welcome": {"general": {"choice": "seen"}}})
    entry = local_families.record(adopted, "magpie-pr-management", "disable", today=date(2026, 11, 25))
    assert entry == {"choice": "disable", "at": "2026-11-25", "worktrees": [str(adopted.resolve())]}
    saved = stamp(adopted)
    assert saved["version"] == "0.9.0"
    assert saved["welcome"] == {"general": {"choice": "seen"}}
    assert saved["local_families"]["magpie-pr-management"]["choice"] == "disable"


def test_hinted_never_overwrites_a_decision(adopted: Path) -> None:
    local_families.record(adopted, "magpie-contributor-growth", "keep")
    local_families.record(adopted, "magpie-contributor-growth", "hinted")
    assert stamp(adopted)["local_families"]["magpie-contributor-growth"]["choice"] == "keep"


def test_record_refuses_substrate_plugins_and_unknown_choices(adopted: Path) -> None:
    with pytest.raises(ValueError):
        local_families.record(adopted, "magpie-agent-guard", "disable")
    with pytest.raises(ValueError):
        local_families.record(adopted, "magpie-pr-management", "remove")


# --- the pre-flight hint -------------------------------------------------------------


def test_hint_fires_once_for_a_non_adopted_family(adopted: Path) -> None:
    findings = family_not_adopted_findings(adopted, PR_SKILL)
    assert [(f.scope, f.code, f.section, f.facts) for f in findings] == [
        ("family", "family-not-adopted", "family-not-adopted", {"plugin": "magpie-pr-management"})
    ]
    local_families.record(adopted, "magpie-pr-management", "hinted")
    assert family_not_adopted_findings(adopted, PR_SKILL) == []


def test_no_hint_for_a_floor_family_or_an_unadopted_project(project: Path, isolated_home: Path) -> None:
    user_settings(isolated_home, {"magpie-pr-management@apache-magpie": True})
    assert family_not_adopted_findings(project, PR_SKILL) == []
    write_lock(project, FLOOR_LOCK)
    assert family_not_adopted_findings(project, SECURITY_SKILL) == []


def test_no_hint_when_the_family_is_not_user_enabled(project: Path, isolated_home: Path) -> None:
    write_lock(project, FLOOR_LOCK)
    user_settings(isolated_home, {})
    assert family_not_adopted_findings(project, PR_SKILL) == []


def test_the_hint_section_ships() -> None:
    assert "family-not-adopted" in sections.available()
