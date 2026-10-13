"""Shared shell of the dashboard pages: headers, navigation and caching."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app

PAGES = ("index.html", "demo.html", "wall.html", "review.html", "playback.html", "reports.html",
         "sessions.html", "calibration.html", "admin.html", "users.html")


def test_dashboard_files_are_revalidated_so_fixes_reach_the_browser():
    with TestClient(app) as client:
        for url in ("/static/css/dashboard.css", "/static/js/app.js", "/calibration", "/"):
            assert client.get(url).headers.get("cache-control") == "no-cache", url


def test_data_workbench_is_hidden_from_navigation_but_still_reachable():
    for page in PAGES:
        html = Path("dashboard", page).read_text(encoding="utf-8")
        assert 'href="/data-workbench"' not in html, page
    with TestClient(app) as client:
        assert client.get("/data-workbench").status_code == 200


def test_live_monitor_uses_the_shared_page_header():
    html = Path("dashboard/demo.html").read_text(encoding="utf-8")
    assert '<header class="vg-topbar">' in html and "classroom-topbar" not in html
    for chip in ("modeBadge", "stateBadge", "fpsBadge", "wsBadge", "modeText", "stateText", "fpsText", "wsText"):
        assert f'id="{chip}"' in html, chip  # demo.js updates these


def test_calibration_toolbar_aligns_its_controls_on_one_baseline():
    html = Path("dashboard/calibration.html").read_text(encoding="utf-8")
    assert 'calibration-toolbar glass-panel p-4 flex flex-wrap items-end' in html
    css = Path("dashboard/css/dashboard.css").read_text(encoding="utf-8")
    assert ".calibration-toolbar label.block { min-height: 0" in css


def test_every_page_with_a_sidebar_uses_the_shared_page_header():
    for page in PAGES:
        html = Path("dashboard", page).read_text(encoding="utf-8")
        assert '<header class="vg-topbar">' in html, page
        assert "classroom-topbar" not in html and "operations-topbar" not in html and "calibration-topbar" not in html, page
    assert 'id="reviewerName"' in Path("dashboard/review.html").read_text(encoding="utf-8")
