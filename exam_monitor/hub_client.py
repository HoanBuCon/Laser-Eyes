"""Sync VIGIL One sessions to the shared VIGIL Hub (the Classroom server).

Local first: the desktop always records to its own JSON/JPEG store, and
everything meant for the Hub goes through a durable outbox (SQLite) that a
background thread sends in order, retrying with back-off.  A network drop or a
Hub restart therefore loses nothing and blocks nothing in the Tk thread; the
server de-duplicates resends (session by ``client_session_id``, event by
``event_id``, bookmark by ``request_id``).

The live view (status + small JPEG every couple of seconds) is not queued:
only the newest one matters, and it is dropped while offline.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests

APP_VERSION = "vigil-one-2.0"
BACKOFF = (1, 2, 5, 10, 30)
HTTP_TIMEOUT = 5.0
LIVE_INTERVAL = 2.0

# Sync states shown in the UI
DISABLED = "DISABLED"    # not paired: local only
SYNCED = "SYNCED"        # nothing waiting
PENDING = "PENDING"      # items waiting, Hub reachable
OFFLINE = "OFFLINE"      # Hub unreachable, items kept for later
UNPAIRED = "UNPAIRED"    # the Hub rejected the device token (revoked)


class HubError(RuntimeError):
    pass


@dataclass
class SyncStatus:
    state: str
    pending: int
    failed: int
    last_error: str = ""
    hub_url: str = ""


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


MAX_REJECTED_ATTEMPTS = 3


def _local_networks() -> list:
    """The /16 networks of this computer's own addresses (school LANs often use non-standard ranges)."""
    import socket
    from ipaddress import ip_network

    networks = []
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        addresses = set()
    for address in addresses:
        try:
            networks.append(ip_network(f"{address}/16", strict=False))
        except ValueError:
            pass
    return networks


def check_transport(url: str) -> None:
    """Plain HTTP only inside the local network (this computer, a private range or this computer's own
    network); a Hub reached by name on the Internet needs HTTPS."""
    from ipaddress import ip_address
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "") or host.endswith((".local", ".lan", ".home.arpa")) or "." not in host:
        return
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if address.is_private or address.is_loopback or address.is_link_local:
            return
        if any(address in network for network in _local_networks()):
            return
    raise HubError("Địa chỉ VIGIL Hub ngoài mạng nội bộ phải dùng https:// để bảo vệ dữ liệu thí sinh. "
                   "Trong cùng mạng LAN, hãy nhập địa chỉ IP của máy chủ thi (ví dụ http://192.168.1.10:8000).")


