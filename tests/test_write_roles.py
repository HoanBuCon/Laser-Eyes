"""Changes need a role once sign-in is required (any Hub opened to the network):
room configuration needs a Chief, running sessions a Proctor, AI data tools an Admin.
Reading stays open to any signed-in user; the open local demo is unchanged."""

from __future__ import annotations

import pytest

from storage.db_models import ExamRoom
from test_one_hub import _sign_in, client, db  # noqa: F401  (fixtures)


@pytest.fixture
def required(monkeypatch):
    monkeypatch.setenv("VIGIL_AUTH_MODE", "required")


def _room(db):  # noqa: F811
    room = ExamRoom(room_code="R-ROLES", name="Phòng kiểm tra quyền")
    db.add(room)
    db.commit()
    return room


def test_proctor_reads_but_cannot_change_room_configuration(client, db, required):  # noqa: F811
    room = _room(db)
    _sign_in(client, db, "proctor", "PROCTOR")
    assert client.get("/api/v1/rooms").status_code == 200
    assert client.patch(f"/api/v1/rooms/{room.id}", json={"name": "x"}).status_code == 403
    assert client.post(f"/api/v1/rooms/{room.id}/seats", json={
        "seat_code": "S01", "polygon": [[0, 0], [10, 0], [10, 10]]}).status_code == 403
    assert client.post("/api/v1/cameras", json={"room_id": room.id, "name": "Cam"}).status_code == 403


def test_chief_changes_room_configuration(client, db, required):  # noqa: F811
    room = _room(db)
    _sign_in(client, db, "chief", "CHIEF")
    res = client.patch(f"/api/v1/rooms/{room.id}", json={"name": "Phòng mới"})
    assert res.status_code not in (401, 403), res.text


def test_data_tools_need_an_admin(client, db, required):  # noqa: F811
    _sign_in(client, db, "chief", "CHIEF")
    assert client.post("/api/v1/data/index-assets").status_code == 403


def test_signed_out_writes_are_refused(client, db, required):  # noqa: F811
    room = _room(db)
    assert client.patch(f"/api/v1/rooms/{room.id}", json={"name": "x"}).status_code == 401
    assert client.post("/api/v1/demo/stop").status_code == 401


def test_open_local_demo_is_unchanged(client, db):  # noqa: F811
    room = _room(db)
    res = client.patch(f"/api/v1/rooms/{room.id}", json={"name": "x"})
    assert res.status_code not in (401, 403), res.text
