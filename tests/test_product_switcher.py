"""One dashboard for both products: the sidebar's Classroom / One switcher and the
``product`` filter of the session list and the statistics behind it."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from storage.db_models import ExamRoom, ExamSession
from test_one_hub import _event, _open, _paired, _sign_in, client, db  # noqa: F401  (fixtures)

SIDEBAR_PAGES = ("index.html", "demo.html", "wall.html", "online.html", "one_video.html", "one_monitor.html", "review.html", "playback.html",
                 "reports.html", "sessions.html", "calibration.html", "admin.html", "users.html")


def _classroom_session(db) -> ExamSession:  # noqa: F811
    room = ExamRoom(id=str(uuid.uuid4()), room_code="P101", name="Phòng 101")
    session = ExamSession(id=str(uuid.uuid4()), room_id=room.id, exam_name="Giữa kỳ", status="COMPLETED")
    db.add_all([room, session])
    db.commit()
    return session


def test_session_list_and_statistics_split_by_product(client, db):  # noqa: F811
    classroom = _classroom_session(db)
    headers = _paired(client, db)
    one_id = _open(client, headers, candidate="SV-2026-007")["session"]["session_id"]
    res = client.post(f"/api/v1/one/client/sessions/{one_id}/events", headers=headers, json={"events": [_event()]})
    assert res.status_code == 200, res.text
    _sign_in(client, db)

    def ids(product):
        res = client.get("/api/v1/exam-sessions", params={"product": product} if product else {})
        assert res.status_code == 200, res.text
        return {s["id"]: s for s in res.json()["items"]}

    assert set(ids(None)) == {classroom.id, one_id} == set(ids("ALL"))
    assert set(ids("CLASSROOM")) == {classroom.id}
    one = ids("ONE")
    assert set(one) == {one_id}
    assert one[one_id]["product"] == "ONE" and one[one_id]["subject_ref"] == "SV-2026-007"
    assert ids("CLASSROOM")[classroom.id]["product"] == "CLASSROOM"

    def total(product):
        res = client.get("/api/v1/statistics/summary", params={"product": product})
        assert res.status_code == 200, res.text
        return res.json()["total_events"]

    assert total("ONE") == 1 and total("CLASSROOM") == 0 and total("ALL") == 1
    assert client.get("/api/v1/statistics/summary", params={"product": "BOTH"}).status_code == 422


def test_every_page_with_a_sidebar_loads_the_product_switcher_before_its_own_scripts():
    for page in SIDEBAR_PAGES:
        html = Path("dashboard", page).read_text(encoding="utf-8")
        assert 'class="vigil-sidebar"' in html, page
        head = html.split("</head>")[0]
        assert '<script src="/static/js/vigil-product.js"></script>' in head, page


def test_switcher_hides_the_other_products_pages_and_blocks():
    js = Path("dashboard/js/vigil-product.js").read_text(encoding="utf-8")
    for href, product in (("/demo", "CLASSROOM"), ("/calibration", "CLASSROOM"), ("/online", "ONE"), ("/one-video", "ONE")):
        assert f"'{href}': '{product}'" in js
    css = Path("dashboard/css/vigil-shell.css").read_text(encoding="utf-8")
    assert 'html[data-product="ONE"] .vigil-nav-item[data-nav-product="CLASSROOM"]:not(.is-active)' in css
    assert 'html[data-product="CLASSROOM"] [data-for-product="ONE"]' in css
    index = Path("dashboard/index.html").read_text(encoding="utf-8")
    assert re.search(r'<section class="vg-panel ops-one" data-for-product="ONE"', index)
    assert re.search(r'<section class="vg-panel ops-rooms" data-for-product="CLASSROOM"', index)
    app = Path("dashboard/js/app.js").read_text(encoding="utf-8")
    assert "/api/v1/one/analysis/status" in app and "statistics/summary?product=" in app
