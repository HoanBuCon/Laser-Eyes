"""VIGIL One — candidate mode, for online exams on the candidate's own computer.

Steps: join (exam code + candidate code + PIN) -> consent -> device check ->
exam (calibration first, then a small always-on-top window) -> submit.

The app only captures: it streams the camera live to VIGIL Hub (``live_stream``)
and records the whole exam in short H.264 segments sent through the outbox.  All
the analysis — gaze, head, faces, objects, speech — runs on the Hub, next to VIGIL
Classroom, where proctors watch and review.  The candidate cannot change thresholds,
review or delete signals, or pick a video file; they only see what they need to fix
(no face, too dark, look at the screen to calibrate) and the connection state.
``--sim`` keeps a scripted, on-device simulation for demos without a camera.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import messagebox

import cv2
from PIL import Image, ImageTk

from .audio import VoiceActivityDetector, speech_is_confirmed
from .clip_recorder import ClipRecorder
from .session_recorder import SessionRecorder
from .events import EventDetector, SessionStore
from .live_stream import LiveStreamer
from .paths import app_data_dir, resource
from .hub_client import OFFLINE, SYNCED, UNPAIRED, HubClient, HubError
from .models import AnalysisResult, EventType, MonitoringEvent, SessionInfo, Severity
from .sources import CameraSource, DemoSource
from .theme import COLORS, FONTS, brand_font, set_theme

APP_TITLE = "VIGIL One — Thi trực tuyến"
LOGO_PATH = resource("assets", "V.png")
VIRTUAL_CAMERA_HINTS = ("obs", "virtual", "manycam", "snap camera", "xsplit", "droidcam", "epoccam", "camtwist", "e2esoft")

CONSENT_TEXT = (
    "Trong thời gian làm bài, VIGIL One sử dụng webcam (và micro nếu kỳ thi yêu cầu) của máy này để ghi nhận "
    "các tín hiệu quan sát như: hướng nhìn, quay đầu, không thấy khuôn mặt, có thêm người, vật thể như điện thoại "
    "hoặc tài liệu, tiếng nói chuyện.\n\n"
    "• Hình ảnh camera được gửi trực tiếp về máy chủ của đơn vị tổ chức thi để giám thị theo dõi và để máy chủ "
    "phân tích. Phần mềm trên máy này không phân tích hình ảnh.\n"
    "• Toàn bộ buổi thi được ghi hình và lưu trên máy chủ thi; khi có tín hiệu, máy chủ lưu kèm ảnh và đoạn video ngắn.\n"
    "• Các tín hiệu chỉ để giám thị xem xét. Phần mềm không tự kết luận vi phạm; mọi quyết định do giám thị đưa ra.\n"
    "• Dữ liệu được lưu và xử lý theo quy định của đơn vị tổ chức thi; bạn có quyền hỏi và khiếu nại theo quy chế thi.\n\n"
    "Không nhận diện khuôn mặt. Không theo dõi các ứng dụng khác trên máy."
)


def data_dir() -> Path:
    """Candidate-mode files, kept apart from the proctor mode's sessions on the same computer."""
    return app_data_dir() / "candidate"


def display_count() -> int:
    if sys.platform != "win32":
        return 1
    try:
        return int(ctypes.windll.user32.GetSystemMetrics(80))  # SM_CMONITORS
    except (AttributeError, OSError):
        return 1


