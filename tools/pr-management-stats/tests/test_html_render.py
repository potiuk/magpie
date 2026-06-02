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

"""Tests for the HTML render helpers (render.md)."""
from __future__ import annotations

import dashboard
from helpers import make_ctx


def test_partial_banner_appears_only_when_fetch_was_incomplete():
    ctx = make_ctx()
    with_banner = dashboard.render_title(ctx, partial_fetch=True)
    without_banner = dashboard.render_title(ctx, partial_fetch=False)

    assert "INCOMPLETE DATA" in with_banner
    assert 'class="warn"' in with_banner
    assert "INCOMPLETE DATA" not in without_banner


def test_title_renders_repo_and_viewer():
    ctx = make_ctx(repo="apache/airflow", viewer="potiuk")
    out = dashboard.render_title(ctx)
    assert "apache/airflow" in out
    assert "@potiuk" in out


def _hero_for_render():
    return {
        "open_total": 42, "non_drafts": 30, "drafts": 12, "contribs": 25,
        "maintainers": 17, "ready": 7, "ready_contrib": 5, "ready_contrib_4w": 2,
        "ready_contrib_nondraft": 4, "ready_contrib_nondraft_4w": 1,
        "ready_maintainer": 2, "untriaged": 8, "untriaged_4w": 2,
        "qc_triaged": 10, "defacto": 4, "ai_triaged": 6, "bots": 3,
        "bots_dependabot": 2, "bots_other": 1, "contrib_nondraft_total": 20,
    }


def test_status_box_empty_state():
    out = dashboard.render_status_box(("✅ Healthy", "#56d364"), [])
    assert "No urgent actions detected" in out
    assert "✅ Healthy" in out
    assert "What needs attention" in out


def test_status_box_renders_titles_and_actions():
    recs = [
        {
            "priority": "high",
            "icon": "🔥",
            "title": "Triage 3 PRs",
            "detail": "do it",
            "action": "/pr-management-triage all PR issues",
            "count": 3,
        }
    ]
    out = dashboard.render_status_box(("✅ Healthy", "#56d364"), recs)
    assert "Triage 3 PRs" in out
    assert "/pr-management-triage all PR issues" in out
    assert 'class="action high"' in out


def test_hero_rows_show_counts():
    out = dashboard.render_hero_rows(_hero_for_render())
    assert "42" in out
    assert "Ready to review from contributors" in out
    assert "maintainer-authored" in out


def test_top_region_has_backlog_status_and_health():
    out = dashboard.render_top_region(_hero_for_render(), ("✅ Healthy", "#56d364"), [])
    assert "Backlog state" in out
    assert "Status" in out
    assert "✅ Healthy" in out


def test_html_escaping_in_title():
    ctx = make_ctx(repo="a/<script>", viewer="x")
    out = dashboard.render_title(ctx)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