class HubClient:
    def __init__(self, data_dir: Path, base_url: str = "", token: str = "",
                 session: Optional[requests.Session] = None, on_change: Optional[Callable[[SyncStatus], None]] = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = normalize_url(base_url)
        self.token = token or ""
        self.http = session or requests.Session()
        # The heartbeat has its own connection and thread: a long video upload must not make the Hub
        # believe the candidate is gone
        self.live_http = session or requests.Session()
        self.on_change = on_change
        self._db_path = self.data_dir / "one_outbox.db"
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._live_thread: Optional[threading.Thread] = None
        self._live: dict[str, tuple[dict[str, Any], Optional[bytes]]] = {}
        self._last_live_sent = 0.0
        # What the Hub asked for in its last heartbeat reply (online exams adapt the live picture)
        self.analysis_info: dict[str, Any] = {}  # SERVER mode: how far the Hub's analysis is (heartbeat answer)
        self.live_settings: dict[str, Any] = {"enabled": True, "width": 640, "interval": LIVE_INTERVAL}
        self._state = DISABLED if not self.configured else PENDING
        self._last_error = ""
        self._attempt = 0
        self._init_db()
        self._state = self._compute_state()

    # ------------------------------------------------------------ storage
    @contextmanager
    def _conn(self):
        """Commit on success, roll back on error, and always close (Windows keeps open files locked)."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
                "session_key TEXT NOT NULL, payload TEXT NOT NULL, file_path TEXT, created REAL NOT NULL, "
                "attempts INTEGER NOT NULL DEFAULT 0, dead INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (session_key TEXT PRIMARY KEY, server_id TEXT NOT NULL)")
            # Items a Hub rejected get another chance at each start (the Hub may have been updated since)
            conn.execute(f"UPDATE outbox SET dead = 0 WHERE dead = 1 AND attempts < {MAX_REJECTED_ATTEMPTS}")

    def _enqueue(self, kind: str, session_key: str, payload: dict[str, Any], file_path: Optional[str] = None) -> None:
        if not self.configured:
            return
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO outbox (kind, session_key, payload, file_path, created) VALUES (?, ?, ?, ?, ?)",
                (kind, session_key, json.dumps(payload, ensure_ascii=False), file_path, time.time()),
            )
        self._wake.set()
        self._notify()

    def _server_id(self, session_key: str) -> Optional[str]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT server_id FROM sessions WHERE session_key = ?", (session_key,)).fetchone()
        return row["server_id"] if row else None

    def counts(self) -> tuple[int, int]:
        with self._lock, self._conn() as conn:
            pending = conn.execute("SELECT COUNT(*) FROM outbox WHERE dead = 0").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM outbox WHERE dead = 1").fetchone()[0]
        return int(pending), int(failed)

    def retry_failed(self) -> int:
        """Queue again the items the Hub refused (e.g. after the cause was fixed). Returns how many."""
        with self._lock, self._conn() as conn:
            count = conn.execute("UPDATE outbox SET dead = 0, last_error = NULL WHERE dead = 1").rowcount
        self._last_error = ""
        self._wake.set()
        self._notify()
        return int(count or 0)

    # ------------------------------------------------------------ configuration
    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def configure(self, base_url: str, token: str) -> None:
        self.base_url = normalize_url(base_url)
        self.token = token or ""
        self._attempt = 0
        self._last_error = ""
        self._state = self._compute_state()
        self._wake.set()
        self._notify()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1/one{path}"

    # ------------------------------------------------------------ direct calls (used from a UI helper thread)
    def pair(self, base_url: str, code: str, name: str = "") -> dict[str, Any]:
        url = normalize_url(base_url)
        if not url:
            raise HubError("Hãy nhập địa chỉ VIGIL Hub.")
        try:
            res = self.http.post(f"{url}/api/v1/one/client/pair", timeout=HTTP_TIMEOUT, json={
                "code": code.strip(), "name": name or socket.gethostname(), "hostname": socket.gethostname(),
                "app_version": APP_VERSION,
            })
        except requests.RequestException as exc:
            raise HubError(f"Không kết nối được tới {url}: {exc.__class__.__name__}") from exc
        if res.status_code != 200:
            raise HubError(self._detail(res))
        data = res.json()
        self.configure(url, data["token"])
        return data["device"]

    def join(self, base_url: str, join_code: str, candidate_code: str, pin: str) -> dict[str, Any]:
        """Online exam: exchange exam code + candidate code + PIN for a token for this exam."""
        url = normalize_url(base_url)
        if not url:
            raise HubError("Chưa có địa chỉ VIGIL Hub.")
        check_transport(url)
        try:
            res = self.http.post(f"{url}/api/v1/one/client/join", timeout=HTTP_TIMEOUT, json={
                "join_code": join_code.strip(), "candidate_code": candidate_code.strip(), "pin": pin,
                "hostname": socket.gethostname(), "app_version": APP_VERSION,
            })
        except requests.RequestException as exc:
            raise HubError(f"Không kết nối được tới máy chủ thi ({exc.__class__.__name__}). Kiểm tra mạng rồi thử lại.") from exc
        if res.status_code != 200:
            raise HubError(self._detail(res))
        data = res.json()
        self.configure(url, data["token"])
        return data

    def consent(self) -> None:
        try:
            res = self.http.post(self._url("/client/consent"), headers=self._headers(), timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise HubError(f"Không gửi được xác nhận ({exc.__class__.__name__}).") from exc
        if res.status_code != 200:
            raise HubError(self._detail(res))

    def check_frame(self, jpeg: bytes) -> dict[str, Any]:
        """Pre-exam check done by the Hub (SERVER mode): faces and light in one camera picture."""
        try:
            res = self.http.post(self._url("/client/check"), headers=self._headers(),
                                 files={"image": ("check.jpg", jpeg, "image/jpeg")}, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise HubError(f"không kết nối được ({exc.__class__.__name__})") from exc
        if res.status_code != 200:
            raise HubError(self._detail(res))
        return res.json()

    def list_exams(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            res = self.http.get(self._url("/client/exams"), headers=self._headers(), timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            return []
        return res.json() if res.status_code == 200 else []

    @staticmethod
    def _detail(res: requests.Response) -> str:
        try:
            detail = res.json().get("detail")
        except ValueError:
            detail = None
        return str(detail or f"HTTP {res.status_code}")

    # ------------------------------------------------------------ what the app records
    def open_session(self, session_key: str, payload: dict[str, Any]) -> None:
        self._enqueue("open", session_key, {**payload, "client_session_id": session_key})

    def add_event(self, session_key: str, event: dict[str, Any], evidence_path: Optional[str] = None) -> None:
        self._enqueue("event", session_key, event)
        if evidence_path:
            self._enqueue("evidence", session_key, {"event_id": event["event_id"]}, evidence_path)

    def add_clip(self, session_key: str, event_id: str, clip_path: str) -> None:
        """The short MP4 around a signal (sent after the signal and its snapshot)."""
        self._enqueue("clip", session_key, {"event_id": event_id}, clip_path)

    def add_video_segment(self, session_key: str, seq: int, start_ms: float, duration_s: float, path: str,
                          speech: Optional[list] = None) -> None:
        """One segment of the full-session recording (and, in SERVER mode, of the Hub's analysis input)."""
        payload = {"seq": seq, "start_ms": round(start_ms, 1), "duration_s": round(duration_s, 3)}
        if speech is not None:
            payload["speech"] = json.dumps([[round(a, 1), round(b, 1)] for a, b in speech])
        self._enqueue("video", session_key, payload, path)

    def add_bookmark(self, session_key: str, form: dict[str, Any], image_path: Optional[str]) -> None:
        self._enqueue("bookmark", session_key, form, image_path)

    def stop_session(self, session_key: str, payload: dict[str, Any]) -> None:
        self._live.pop(session_key, None)
        self._enqueue("stop", session_key, payload)

    def update_live(self, session_key: str, status: dict[str, Any], jpeg: Optional[bytes]) -> None:
        if self.configured:
            self._live = {session_key: (status, jpeg)}

    # ------------------------------------------------------------ status
    def _compute_state(self) -> str:
        if not self.configured:
            return DISABLED
        if self._state == UNPAIRED:
            return UNPAIRED
        pending, _ = self.counts()
        if self._attempt > 0:
            return OFFLINE
        return PENDING if pending else SYNCED

    def status(self) -> SyncStatus:
        pending, failed = self.counts()
        return SyncStatus(self._state, pending, failed, self._last_error, self.base_url)

    def _notify(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.status())
            except Exception:
                pass

    # ------------------------------------------------------------ worker
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hub-sync", daemon=True)
        self._thread.start()
        self._live_thread = threading.Thread(target=self._run_live, name="hub-live", daemon=True)
        self._live_thread.start()

    def drain(self, timeout: float = 3.0) -> bool:
        """Try to send what is waiting before the app exits. True if nothing is left."""
        if not self.configured or self._state == UNPAIRED:
            return False
        deadline = time.monotonic() + timeout
        self.close(timeout=timeout)  # the background thread must not send the same rows
        while time.monotonic() < deadline:
            try:
                if not self.flush_once():
                    return True
            except HubError:
                return False
        return self.counts()[0] == 0

    def close(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        for thread in (self._thread, self._live_thread):
            if thread:
                thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            delay = 0.5
            if self.configured and self._state != UNPAIRED:
                try:
                    progressed = self.flush_once()
                    delay = 0.05 if progressed else LIVE_INTERVAL / 2
                except HubError:
                    delay = BACKOFF[min(self._attempt - 1, len(BACKOFF) - 1)] if self._attempt else 1
            self._wake.wait(timeout=delay)
            self._wake.clear()

    def _run_live(self) -> None:
        while not self._stop.is_set():
            if self.configured and self._state != UNPAIRED:
                try:
                    self._send_live()
                except Exception:  # a lost live frame is not worth stopping for
                    pass
            self._stop.wait(0.5)

    def flush_once(self) -> bool:
        """Send the oldest waiting item. True if something was sent (or dropped as invalid)."""
        with self._lock, self._conn() as conn:
            # Signals and snapshots first, the full-session video after them, the end of a session last
            row = conn.execute("SELECT * FROM outbox WHERE dead = 0 ORDER BY CASE kind WHEN 'stop' THEN 2 "
                               "WHEN 'video' THEN 1 ELSE 0 END, id LIMIT 1").fetchone()
        if row is None:
            if self._state not in (SYNCED, UNPAIRED):
                self._state = SYNCED
                self._notify()
            return False
        try:
            self._send(row)
        except _Rejected as exc:
            with self._lock, self._conn() as conn:
                conn.execute("UPDATE outbox SET dead = 1, last_error = ?, attempts = attempts + 1 WHERE id = ?", (str(exc), row["id"]))
            self._last_error = str(exc)
            self._notify()
            return True
        except _Unauthorized as exc:
            self._state = UNPAIRED
            self._last_error = str(exc)
            self._notify()
            raise HubError(str(exc)) from exc
        except (requests.RequestException, _Retry) as exc:
            self._attempt += 1
            self._state = OFFLINE
            self._last_error = f"Mất kết nối VIGIL Hub ({exc.__class__.__name__})"
            with self._lock, self._conn() as conn:
                conn.execute("UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?", (self._last_error, row["id"]))
            self._notify()
            raise HubError(self._last_error) from exc
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM outbox WHERE id = ?", (row["id"],))
        self._attempt = 0
        self._last_error = ""
        self._state = self._compute_state()
        self._notify()
        return True

    def _check(self, res: requests.Response) -> dict[str, Any]:
        if res.status_code in (200, 201):
            return res.json()
        if res.status_code == 401:
            raise _Unauthorized("VIGIL Hub đã thu hồi hoặc không nhận thiết bị này. Hãy ghép nối lại.")
        if res.status_code in (408, 425, 429) or res.status_code >= 500:
            raise _Retry(f"HTTP {res.status_code}")
        raise _Rejected(self._detail(res))

    def _send(self, row: sqlite3.Row) -> None:
        kind, key = row["kind"], row["session_key"]
        payload = json.loads(row["payload"])
        if kind == "open":
            data = self._check(self.http.post(self._url("/client/sessions"), headers=self._headers(), json=payload, timeout=HTTP_TIMEOUT))
            with self._lock, self._conn() as conn:
                conn.execute("INSERT OR REPLACE INTO sessions (session_key, server_id) VALUES (?, ?)", (key, data["session"]["session_id"]))
            return
        server_id = self._server_id(key)
        if server_id is None:
            raise _Rejected("Phiên chưa được mở trên VIGIL Hub.")
        if kind == "event":
            self._check(self.http.post(self._url(f"/client/sessions/{server_id}/events"), headers=self._headers(),
                                       json={"events": [payload]}, timeout=HTTP_TIMEOUT))
        elif kind == "evidence":
            data = _read(row["file_path"])
            if data is None:
                raise _Rejected("Ảnh bằng chứng không còn trên máy.")
            self._check(self.http.post(self._url(f"/client/events/{payload['event_id']}/evidence"), headers=self._headers(),
                                       files={"image": ("evidence.jpg", data, "image/jpeg")},
                                       data={"sha256": hashlib.sha256(data).hexdigest()}, timeout=HTTP_TIMEOUT * 2))
        elif kind == "clip":
            data = _read(row["file_path"])
            if data is None:
                raise _Rejected("Clip bằng chứng không còn trên máy.")
            self._check(self.http.post(self._url(f"/client/events/{payload['event_id']}/clip"), headers=self._headers(),
                                       files={"video": ("clip.mp4", data, "video/mp4")},
                                       data={"sha256": hashlib.sha256(data).hexdigest()}, timeout=HTTP_TIMEOUT * 6))
        elif kind == "video":
            data = _read(row["file_path"])
            if data is None:
                raise _Rejected("Đoạn video không còn trên máy.")
            form = {k: str(v) for k, v in payload.items()}
            form["sha256"] = hashlib.sha256(data).hexdigest()
            self._check(self.http.post(self._url(f"/client/sessions/{server_id}/video"), headers=self._headers(),
                                       files={"video": (f"seg_{payload['seq']:05d}.mp4", data, "video/mp4")}, data=form,
                                       timeout=(HTTP_TIMEOUT, 180)))
        elif kind == "bookmark":
            data = _read(row["file_path"])
            if data is None:
                raise _Rejected("Ảnh bookmark không còn trên máy.")
            form = {k: ("" if v is None else str(v)) for k, v in payload.items()}
            form["sha256"] = hashlib.sha256(data).hexdigest()
            self._check(self.http.post(self._url(f"/client/sessions/{server_id}/bookmarks"), headers=self._headers(),
                                       files={"image": ("bookmark.jpg", data, "image/jpeg")}, data=form, timeout=HTTP_TIMEOUT * 2))
        elif kind == "stop":
            self._check(self.http.post(self._url(f"/client/sessions/{server_id}/stop"), headers=self._headers(), json=payload, timeout=HTTP_TIMEOUT))
        else:
            raise _Rejected(f"Loại mục không rõ: {kind}")

    def _send_live(self) -> None:
        # The heartbeat also tells the Hub this computer is still there: never rarer than every 5 s
        interval = min(5.0, float(self.live_settings.get("interval") or LIVE_INTERVAL))
        if not self._live or time.monotonic() - self._last_live_sent < interval:
            return
        key, (status, jpeg) = next(iter(self._live.items()))
        server_id = self._server_id(key)
        if server_id is None:
            return
        self._last_live_sent = time.monotonic()
        pending, _ = self.counts()
        files = {"thumbnail": ("live.jpg", jpeg, "image/jpeg")} if jpeg and self.live_settings.get("enabled", True) else None
        try:
            res = self.live_http.post(self._url(f"/client/sessions/{server_id}/heartbeat"), headers=self._headers(),
                                 data={"status": json.dumps({**status, "pending_sync": pending}, ensure_ascii=False)},
                                 files=files, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            return  # a lost live frame is not worth retrying
        if res.status_code == 401:
            self._state = UNPAIRED
            self._notify()
        elif res.status_code == 200:
            try:
                live = res.json().get("live")
            except ValueError:
                live = None
            if isinstance(live, dict):
                self.live_settings = {**self.live_settings, **live}
            try:
                analysis = res.json().get("analysis")
            except ValueError:
                analysis = None
            if isinstance(analysis, dict):
                self.analysis_info = analysis


class _Retry(Exception):
    pass


class _Rejected(Exception):
    """The Hub refused the item for good (validation): keep it locally, stop resending."""


class _Unauthorized(Exception):
    pass


def _read(path: Optional[str]) -> Optional[bytes]:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None