def camera_device_names() -> list[str]:
    """Names of camera drivers installed on Windows (to spot virtual cameras)."""
    if sys.platform != "win32":
        return []
    cmd = ["powershell", "-NoProfile", "-Command",
           "Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPClass -in 'Camera','Image' } | "
           "ForEach-Object { $_.Name }"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=8,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _with_offset(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return (parsed if parsed.tzinfo else parsed.astimezone()).isoformat(timespec="milliseconds")


class Button(tk.Button):
    def __init__(self, master, text, command=None, primary=True, **kw):
        super().__init__(master, text=text, command=command, relief="flat", bd=0, cursor="hand2",
                         bg=COLORS["primary"] if primary else COLORS["surface_alt"],
                         fg=COLORS["primary_text"] if primary else COLORS["text"],
                         activebackground=COLORS["primary_hover"] if primary else COLORS["surface_hover"],
                         activeforeground=COLORS["primary_text"] if primary else COLORS["text"],
                         font=FONTS["body_medium"], padx=18, pady=9, **kw)


class CandidateApp(tk.Tk):
    def __init__(self, config: dict[str, Any], simulate: bool = False):
        super().__init__()
        set_theme("light")
        self.config_data = config
        self.simulate = simulate
        self.title(APP_TITLE)
        self._set_window_icon()
        self.configure(bg=COLORS["bg"])
        # Tall enough for every step on a scaled (125-150 %) laptop screen; the action buttons
        # always sit in a bar at the bottom, so a shorter window only shrinks the content above them
        height = max(560, min(780, self.winfo_screenheight() - 110))
        self.geometry(f"900x{height}+80+30")
        self.minsize(760, 520)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.dir = data_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self.dir / "candidate.json"
        saved = self._load_state()
        # A locked address (school build) always wins; an editable one remembers what the candidate typed last
        first, second = (config.get("hub_url"), saved.get("hub_url"))
        if not config.get("lock_hub_url"):
            first, second = second, first
        self.hub_url = first or second or "http://127.0.0.1:8000"
        self.hub = HubClient(self.dir / "hub", base_url=self.hub_url, token=saved.get("token", ""),
                             on_change=lambda status: self._ui_queue.put(("sync", status)))
        self.exam: dict[str, Any] = saved.get("exam") or {}
        self.candidate: dict[str, Any] = saved.get("candidate") or {}
        self.policy: dict[str, Any] = saved.get("policy") or {}
        self.store = SessionStore(self.dir)

        self._ui_queue: queue.Queue = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._clips: Optional[ClipRecorder] = None
        self._recorder: Optional[SessionRecorder] = None
        self._stop = threading.Event()
        self._phase = "idle"          # idle -> check -> exam
        self._source = None
        self._streamer: Optional[LiveStreamer] = None
        # SERVER analysis mode: when the microphone heard a voice (ms of the session), sent with each segment
        self._speech_lock = threading.Lock()
        self._speech: list[list[float]] = []
        self._speech_open: Optional[float] = None
        self._session: Optional[SessionInfo] = None
        self._started_mono = 0.0
        self._checks: dict[str, tuple[str, str]] = {}
        self._photo = None
        self._finished = False

        self.body = tk.Frame(self, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True)
        self.after(50, self._pump)
        self.hub.start()
        if self.hub.configured and self.exam:
            self.after(100, self._resume_or_join)
        else:
            self._show_join()

    def _set_window_icon(self) -> None:
        try:
            icon = Image.open(LOGO_PATH).convert("RGBA")
            icon.thumbnail((64, 64), Image.Resampling.LANCZOS)
            self._icon = ImageTk.PhotoImage(icon)
            self.iconphoto(True, self._icon)
        except OSError:
            pass

    # ------------------------------------------------------------ persistence
    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        state = {"hub_url": self.hub.base_url, "token": self.hub.token, "exam": self.exam,
                 "candidate": self.candidate, "policy": self.policy}
        self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------ layout helpers
    def _clear(self) -> tk.Frame:
        for child in self.body.winfo_children():
            child.destroy()
        frame = tk.Frame(self.body, bg=COLORS["bg"])
        frame.pack(fill="both", expand=True, padx=40, pady=28)
        return frame

    def _action_bar(self, parent) -> tk.Frame:
        """A bar at the bottom of the step for its buttons: packed before the content, so it stays visible."""
        bar = tk.Frame(parent, bg=COLORS["bg"])
        bar.pack(side="bottom", fill="x", pady=(12, 0))
        return bar

    def _header(self, parent, title: str, subtitle: str = "") -> None:
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill="x", pady=(0, 18))
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            logo.thumbnail((44, 44), Image.Resampling.LANCZOS)
            self._logo = ImageTk.PhotoImage(logo)
            tk.Label(row, image=self._logo, bg=COLORS["bg"]).pack(side="left", padx=(0, 12))
        except OSError:
            pass
        text = tk.Frame(row, bg=COLORS["bg"])
        text.pack(side="left")
        tk.Label(text, text="vigil one", bg=COLORS["bg"], fg=COLORS["text_muted"], font=brand_font(16)).pack(anchor="w")
        tk.Label(text, text=title, bg=COLORS["bg"], fg=COLORS["text"], font=FONTS["h1"]).pack(anchor="w")
        if subtitle:
            tk.Label(text, text=subtitle, bg=COLORS["bg"], fg=COLORS["text_muted"], font=FONTS["small"]).pack(anchor="w")

    def _preview_label(self, parent, width: int, height: int) -> tk.Label:
        """Camera preview sized in pixels (a Label without an image measures width/height in characters)."""
        self._blank = ImageTk.PhotoImage(Image.new("RGB", (width, height), COLORS["video_bg"]))
        return tk.Label(parent, image=self._blank, bg=COLORS["video_bg"], bd=0)

    def _field(self, parent, label: str, show: str = "", value: str = "") -> tk.Entry:
        tk.Label(parent, text=label, bg=COLORS["bg"], fg=COLORS["text_muted"], font=FONTS["small"]).pack(anchor="w", pady=(10, 4))
        entry = tk.Entry(parent, show=show, bg=COLORS["surface"], fg=COLORS["text"], relief="solid", bd=1,
                         insertbackground=COLORS["text"], font=("Segoe UI", 13), width=34)
        entry.insert(0, value)
        entry.pack(anchor="w", ipady=6)
        return entry

    # ------------------------------------------------------------ step 1: join
    def _show_join(self, message: str = "") -> None:
        frame = self._clear()
        self._header(frame, "Vào phòng thi trực tuyến", "Nhập thông tin do đơn vị tổ chức thi cung cấp")
        if not self.config_data.get("lock_hub_url"):
            self.hub_entry = self._field(frame, "Địa chỉ máy chủ thi", value=self.hub_url)
        else:
            self.hub_entry = None
        bar = self._action_bar(frame)
        self.join_button = Button(bar, "VÀO THI  →", self._join)
        self.join_button.pack(side="bottom", anchor="w")
        self.join_error = tk.Label(bar, text=message, bg=COLORS["bg"], fg=COLORS["danger"], font=FONTS["small"],
                                   wraplength=560, justify="left")
        self.join_error.pack(side="bottom", anchor="w", pady=(0, 10))
        self.code_entry = self._field(frame, "Mã kỳ thi")
        self.cand_entry = self._field(frame, "Mã thí sinh", value=self.candidate.get("code", ""))
        self.pin_entry = self._field(frame, "Mã PIN", show="•")
        self.bind("<Return>", lambda _e: self._join())
        self.code_entry.focus_set()

    def _join(self) -> None:
        if self._phase != "idle":
            return
        url = self.hub_entry.get().strip() if self.hub_entry else self.hub_url
        code, cand, pin = self.code_entry.get().strip(), self.cand_entry.get().strip(), self.pin_entry.get()
        if not (code and cand and pin):
            self.join_error.configure(text="Hãy nhập đủ mã kỳ thi, mã thí sinh và PIN.")
            return
        self.join_button.configure(state="disabled", text="ĐANG KIỂM TRA…")

        def work():
            try:
                self._ui_queue.put(("joined", self.hub.join(url, code, cand, pin)))
            except HubError as exc:
                self._ui_queue.put(("join_failed", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _on_joined(self, data: dict[str, Any]) -> None:
        self.unbind("<Return>")
        self.exam, self.policy = data["exam"], data["policy"]
        self.candidate = data["candidate"]
        self.hub_url = self.hub.base_url
        self._save_state()
        if self.candidate.get("consent_given"):
            self._show_check()
        else:
            self._show_consent()

    def _resume_or_join(self) -> None:
        """A saved token from an earlier start today: continue without asking for the PIN again."""
        def work():
            try:
                res = self.hub.http.get(self.hub._url("/client/policy"), headers=self.hub._headers(), timeout=5)
                ok = res.status_code == 200
                self._ui_queue.put(("resume", res.json() if ok else None))
            except Exception:
                self._ui_queue.put(("resume", None))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ step 2: consent
    def _show_consent(self) -> None:
        frame = self._clear()
        self._header(frame, "Thông báo và đồng ý", f"{self.exam.get('name', '')} · Thí sinh {self.candidate.get('code', '')}"
                     + (f" · {self.candidate['full_name']}" if self.candidate.get("full_name") else ""))
        bar = self._action_bar(frame)
        agreed = tk.BooleanVar(value=False)
        go = Button(bar, "TIẾP TỤC  →", self._consent)
        go.configure(state="disabled")
        go.pack(side="bottom", anchor="w")
        self.consent_error = tk.Label(bar, text="", bg=COLORS["bg"], fg=COLORS["danger"], font=FONTS["small"])
        self.consent_error.pack(side="bottom", anchor="w")
        tk.Checkbutton(bar, text="Tôi đã đọc và đồng ý cho VIGIL One giám sát trong thời gian làm bài.",
                       variable=agreed, bg=COLORS["bg"], fg=COLORS["text"], activebackground=COLORS["bg"],
                       font=FONTS["body"], command=lambda: go.configure(state="normal" if agreed.get() else "disabled")
                       ).pack(side="bottom", anchor="w", pady=(0, 8))
        text_row = tk.Frame(frame, bg=COLORS["bg"])
        text_row.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(text_row, orient="vertical")
        box = tk.Text(text_row, height=6, wrap="word", bg=COLORS["surface"], fg=COLORS["text"], relief="solid", bd=1,
                      font=FONTS["body"], padx=14, pady=12, yscrollcommand=scroll.set)
        scroll.configure(command=box.yview)
        box.insert("1.0", CONSENT_TEXT + ("" if self.policy.get("microphone", True) else "\n\nKỳ thi này không dùng micro."))
        box.configure(state="disabled")
        scroll.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True)

    def _consent(self) -> None:
        def work():
            try:
                self.hub.consent()
                self._ui_queue.put(("consented", None))
            except HubError as exc:
                self._ui_queue.put(("consent_failed", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ step 3: device check
    def _show_check(self) -> None:
        frame = self._clear()
        self._header(frame, "Kiểm tra thiết bị", "Ngồi ngay ngắn, nhìn vào màn hình, đủ ánh sáng trên khuôn mặt")
        bar = self._action_bar(frame)
        row = tk.Frame(frame, bg=COLORS["bg"])
        row.pack(fill="both", expand=True)
        self.preview = self._preview_label(row, 480, 270)
        self.preview.pack(side="left", anchor="n")
        side = tk.Frame(row, bg=COLORS["bg"])
        side.pack(side="left", fill="both", expand=True, padx=(22, 0))
        self.check_labels = {}
        items = [("camera", "Camera"), ("face", "Đúng một khuôn mặt"), ("light", "Ánh sáng")]
        if self.policy.get("microphone", True):
            items.append(("mic", "Micro"))
        items += [("hub", "Kết nối máy chủ thi"), ("virtual", "Không dùng camera ảo"), ("displays", "Một màn hình")]
        for key, label in items:
            lab = tk.Label(side, text=f"…  {label}", bg=COLORS["bg"], fg=COLORS["text_muted"], font=FONTS["body"], anchor="w")
            lab.pack(fill="x", pady=4)
            self.check_labels[key] = (lab, label)
        self.check_hint = tk.Label(side, text="", bg=COLORS["bg"], fg=COLORS["text_muted"], font=FONTS["small"],
                                   wraplength=280, justify="left")
        self.check_hint.pack(anchor="w", pady=(12, 0))
        self.start_button = Button(bar, "BẮT ĐẦU LÀM BÀI", self._start_exam)
        self.start_button.configure(state="disabled")
        self.start_button.pack(anchor="w")
        self._set_check("hub", "ok", "")
        self._phase = "check"
        self._stop.clear()
        self._worker = threading.Thread(target=self._camera_loop, daemon=True, name="candidate-camera")
        self._worker.start()
        threading.Thread(target=self._system_checks, daemon=True).start()

    def _set_check(self, key: str, state: str, detail: str) -> None:
        self._checks[key] = (state, detail)
        self._ui_queue.put(("check", key))

    def _system_checks(self) -> None:
        names = camera_device_names()
        virtual = [n for n in names if any(h in n.lower() for h in VIRTUAL_CAMERA_HINTS)]
        self._virtual_names = virtual
        self._set_check("virtual", "warn" if virtual else "ok", ", ".join(virtual))
        screens = display_count()
        self._screens = screens
        self._set_check("displays", "warn" if screens > 1 else "ok", f"{screens} màn hình" if screens > 1 else "")
        if self.policy.get("microphone", True) and not self.simulate:
            vad = VoiceActivityDetector()
            state = vad.start()
            vad.stop()
            self._set_check("mic", "ok" if state.available else "warn", "" if state.available else "Không mở được micro")
        elif self.policy.get("microphone", True):
            self._set_check("mic", "ok", "mô phỏng")

    def _render_check(self, key: str) -> None:
        if key not in self.check_labels:
            return
        lab, label = self.check_labels[key]
        state, detail = self._checks[key]
        mark, color = {"ok": ("✓", COLORS["good"]), "warn": ("!", COLORS["warning"]), "fail": ("✗", COLORS["danger"])}[state]
        lab.configure(text=f"{mark}  {label}" + (f" — {detail}" if detail else ""), fg=color)
        ready = all(self._checks.get(k, ("fail", ""))[0] != "fail" for k in ("camera", "face", "light")) \
            and all(k in self._checks for k in ("camera", "face", "light"))
        if hasattr(self, "start_button") and self._phase == "check":
            self.start_button.configure(state="normal" if ready else "disabled")
            hints = []
            if self._checks.get("face", ("ok", ""))[0] == "fail":
                hints.append("Hãy ngồi vào giữa khung hình, chỉ một người.")
            if self._checks.get("light", ("ok", ""))[0] == "fail":
                hints.append("Bật đèn hoặc quay mặt về phía nguồn sáng.")
            if any(self._checks.get(k, ("ok", ""))[0] == "warn" for k in ("virtual", "displays", "mic")):
                hints.append("Các mục có dấu ! vẫn cho phép làm bài nhưng sẽ được báo cho giám thị.")
            self.check_hint.configure(text="\n".join(hints))

    # ------------------------------------------------------------ camera + analysis worker
    def _open_source(self):
        if self.simulate:
            return DemoSource()
        source = CameraSource(int(self.config_data.get("camera_index", 0)))
        if not source.capture.isOpened():
            source.release()
            return None
        return source

    def _camera_loop(self) -> None:
        source = self._open_source()
        if source is None:
            self._set_check("camera", "fail", "Không mở được camera — kiểm tra quyền camera của Windows")
            return
        self._source = source
        self._set_check("camera", "ok", "mô phỏng" if self.simulate else "")
        server = self._server_analysis()
        if server:
            # The Hub analyses the video: no face model here, the pre-exam check asks the Hub too
            threading.Thread(target=self._remote_checks, daemon=True, name="candidate-check").start()
        detector = EventDetector()  # --sim only: its scripted results become signals here
        for name, seconds in (self.policy.get("thresholds") or {}).items():
            try:
                detector.set_threshold(EventType(name), float(seconds))
            except ValueError:
                pass
        audio: Optional[VoiceActivityDetector] = None
        last_check = last_live = last_screen = 0.0
        frames, fps_started = 0, time.monotonic()
        try:
            while not self._stop.is_set():
                ok, frame = source.read()
                now = time.monotonic()
                if not ok or frame is None:
                    if self._phase == "exam":
                        self._emit(MonitoringEvent(uuid.uuid4().hex[:10].upper(), self._session.session_id,
                                                   EventType.CAMERA_INTERRUPTED, Severity.HIGH,
                                                   datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds"),
                                                   0.0, "Camera bị gián đoạn.", 1.0), frame=None)
                    self._ui_queue.put(("camera_lost", None))
                    break
                self._last_frame = frame
                result = source.last_result if self.simulate else self._remote_result(frame, now)
                if self._phase == "exam":
                    if self._clips is None and self.policy.get("video_clips", True) and not server:
                        session_id = self._session.session_id
                        self._clips = ClipRecorder(lambda event_id, path: self.hub.add_clip(session_id, event_id, path))
                    if self._clips is not None:
                        self._clips.push(frame, now)
                    if self._recorder is None and self.policy.get("record_video", True):
                        self._recorder = self._start_recording()
                    if self._recorder is not None:
                        self._recorder.push(frame, now)
                    if audio is None and self.policy.get("microphone", True) and not self.simulate:
                        audio = VoiceActivityDetector()
                        audio.start()
                    voice = None
                    if audio is not None:
                        state = audio.snapshot()
                        voice = bool(state.voice_active) if state.available else None
                        if server:
                            self._track_speech(state.voice_active, now)
                        else:
                            result = replace(result, is_talking=speech_is_confirmed(result.talking_score, result.is_talking, state))
                    if server:
                        if self._streamer is None:
                            self._streamer = LiveStreamer(
                                self.hub, self._session.session_id, fps=float(self.policy.get("analysis_fps") or 5),
                                width=min(640, int(self.policy.get("video_width") or 640)), on_reply=self._on_live_reply)
                        self._streamer.push(frame, (now - self._started_mono) * 1000.0, voice)
                    if not server:
                        for event in detector.update(result, self._session.session_id, now):
                            self._emit(event, frame)
                    frames += 1
                    if now - last_live >= float(self.hub.live_settings.get("interval") or 5):
                        last_live = now
                        self._push_live(frame, result, frames, fps_started, now)
                    if now - last_screen >= 60:
                        last_screen = now
                        if display_count() > 1 and not getattr(self, "_screens_flagged", False):
                            self._screens_flagged = True
                            self._integrity("multiple_displays", f"Máy đang nối {display_count()} màn hình.")
                    self._session.frame_count = frames
                    self._session.average_fps = frames / max(0.001, now - fps_started)
                elif now - last_check >= 0.4 and not server:
                    last_check = now
                    faces = int(result.face_count)
                    self._set_check("face", "ok" if faces == 1 else "fail",
                                    "" if faces == 1 else ("không thấy khuôn mặt" if faces == 0 else f"{faces} khuôn mặt"))
                    dark = float(getattr(result, "brightness", 120)) < 55
                    self._set_check("light", "fail" if dark else "ok", "quá tối" if dark else "")
                self._ui_queue.put(("frame", (frame, result)))
                if self.simulate:
                    time.sleep(1 / 30)
        finally:
            if audio is not None:
                audio.stop()
            if self._streamer is not None:
                self._streamer.close()
            if self._recorder is not None:
                self._recorder.close()  # the last minute of video is queued before the session's stop
            if self._clips is not None:
                self._clips.close()  # clips are queued before the session's stop
            source.release()

    def _start_recording(self) -> SessionRecorder:
        session_id = self._session.session_id
        server = self._server_analysis()

        def on_segment(seq, path, start_ms, duration_s):
            speech = self._speech_between(start_ms, start_ms + duration_s * 1000.0) if server else None
            self.hub.add_video_segment(session_id, seq, start_ms, duration_s, path, speech=speech)

        # In SERVER mode the segments are what the Hub analyses: short ones keep its delay short
        seconds = float(self.policy.get("segment_seconds") or 10) if server else 60.0
        return SessionRecorder(
            self.dir / "recordings" / session_id, on_segment,
            fps=float(self.policy.get("video_fps") or 10), width=int(self.policy.get("video_width") or 640),
            segment_seconds=seconds, started=self._started_mono)

    # ------------------------------------------------------------ SERVER analysis mode
    def _server_analysis(self) -> bool:
        """The Hub analyses this candidate's video; only a --sim demo keeps its scripted signals."""
        return not self.simulate

    def _on_live_reply(self, reply: dict) -> None:
        """The Hub's answer on the live stream (about once a second): what its analysis sees."""
        if isinstance(reply.get("analysis"), dict):
            self.hub.analysis_info = reply["analysis"]

    def _remote_checks(self) -> None:
        """Pre-exam: send a small camera picture to the Hub every ~1.5 s and show what it sees."""
        while not self._stop.is_set() and self._phase == "check":
            frame = getattr(self, "_last_frame", None)
            if frame is not None:
                h, w = frame.shape[:2]
                small = cv2.resize(frame, (480, max(1, int(h * 480 / max(1, w))))) if w > 480 else frame
                ok, enc = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                try:
                    seen = self.hub.check_frame(enc.tobytes()) if ok else None
                except HubError as exc:
                    self._set_check("face", "fail", f"máy chủ chưa kiểm tra được ({exc})")
                    seen = None
                if seen is not None:
                    self._remote_seen = seen
                    faces = int(seen.get("face_count") or 0)
                    self._set_check("face", "ok" if faces == 1 else "fail",
                                    "" if faces == 1 else ("không thấy khuôn mặt" if faces == 0 else f"{faces} khuôn mặt"))
                    self._set_check("light", "fail" if seen.get("too_dark") else "ok", "quá tối" if seen.get("too_dark") else "")
            self._stop.wait(1.5)

    def _remote_result(self, frame, now: float):
        """What the UI shows when the Hub does the analysis: its last answer, plus brightness measured here."""
        info = self.hub.analysis_info or {}
        seen = getattr(self, "_remote_seen", None) or {}
        faces = info.get("faces", seen.get("face_count", 1))
        return AnalysisResult(timestamp=now, face_count=int(faces if faces is not None else 1),
                              brightness=float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()),
                              calibrated=str(info.get("calibration") or "") == "READY",
                              status_text="Máy chủ đang phân tích")

    def _track_speech(self, voice: bool, now: float) -> None:
        t_ms = round((now - self._started_mono) * 1000.0, 1)
        with self._speech_lock:
            if voice and self._speech_open is None:
                self._speech_open = t_ms
            elif not voice and self._speech_open is not None:
                self._speech.append([self._speech_open, t_ms])
                self._speech_open = None
            self._speech = self._speech[-400:]

    def _speech_between(self, start_ms: float, end_ms: float) -> list[list[float]]:
        with self._speech_lock:
            items = list(self._speech) + ([[self._speech_open, end_ms]] if self._speech_open is not None else [])
        return [[max(a, start_ms), min(b, end_ms)] for a, b in items if b > start_ms and a < end_ms]

    def _emit(self, event: MonitoringEvent, frame) -> None:
        """Record a signal locally, then queue it (and its snapshot) for VIGIL Hub."""
        session = self._session
        if frame is not None:
            path = self.store.evidence_dir / f"{session.session_id}_{event.event_id}.jpg"
            cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            event.evidence_path = str(path)
        session.events.append(event)
        elapsed = max(0.0, (time.monotonic() - self._started_mono - event.duration_seconds) * 1000.0)
        hub_event_id = f"{session.session_id}-{event.event_id}"[:36]
        if frame is not None and self._clips is not None:
            self._clips.request(hub_event_id, self.store.evidence_dir / f"{session.session_id}_{event.event_id}.mp4")
        self.hub.add_event(session.session_id, {
            "event_id": hub_event_id, "event_type": event.event_type.value,
            "severity": event.severity.value, "started_at": _with_offset(event.started_at),
            "ended_at": _with_offset(event.ended_at), "duration_seconds": round(event.duration_seconds, 2),
            "session_elapsed_ms": round(elapsed, 1), "confidence": round(float(event.confidence), 3),
            "label": event.label, "reason": event.reason,
        }, event.evidence_path)

    def _integrity(self, kind: str, reason: str) -> None:
        """Set-up facts the Hub turns into integrity signals (virtual camera, several screens)."""
        session = self._session
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self.hub.add_event(session.session_id, {
            "event_id": f"{session.session_id}-{kind[:6].upper()}{uuid.uuid4().hex[:4]}"[:36], "event_type": kind,
            "severity": "medium", "started_at": now, "ended_at": now, "duration_seconds": 0,
            "session_elapsed_ms": round((time.monotonic() - self._started_mono) * 1000.0, 1), "confidence": 1.0,
            "reason": reason,
        })

    def _push_live(self, frame, result, frames: int, fps_started: float, now: float) -> None:
        settings = self.hub.live_settings
        jpeg = None
        streaming = self._streamer is not None and self._streamer.connected  # the Hub already has the picture
        if settings.get("enabled", True) and not streaming:
            width = int(settings.get("width") or 320)
            h, w = frame.shape[:2]
            small = cv2.resize(frame, (width, max(1, int(h * width / max(1, w)))), interpolation=cv2.INTER_AREA) if w > width else frame
            ok, enc = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            jpeg = enc.tobytes() if ok else None
        labels = {"center": "THẲNG", "left": "TRÁI", "right": "PHẢI", "up": "TRÊN", "down": "DƯỚI", "unknown": "—"}
        eye = "—" if not result.face_count else ("ĐANG HỌC" if not result.calibrated else (
            f"NGOÀI • {labels.get(result.eye_direction, '—')}" if result.eyes_outside_zone else "TRONG VÙNG"))
        self.hub.update_live(self._session.session_id, {
            "fps": round(frames / max(0.001, now - fps_started), 1), "eye": eye,
            "head": labels.get(result.head_direction, "—") if result.face_count else "—",
            "people": int(result.detected_people_count), "alerts": len(self._session.events),
            "risk_score": self._session.risk_score, "calibrated": bool(result.calibrated),
            "source_kind": "SIMULATION" if self.simulate else "CAMERA", "frame_count": frames,
        }, jpeg)

    # ------------------------------------------------------------ step 4: exam
    def _start_exam(self) -> None:
        key = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4].upper()
        self._session = SessionInfo(session_id=key, candidate_id=self.candidate.get("code", ""),
                                    exam_name=self.exam.get("name", ""), started_at=datetime.now().isoformat(timespec="seconds"),
                                    source_name="Mô phỏng" if self.simulate else "Camera", exam_id=self.exam.get("id"))
        self.hub.open_session(key, {
            "candidate_ref": self.candidate.get("code", ""), "exam_id": self.exam.get("id"), "source_kind": "SIMULATION" if self.simulate else "CAMERA",
            "simulation": self.simulate, "started_at": _with_offset(self._session.started_at),
            "client_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "analysis": "server" if self._server_analysis() else "client",
        })
        self._started_mono = time.monotonic()
        self._phase = "exam"
        if getattr(self, "_virtual_names", None):
            self._integrity("virtual_camera", "Máy có cài camera ảo: " + ", ".join(self._virtual_names))
        if getattr(self, "_screens", 1) > 1:
            self._screens_flagged = True
            self._integrity("multiple_displays", f"Máy đang nối {self._screens} màn hình khi bắt đầu làm bài.")
        self._show_exam()

    def _show_exam(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        width, height = 340, 330
        x = max(0, self.winfo_screenwidth() - width - 24)
        self.minsize(300, 280)
        self.geometry(f"{width}x{height}+{x}+24")
        self.attributes("-topmost", True)
        frame = tk.Frame(self.body, bg=COLORS["bg"])
        frame.pack(fill="both", expand=True, padx=12, pady=10)
        top = tk.Frame(frame, bg=COLORS["bg"])
        top.pack(fill="x")
        self.rec_dot = tk.Label(top, text="●", bg=COLORS["bg"], fg=COLORS["danger"], font=("Segoe UI", 10))
        self.rec_dot.pack(side="left")
        tk.Label(top, text="vigil one", bg=COLORS["bg"], fg=COLORS["text"], font=brand_font(14)).pack(side="left", padx=4)
        tk.Label(top, text="· ĐANG GIÁM SÁT", bg=COLORS["bg"], fg=COLORS["text"], font=FONTS["tiny"]).pack(side="left")
        self.timer = tk.Label(top, text="00:00", bg=COLORS["bg"], fg=COLORS["text_muted"], font=FONTS["mono"])
        self.timer.pack(side="right")
        self.preview = self._preview_label(frame, 316, 178)
        self.preview.pack(pady=(8, 6))
        self.exam_hint = tk.Label(frame, text="Nhìn vào giữa màn hình vài giây để hiệu chỉnh…", bg=COLORS["bg"],
                                  fg=COLORS["warning"], font=FONTS["small"], wraplength=310, justify="left")
        self.exam_hint.pack(anchor="w")
        self.sync_label = tk.Label(frame, text="", bg=COLORS["bg"], fg=COLORS["text_dim"], font=FONTS["tiny"])
        self.sync_label.pack(anchor="w", pady=(2, 6))
        Button(frame, "NỘP BÀI / KẾT THÚC", self._submit).pack(fill="x")
        # Fit the window to its content (text height depends on the screen's DPI), top-right corner
        self.update_idletasks()
        width, height = max(width, self.body.winfo_reqwidth()), self.body.winfo_reqheight()
        self.geometry(f"{width}x{height}+{max(0, self.winfo_screenwidth() - width - 24)}+24")
        self._tick()

    def _tick(self) -> None:
        if self._phase != "exam":
            return
        elapsed = int(time.monotonic() - self._started_mono)
        self.timer.configure(text=f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        self.after(1000, self._tick)

    def _finish(self, reason: str) -> None:
        """Stop analysing, save locally, close the session on the Hub (SUBMITTED or QUIT)."""
        if self._finished or self._session is None:
            return
        self._finished = True
        self._phase = "done"
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=40)  # the last clips and video are written before the stop is queued
        self._session.ended_at = datetime.now().isoformat(timespec="seconds")
        self.store.save_session(self._session)
        self.hub.stop_session(self._session.session_id, {
            "ended_at": _with_offset(self._session.ended_at), "frame_count": self._session.frame_count,
            "average_fps": round(self._session.average_fps, 2), "reason": reason,
        })

    def _submit(self) -> None:
        if not messagebox.askyesno("Nộp bài", "Kết thúc giám sát và xác nhận bạn đã nộp bài?", parent=self):
            return
        self._finish("SUBMITTED")
        self.attributes("-topmost", False)
        frame = self._clear()
        self._header(frame, "Đã kết thúc", "Cảm ơn bạn. Đang gửi nốt dữ liệu về máy chủ thi…")
        self.done_label = tk.Label(frame, text="", bg=COLORS["bg"], fg=COLORS["text"], font=FONTS["body"],
                                   wraplength=520, justify="left")
        self.done_label.pack(anchor="w")
        self.update_idletasks()
        self.geometry(f"{max(640, self.body.winfo_reqwidth())}x{max(300, self.body.winfo_reqheight())}+120+120")
        self._wait_sent()

    def _wait_sent(self) -> None:
        """Done only when the Hub took everything: nothing waiting and nothing refused."""
        status = self.hub.status()
        retry = getattr(self, "retry_button", None)
        if status.pending == 0 and status.failed == 0:
            self.done_label.configure(text="✓ Máy chủ thi đã nhận đủ dữ liệu. Bạn có thể đóng cửa sổ này.", fg=COLORS["good"])
            if retry is not None:
                retry.pack_forget()
            self._clear_token()
            return
        if status.pending == 0:  # everything was tried; some items were refused
            self.done_label.configure(
                text=(f"✗ {status.failed} mục bị máy chủ thi từ chối: {status.last_error or 'không rõ lý do'}\n"
                      "Đừng xóa phần mềm hay dữ liệu. Báo giám thị, rồi bấm GỬI LẠI khi được yêu cầu."),
                fg=COLORS["danger"])
            if retry is None:
                self.retry_button = retry = Button(self.done_label.master, "GỬI LẠI", self._retry_failed, primary=False)
            if not retry.winfo_ismapped():
                retry.pack(anchor="w", pady=(12, 0))
        else:
            note = "Đang chờ mạng…" if status.state == OFFLINE else "Đang gửi…"
            refused = f" {status.failed} mục bị từ chối." if status.failed else ""
            self.done_label.configure(text=f"{note} Còn {status.pending} mục.{refused} Đừng tắt máy cho tới khi xong.",
                                      fg=COLORS["text"])
        self.after(1000, self._wait_sent)

    def _retry_failed(self) -> None:
        self.hub.retry_failed()
        self.done_label.configure(text="Đang gửi lại…", fg=COLORS["text"])

    def _clear_token(self) -> None:
        self._state_path.unlink(missing_ok=True)

    # ------------------------------------------------------------ UI pump
    def _pump(self) -> None:
        latest = None
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "frame":
                    latest = payload
                elif kind == "joined":
                    self._on_joined(payload)
                elif kind == "join_failed":
                    self.join_button.configure(state="normal", text="VÀO THI  →")
                    self.join_error.configure(text=payload)
                elif kind == "resume":
                    if payload is None:
                        self.hub.configure(self.hub_url, "")
                        self._show_join("Phiên đăng nhập trước đã hết hạn. Hãy vào lại.")
                    else:
                        self.policy = payload
                        self._show_check()
                elif kind == "consented":
                    self.candidate["consent_given"] = True
                    self._save_state()
                    self._show_check()
                elif kind == "consent_failed":
                    self.consent_error.configure(text=payload)
                elif kind == "check":
                    if self._phase == "check":
                        self._render_check(payload)
                elif kind == "camera_lost":
                    messagebox.showerror("Camera", "Không đọc được hình từ camera. Hãy kiểm tra camera rồi mở lại phần mềm.", parent=self)
                elif kind == "sync":
                    self._render_sync(payload)
        except queue.Empty:
            pass
        if latest is not None:
            self._render_frame(*latest)
        self.after(40, self._pump)

    def _render_frame(self, frame, result) -> None:
        if not hasattr(self, "preview") or not self.preview.winfo_exists():
            return
        target_w = 316 if self._phase == "exam" else 480
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(frame, (target_w, int(h * target_w / w))), cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.preview.configure(image=self._photo)
        if self._phase == "exam":
            if not result.face_count:
                hint, color = "Không thấy khuôn mặt — hãy ngồi vào khung hình.", COLORS["danger"]
            elif float(getattr(result, "brightness", 120)) < 55:
                hint, color = "Hình quá tối — hãy bật thêm đèn.", COLORS["warning"]
            elif not result.calibrated and not self.simulate:
                hint, color = "Nhìn vào giữa màn hình vài giây để hiệu chỉnh…", COLORS["warning"]
            else:
                hint, color = "Đang làm bài. Giữ cửa sổ này mở cho tới khi nộp bài.", COLORS["good"]
            self.exam_hint.configure(text=hint, fg=color)

    def _render_sync(self, status) -> None:
        if not hasattr(self, "sync_label") or not self.sync_label.winfo_exists():
            return
        text = {SYNCED: "● Đã kết nối máy chủ thi", OFFLINE: f"● Mất kết nối — {status.pending} mục được giữ lại, sẽ tự gửi",
                UNPAIRED: "● Máy chủ từ chối phiên này — báo giám thị"}.get(status.state, f"● Đang gửi {status.pending} mục…")
        color = {SYNCED: COLORS["good"], OFFLINE: COLORS["danger"], UNPAIRED: COLORS["danger"]}.get(status.state, COLORS["warning"])
        if status.state == SYNCED and self._streamer is not None and not self._streamer.connected:
            text, color = "● Đã kết nối — đang nối lại hình trực tiếp", COLORS["warning"]
        if status.failed:  # the reason, so the proctor can act on it
            text += f"\n{status.failed} mục bị máy chủ từ chối: {status.last_error}"[:160]
            color = COLORS["danger"]
        self.sync_label.configure(text=text, fg=color)

    def _on_close(self) -> None:
        if self._phase == "exam":
            if not messagebox.askyesno(
                "Thoát khi chưa nộp bài?",
                "Bạn chưa bấm Nộp bài. Nếu thoát bây giờ, việc rời phòng thi sẽ được ghi nhận và báo cho giám thị.\n\nVẫn thoát?",
                icon="warning", parent=self,
            ):
                return
            self._finish("QUIT")
        self._stop.set()
        self.hub.drain(timeout=3.0)
        self.destroy()


def run(config: Optional[dict[str, Any]] = None, simulate: bool = False) -> None:
    app = CandidateApp(config or {}, simulate=simulate)
    app.mainloop()
