from __future__ import annotations

import csv
import json
import queue
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import cv2
from PIL import Image, ImageGrab, ImageTk

from .audio import VoiceActivityDetector, speech_is_confirmed
from .engine import GazeAnalyzer, OverlayRenderer
from .events import EventDetector, SessionStore
from .models import EventType, MonitoringEvent, SessionInfo, Severity
from .sources import CameraSource, DemoSource, FrameSource, VideoSource
from .theme import COLORS, FONTS, THEMES, set_theme


APP_TITLE = "VIGIL AI — Hệ thống giám sát thi trực tuyến"
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class Card(tk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            bg=kwargs.pop("bg", COLORS["surface"]),
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            bd=0,
            **kwargs,
        )


class PillButton(tk.Button):
    def __init__(self, master, text, command=None, variant="primary", **kwargs):
        variants = {
            "primary": (COLORS["primary"], COLORS["primary_text"], COLORS["primary_hover"]),
            "secondary": (COLORS["surface_alt"], COLORS["text"], COLORS["surface_hover"]),
            "danger": (COLORS["danger_dim"], COLORS["danger"], COLORS["danger_hover"]),
        }
        bg, fg, active = variants[variant]
        padx = kwargs.pop("padx", 18)
        pady = kwargs.pop("pady", 10)
        super().__init__(
            master,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=fg,
            disabledforeground=COLORS["text_dim"],
            font=FONTS["body_medium"],
            relief="flat",
            bd=0,
            padx=padx,
            pady=pady,
            cursor="hand2",
            highlightthickness=0,
            **kwargs,
        )


class OptionToggle(tk.Checkbutton):
    """A full-row option that avoids the inconsistent native checkbox glyph."""

    def __init__(self, master, text: str, variable: tk.BooleanVar, **kwargs):
        self._label = text
        self._variable = variable
        super().__init__(
            master,
            text=text,
            variable=variable,
            indicatoron=False,
            anchor="w",
            justify="left",
            bg=COLORS["surface_alt"],
            fg=COLORS["text_muted"],
            activebackground=COLORS["surface_hover"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["good_dim"],
            font=FONTS["small"],
            relief="flat",
            offrelief="flat",
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            command=self._sync_label,
            **kwargs,
        )
        self._variable.trace_add("write", self._sync_label)
        self._sync_label()

    def _sync_label(self, *_args) -> None:
        mark = "✓" if self._variable.get() else " "
        self.configure(text=f"{mark}   {self._label}")


class ExamMonitorApp(tk.Tk):
    def __init__(self, auto_demo: bool = False, screenshot_path: str | None = None):
        super().__init__()
        self.title(APP_TITLE)
        self._settings_path = DATA_DIR / "settings.json"
        self.settings = self._load_settings()
        self.theme_name = self.settings.get("theme", "light")
        if self.theme_name not in THEMES:
            self.theme_name = "light"
        set_theme(self.theme_name)
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        initial_width = min(1400, max(1060, screen_width - 120))
        initial_height = min(860, max(700, screen_height - 120))
        self.geometry(f"{initial_width}x{initial_height}+40+30")
        self.minsize(960, 680)
        self.configure(bg=COLORS["bg"])
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.store = SessionStore(DATA_DIR)
        self.detector = EventDetector()
        self.analyzer: GazeAnalyzer | None = None
        self.source: FrameSource | None = None
        self.session: SessionInfo | None = None
        self.last_session: SessionInfo | None = None
        self.last_result = None
        self.last_raw_frame = None
        self.last_display_frame = None
        self.monitoring = False
        self._worker: threading.Thread | None = None
        self._stop_worker = threading.Event()
        self._frame_queue: queue.Queue = queue.Queue(maxsize=2)
        # Control messages must never compete with high-frequency video frames.
        # Keep them on a separate unbounded queue so alerts and camera errors
        # cannot be dropped when rendering falls briefly behind.
        self._message_queue: queue.Queue = queue.Queue()
        self._photo = None
        self._page_name = "dashboard"
        self._video_path: Path | None = None
        self._auto_demo = auto_demo
        self._screenshot_path = screenshot_path
        self._save_evidence_enabled = True
        self._show_landmarks_enabled = True
        self._audio_confirmation_enabled = True
        self._fps = 0.0
        self._session_started_monotonic = 0.0
        self._toast_after_id = None
        self._alert_toast_until = 0.0
        self._alert_toast_priority = -1
        self._presentation_mode = False

        self._setup_ttk()
        self._build_shell()
        self._sidebar_compact = None
        self._vertical_compact = None
        self.bind("<Configure>", self._handle_root_resize)
        self.bind("<Configure>", self._handle_vertical_resize, add="+")
        self.bind("<F11>", self._toggle_presentation)
        self.bind("<Escape>", self._exit_presentation)
        self._show_page("dashboard")
        self.after(35, self._poll_frames)
        self.after(700, self._tick_clock)
        if auto_demo:
            self.after(650, lambda: self._show_page("monitor"))
            self.after(900, self.start_session)
        if screenshot_path:
            self.after(5200, self._capture_screenshot)

    def _setup_ttk(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Vigil.TCombobox",
            fieldbackground=COLORS["surface_alt"],
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["text_muted"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=8,
            font=FONTS["body"],
        )
        style.map(
            "Vigil.TCombobox",
            fieldbackground=[("readonly", COLORS["surface_alt"])],
            foreground=[("readonly", COLORS["text"])],
            selectbackground=[("readonly", COLORS["surface_alt"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "Vigil.Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            rowheight=38,
            borderwidth=0,
            font=FONTS["body"],
        )
        style.configure(
            "Vigil.Treeview.Heading",
            background=COLORS["surface_alt"],
            foreground=COLORS["text_muted"],
            relief="flat",
            borderwidth=0,
            padding=10,
            font=FONTS["tiny"],
        )
        style.map(
            "Vigil.Treeview",
            background=[("selected", COLORS["surface_hover"])],
            foreground=[("selected", COLORS["text"])],
        )
        style.configure(
            "Vigil.Horizontal.TScale",
            background=COLORS["surface"],
            troughcolor=COLORS["surface_hover"],
            bordercolor=COLORS["surface"],
            lightcolor=COLORS["primary"],
            darkcolor=COLORS["primary"],
        )

    def _build_shell(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.sidebar = tk.Frame(
            self,
            bg=COLORS["sidebar"],
            width=204,
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.main = tk.Frame(self, bg=COLORS["bg"])
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_rowconfigure(1, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        self._build_sidebar()
        self._build_header()
        self.content = tk.Frame(self.main, bg=COLORS["bg"])
        self.content.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.pages = {
            "dashboard": self._build_dashboard_page(),
            "monitor": self._build_monitor_page(),
            "events": self._build_events_page(),
            "report": self._build_report_page(),
            "settings": self._build_settings_page(),
        }
        self.toast = tk.Label(
            self.main,
            text="",
            bg=COLORS["primary"],
            fg=COLORS["primary_text"],
            font=FONTS["body_medium"],
            padx=18,
            pady=10,
        )

    def _build_sidebar(self) -> None:
        brand = tk.Frame(self.sidebar, bg=COLORS["sidebar"])
        self.sidebar_brand = brand
        brand.pack(fill="x", padx=18, pady=(20, 24))
        mark = tk.Label(
            brand,
            text="V",
            bg=COLORS["primary"],
            fg=COLORS["primary_text"],
            font=("Segoe UI Black", 14),
            width=2,
            height=1,
        )
        mark.pack(side="left")
        names = tk.Frame(brand, bg=COLORS["sidebar"])
        self.sidebar_brand_names = names
        names.pack(side="left", padx=(10, 0))
        tk.Label(names, text="VIGIL AI", bg=COLORS["sidebar"], fg=COLORS["text"], font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(names, text="LOCAL PROCTOR", bg=COLORS["sidebar"], fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(anchor="w")

        section = tk.Label(
            self.sidebar,
            text="KHÔNG GIAN LÀM VIỆC",
            bg=COLORS["sidebar"],
            fg=COLORS["text_dim"],
            font=FONTS["tiny"],
        )
        self.sidebar_section = section
        section.pack(anchor="w", padx=22, pady=(0, 8))

        self.nav_buttons = {}
        self.nav_specs = {}
        nav_items = [
            ("dashboard", "Tổng quan", "01"),
            ("monitor", "Giám sát trực tiếp", "02"),
            ("events", "Dòng sự kiện", "03"),
            ("report", "Báo cáo phiên thi", "04"),
            ("settings", "Cài đặt", "05"),
        ]
        for key, label, number in nav_items:
            button = tk.Button(
                self.sidebar,
                text=f"{number}     {label}",
                anchor="w",
                bg=COLORS["sidebar"],
                fg=COLORS["text_muted"],
                activebackground=COLORS["surface_alt"],
                activeforeground=COLORS["text"],
                relief="flat",
                bd=0,
                highlightthickness=0,
                font=FONTS["body"],
                padx=18,
                pady=10,
                cursor="hand2",
                command=lambda page=key: self._show_page(page),
            )
            button.pack(fill="x", padx=8, pady=2)
            self.nav_buttons[key] = button
            self.nav_specs[key] = (label, number)

        bottom = tk.Frame(self.sidebar, bg=COLORS["sidebar"])
        self.sidebar_bottom = bottom
        bottom.pack(side="bottom", fill="x", padx=18, pady=18)
        privacy = tk.Frame(bottom, bg=COLORS["sidebar"])
        self.sidebar_privacy = privacy
        privacy.pack(fill="x")
        tk.Label(privacy, text="●  LOCAL / PRIVATE", bg=COLORS["sidebar"], fg=COLORS["good"], font=FONTS["tiny"]).pack(anchor="w")
        tk.Label(
            privacy,
            text="Dữ liệu chỉ lưu trên máy",
            justify="left",
            bg=COLORS["sidebar"],
            fg=COLORS["text_dim"],
            font=FONTS["tiny"],
        ).pack(anchor="w", pady=(4, 0))

    def _build_header(self) -> None:
        self.header = tk.Frame(self.main, bg=COLORS["bg"], height=90)
        self.header.grid(row=0, column=0, sticky="ew", padx=20, pady=(0, 0))
        self.header.grid_propagate(False)
        self.header.grid_columnconfigure(0, weight=1)
        title_wrap = tk.Frame(self.header, bg=COLORS["bg"])
        title_wrap.grid(row=0, column=0, sticky="w", pady=(15, 0))
        self.page_title = tk.Label(title_wrap, text="Tổng quan", bg=COLORS["bg"], fg=COLORS["text"], font=FONTS["h1"])
        self.page_title.pack(anchor="w")
        self.page_subtitle = tk.Label(
            title_wrap,
            text="Trung tâm điều khiển phiên giám sát cục bộ",
            bg=COLORS["bg"],
            fg=COLORS["text_muted"],
            font=FONTS["small"],
        )
        self.page_subtitle.pack(anchor="w", pady=(3, 0))
        status_wrap = tk.Frame(self.header, bg=COLORS["bg"])
        status_wrap.grid(row=0, column=1, sticky="e", pady=(19, 0))
        self.global_status_dot = tk.Label(status_wrap, text="●", bg=COLORS["bg"], fg=COLORS["text_dim"], font=("Segoe UI", 9))
        self.global_status_dot.pack(side="left")
        self.global_status = tk.Label(status_wrap, text="Sẵn sàng", bg=COLORS["bg"], fg=COLORS["text_muted"], font=FONTS["small"])
        self.global_status.pack(side="left", padx=(5, 14))
        self.clock_label = tk.Label(status_wrap, text="--:--:--", bg=COLORS["bg"], fg=COLORS["text"], font=FONTS["mono"])
        self.clock_label.pack(side="left")
        tk.Frame(status_wrap, width=1, height=26, bg=COLORS["border"]).pack(side="left", padx=12)
        self.theme_button = PillButton(
            status_wrap,
            text="CHẾ ĐỘ TỐI" if self.theme_name == "light" else "CHẾ ĐỘ SÁNG",
            variant="secondary",
            padx=11,
            pady=6,
            command=self._toggle_theme,
        )
        self.theme_button.pack(side="left", padx=(0, 8))
        self.presentation_button = PillButton(
            status_wrap,
            text="TRÌNH CHIẾU",
            variant="secondary",
            padx=11,
            pady=6,
            command=self._toggle_presentation,
        )
        self.presentation_button.pack(side="left")

    def _new_page(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=COLORS["bg"])
        page.grid(row=0, column=0, sticky="nsew")
        return page

    def _handle_root_resize(self, event) -> None:
        if event.widget is not self:
            return
        compact = event.width < 1120
        if compact == self._sidebar_compact:
            return
        self._sidebar_compact = compact
        if compact:
            self.sidebar.configure(width=68)
            self.sidebar_brand.pack_configure(padx=8, pady=(20, 22))
            self.sidebar_brand_names.pack_forget()
            self.sidebar_section.pack_forget()
            self.sidebar_bottom.pack_forget()
            for key, button in self.nav_buttons.items():
                _, number = self.nav_specs[key]
                button.configure(text=number, anchor="center", padx=0)
            self.theme_button.configure(text="TỐI" if self.theme_name == "light" else "SÁNG")
            self.presentation_button.configure(text="F11" if not self._presentation_mode else "THOÁT")
        else:
            self.sidebar.configure(width=204)
            self.sidebar_brand.pack_configure(padx=18, pady=(20, 24))
            if not self.sidebar_brand_names.winfo_manager():
                self.sidebar_brand_names.pack(side="left", padx=(10, 0))
            if not self.sidebar_section.winfo_manager():
                self.sidebar_section.pack(
                    anchor="w",
                    padx=22,
                    pady=(0, 8),
                    before=self.nav_buttons["dashboard"],
                )
            if not self.sidebar_bottom.winfo_manager():
                self.sidebar_bottom.pack(side="bottom", fill="x", padx=18, pady=18)
            for key, button in self.nav_buttons.items():
                label, number = self.nav_specs[key]
                button.configure(text=f"{number}     {label}", anchor="w", padx=18)
            self.theme_button.configure(
                text="CHẾ ĐỘ TỐI" if self.theme_name == "light" else "CHẾ ĐỘ SÁNG"
            )
            self.presentation_button.configure(
                text="TRÌNH CHIẾU" if not self._presentation_mode else "THOÁT TRÌNH CHIẾU"
            )

    def _handle_vertical_resize(self, event) -> None:
        if event.widget is not self:
            return
        compact = event.height < 760
        if compact == self._vertical_compact:
            return
        self._vertical_compact = compact
        if compact:
            self.session_status_card.grid_remove()
        else:
            self.session_status_card.grid()
            if not self.session_status_heading.winfo_manager():
                self.session_status_heading.pack(
                    anchor="w",
                    padx=12,
                    pady=(10, 4),
                    before=self.session_status,
                )
            self.session_status_card.grid_configure(pady=8)
            self.session_status.pack_configure(pady=(0, 11))

    def _toggle_theme(self) -> None:
        old_colors = dict(COLORS)
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        set_theme(self.theme_name)
        self.settings["theme"] = self.theme_name
        self._save_settings()
        self._apply_theme_to_widget(self, old_colors)
        self._setup_ttk()
        self._show_page(self._page_name)
        if isinstance(self.source, DemoSource):
            calibration_progress = 1.0
        elif self.analyzer:
            calibration_progress = self.analyzer.calibration_progress
        else:
            calibration_progress = 0.0
        self._draw_progress(self.calibration_bar, calibration_progress)
        self._draw_report_chart()
        if self.last_display_frame is not None:
            self._render_video(self.last_display_frame)
        compact = self.winfo_width() < 1120
        self.theme_button.configure(
            text=("TỐI" if self.theme_name == "light" else "SÁNG")
            if compact
            else ("CHẾ ĐỘ TỐI" if self.theme_name == "light" else "CHẾ ĐỘ SÁNG")
        )

    def _apply_theme_to_widget(self, widget: tk.Misc, old_colors: dict[str, str]) -> None:
        replacements = {
            value.lower(): COLORS[key]
            for key, value in old_colors.items()
            if key in COLORS and isinstance(value, str)
        }
        options = (
            "background",
            "foreground",
            "activebackground",
            "activeforeground",
            "disabledbackground",
            "disabledforeground",
            "highlightbackground",
            "highlightcolor",
            "insertbackground",
            "readonlybackground",
            "selectbackground",
            "selectforeground",
            "selectcolor",
            "troughcolor",
        )
        updates = {}
        for option in options:
            try:
                current = widget.cget(option)
            except tk.TclError:
                continue
            if isinstance(current, str) and current.lower() in replacements:
                updates[option] = replacements[current.lower()]
        if updates:
            try:
                widget.configure(**updates)
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            self._apply_theme_to_widget(child, old_colors)

    def _toggle_presentation(self, _event=None) -> None:
        self._presentation_mode = not self._presentation_mode
        self.attributes("-fullscreen", self._presentation_mode)
        if self._presentation_mode:
            self.sidebar.grid_remove()
            self.content.grid_configure(padx=14, pady=(0, 14))
            self.presentation_button.configure(text="THOÁT TRÌNH CHIẾU")
        else:
            self.sidebar.grid()
            self.content.grid_configure(padx=20, pady=(0, 20))
            self.presentation_button.configure(text="TRÌNH CHIẾU")
        self.after_idle(lambda: self._handle_root_resize_forced())

    def _handle_root_resize_forced(self) -> None:
        self._sidebar_compact = None
        self._vertical_compact = None
        event = type(
            "ResizeEvent",
            (),
            {"widget": self, "width": self.winfo_width(), "height": self.winfo_height()},
        )()
        self._handle_root_resize(event)
        self._handle_vertical_resize(event)

    def _exit_presentation(self, _event=None) -> None:
        if self._presentation_mode:
            self._toggle_presentation()

    def _build_dashboard_page(self) -> tk.Frame:
        page = self._new_page()
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_columnconfigure(2, weight=1)

        stats = tk.Frame(page, bg=COLORS["bg"])
        stats.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 16))
        for idx in range(4):
            stats.grid_columnconfigure(idx, weight=1)
        self.dashboard_values = {}
        for idx, (key, label, value, accent) in enumerate(
            [
                ("sessions", "PHIÊN ĐÃ LƯU", "0", COLORS["text"]),
                ("events", "CẢNH BÁO GẦN NHẤT", "0", COLORS["warning"]),
                ("risk", "ĐIỂM RỦI RO", "00", COLORS["good"]),
                ("mode", "CHẾ ĐỘ XỬ LÝ", "LOCAL", COLORS["text"]),
            ]
        ):
            card = Card(stats)
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0 if idx == 3 else 6))
            tk.Label(card, text=label, bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(anchor="w", padx=16, pady=(14, 7))
            value_label = tk.Label(card, text=value, bg=COLORS["surface"], fg=accent, font=("Segoe UI Semibold", 22))
            value_label.pack(anchor="w", padx=16, pady=(0, 14))
            self.dashboard_values[key] = value_label

        hero = Card(page)
        hero.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=(0, 8))
        hero.grid_rowconfigure(2, weight=1)
        hero.grid_columnconfigure(0, weight=1)
        tk.Label(hero, text="BẮT ĐẦU PHIÊN GIÁM SÁT", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).grid(row=0, column=0, sticky="w", padx=22, pady=(20, 8))
        tk.Label(
            hero,
            text="Theo dõi ánh nhìn.\nGiải thích từng cảnh báo.",
            justify="left",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=FONTS["display"],
        ).grid(row=1, column=0, sticky="nw", padx=22)
        tk.Label(
            hero,
            text="Camera, video hoặc chế độ mô phỏng đều chạy hoàn toàn trên máy.\nKết quả chỉ là tín hiệu hỗ trợ giám thị xem lại.",
            justify="left",
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=FONTS["body"],
        ).grid(row=2, column=0, sticky="nw", padx=22, pady=(16, 20))
        action = PillButton(hero, text="MỞ PHÒNG GIÁM SÁT  →", command=lambda: self._show_page("monitor"))
        action.grid(row=3, column=0, sticky="w", padx=22, pady=(0, 22))

        recent = Card(page)
        recent.grid(row=1, column=2, sticky="nsew", padx=(8, 0))
        recent.grid_columnconfigure(0, weight=1)
        tk.Label(recent, text="HOẠT ĐỘNG GẦN ĐÂY", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 12))
        self.dashboard_recent = tk.Frame(recent, bg=COLORS["surface"])
        self.dashboard_recent.grid(row=1, column=0, sticky="nsew", padx=18)
        self.dashboard_empty = tk.Label(
            self.dashboard_recent,
            text="Chưa có phiên giám sát nào.\nBắt đầu bằng chế độ mô phỏng để xem thử.",
            justify="left",
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=FONTS["small"],
        )
        self.dashboard_empty.pack(anchor="w", pady=8)
        demo = PillButton(recent, text="CHẠY DEMO MÔ PHỎNG", variant="secondary", command=self._quick_demo)
        demo.grid(row=2, column=0, sticky="ew", padx=18, pady=18)
        return page

    def _build_monitor_page(self) -> tk.Frame:
        page = self._new_page()
        page.grid_rowconfigure(0, weight=1)
        page.grid_columnconfigure(0, weight=1, minsize=440)
        page.grid_columnconfigure(1, weight=0, minsize=280)

        viewer_card = Card(page)
        viewer_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        viewer_card.grid_rowconfigure(1, weight=1)
        viewer_card.grid_columnconfigure(0, weight=1)
        viewer_header = tk.Frame(viewer_card, bg=COLORS["surface"])
        viewer_header.grid(row=0, column=0, sticky="ew", padx=14, pady=10)
        self.live_dot = tk.Label(viewer_header, text="●", bg=COLORS["surface"], fg=COLORS["text_dim"], font=("Segoe UI", 9))
        self.live_dot.pack(side="left")
        self.live_title = tk.Label(viewer_header, text="CAMERA CHƯA KHỞI ĐỘNG", bg=COLORS["surface"], fg=COLORS["text_muted"], font=FONTS["tiny"])
        self.live_title.pack(side="left", padx=(6, 0))
        self.fps_label = tk.Label(viewer_header, text="0.0 FPS", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["mono"])
        self.fps_label.pack(side="right")
        self.mic_label = tk.Label(viewer_header, text="MIC —", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["mono"])
        self.mic_label.pack(side="right", padx=(0, 14))

        self.video_frame = tk.Frame(viewer_card, bg=COLORS["video_bg"])
        self.video_frame.grid(row=1, column=0, sticky="nsew", padx=8)
        self.video_frame.grid_rowconfigure(0, weight=1)
        self.video_frame.grid_columnconfigure(0, weight=1)
        # The PhotoImage changes size on every resize. Do not let its requested
        # dimensions push the control panel outside the window.
        self.video_frame.grid_propagate(False)
        self.video_label = tk.Label(
            self.video_frame,
            text="CAMERA CHƯA KHỞI ĐỘNG\n\nChọn nguồn hình ảnh ở bảng điều khiển\nrồi nhấn Bắt đầu giám sát",
            bg=COLORS["video_bg"],
            fg=COLORS["text_dim"],
            font=FONTS["body"],
            justify="center",
        )
        self.video_label.grid(row=0, column=0, sticky="nsew")

        metrics = tk.Frame(viewer_card, bg=COLORS["surface"])
        metrics.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        for idx in range(4):
            metrics.grid_columnconfigure(idx, weight=1)
        self.metric_labels = {}
        for idx, (key, label) in enumerate(
            [("eye", "MẮT"), ("head", "ĐẦU"), ("faces", "NGƯỜI"), ("events", "CẢNH BÁO")]
        ):
            cell = tk.Frame(metrics, bg=COLORS["surface_alt"])
            cell.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 3, 0 if idx == 3 else 3))
            tk.Label(cell, text=label, bg=COLORS["surface_alt"], fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(anchor="w", padx=10, pady=(8, 2))
            value = tk.Label(cell, text="—", bg=COLORS["surface_alt"], fg=COLORS["text"], font=FONTS["h2"])
            value.pack(anchor="w", padx=10, pady=(0, 8))
            self.metric_labels[key] = value

        control = Card(page, width=300)
        control.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        control.grid_propagate(False)
        control.grid_columnconfigure(0, weight=1)
        tk.Label(control, text="THIẾT LẬP PHIÊN", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
        self.candidate_entry = self._labeled_entry(control, "Mã thí sinh", "SV-2026-001", 1)
        self.exam_entry = self._labeled_entry(control, "Tên kỳ thi", "Kỳ thi thử nghiệm", 2)

        source_wrap = tk.Frame(control, bg=COLORS["surface"])
        source_wrap.grid(row=3, column=0, sticky="ew", padx=14, pady=(2, 7))
        source_wrap.grid_columnconfigure(0, weight=1)
        tk.Label(source_wrap, text="Nguồn hình ảnh", bg=COLORS["surface"], fg=COLORS["text_muted"], font=FONTS["small"]).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.source_var = tk.StringVar(value="Mô phỏng có sẵn")
        self.source_combo = ttk.Combobox(
            source_wrap,
            textvariable=self.source_var,
            values=("Mô phỏng có sẵn", "Camera trực tiếp", "Video từ máy"),
            state="readonly",
            style="Vigil.TCombobox",
        )
        self.source_combo.grid(row=1, column=0, sticky="ew")
        self.source_combo.bind("<<ComboboxSelected>>", self._source_changed)
        self.video_picker = PillButton(source_wrap, text="CHỌN VIDEO", variant="secondary", command=self._pick_video)

        calibration = tk.Frame(control, bg=COLORS["surface"])
        calibration.grid(row=4, column=0, sticky="ew", padx=14, pady=(7, 4))
        calibration.grid_columnconfigure(0, weight=1)
        tk.Label(calibration, text="VÙNG NHÌN AN TOÀN", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).grid(row=0, column=0, sticky="w")
        self.calibration_text = tk.Label(calibration, text="Tự động khi bắt đầu", bg=COLORS["surface"], fg=COLORS["text_muted"], font=FONTS["small"])
        self.calibration_text.grid(row=1, column=0, sticky="w", pady=(5, 6))
        self.calibration_bar = tk.Canvas(calibration, height=4, bg=COLORS["surface_hover"], highlightthickness=0)
        self.calibration_bar.grid(row=2, column=0, sticky="ew")

        options = tk.Frame(control, bg=COLORS["surface"])
        options.grid(row=5, column=0, sticky="ew", padx=14, pady=7)
        self.landmark_var = tk.BooleanVar(value=False)
        self.save_evidence_var = tk.BooleanVar(value=True)
        self.audio_confirmation_var = tk.BooleanVar(value=True)
        for text, var in (
            ("Hiện lưới khuôn mặt", self.landmark_var),
            ("Microphone xác nhận giọng nói", self.audio_confirmation_var),
            ("Lưu ảnh bằng chứng", self.save_evidence_var),
        ):
            OptionToggle(options, text=text, variable=var).pack(fill="x", pady=3)

        tk.Frame(control, bg=COLORS["border"], height=1).grid(row=6, column=0, sticky="ew", padx=14, pady=4)
        self.session_status_card = tk.Frame(control, bg=COLORS["surface_alt"])
        self.session_status_card.grid(row=7, column=0, sticky="new", padx=14, pady=8)
        self.session_status_heading = tk.Label(
            self.session_status_card,
            text="TRẠNG THÁI HỆ THỐNG",
            bg=COLORS["surface_alt"],
            fg=COLORS["text_dim"],
            font=FONTS["tiny"],
        )
        self.session_status_heading.pack(anchor="w", padx=12, pady=(10, 4))
        self.session_status = tk.Label(
            self.session_status_card,
            text="Hệ thống đang sẵn sàng.",
            wraplength=244,
            justify="left",
            anchor="nw",
            bg=COLORS["surface_alt"],
            fg=COLORS["text_muted"],
            font=FONTS["small"],
            padx=12,
            pady=0,
        )
        self.session_status.pack(fill="x", pady=(0, 11))

        actions = tk.Frame(control, bg=COLORS["surface"])
        actions.grid(row=8, column=0, sticky="sew", padx=14, pady=(4, 14))
        actions.grid_columnconfigure(0, weight=1)
        self.start_button = PillButton(actions, text="BẮT ĐẦU GIÁM SÁT", command=self.start_session)
        self.start_button.grid(row=0, column=0, sticky="ew")
        self.stop_button = PillButton(actions, text="KẾT THÚC PHIÊN", variant="danger", command=self.stop_session, state="disabled")
        control.grid_rowconfigure(7, weight=1)
        return page

    def _labeled_entry(self, parent, label: str, initial: str, row: int) -> tk.Entry:
        wrap = tk.Frame(parent, bg=COLORS["surface"])
        wrap.grid(row=row, column=0, sticky="ew", padx=14, pady=(2, 7))
        wrap.grid_columnconfigure(0, weight=1)
        tk.Label(wrap, text=label, bg=COLORS["surface"], fg=COLORS["text_muted"], font=FONTS["small"]).grid(row=0, column=0, sticky="w", pady=(0, 5))
        entry = tk.Entry(
            wrap,
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["surface_hover"],
            disabledbackground=COLORS["surface_alt"],
            disabledforeground=COLORS["text_dim"],
            readonlybackground=COLORS["surface_alt"],
            relief="flat",
            bd=0,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["text_muted"],
            highlightthickness=1,
            font=FONTS["body"],
        )
        entry.insert(0, initial)
        entry.grid(row=1, column=0, sticky="ew", ipady=9)
        return entry

    def _build_events_page(self) -> tk.Frame:
        page = self._new_page()
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)
        tools = tk.Frame(page, bg=COLORS["bg"])
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        tk.Label(tools, text="Tất cả sự kiện được sinh theo ngưỡng thời gian và cần giám thị xem lại.", bg=COLORS["bg"], fg=COLORS["text_muted"], font=FONTS["small"]).pack(side="left")
        PillButton(tools, text="ĐÁNH DẤU ĐÃ XEM", variant="secondary", command=self._review_selected_event).pack(side="right")

        card = Card(page)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)
        columns = ("time", "type", "severity", "duration", "confidence", "review")
        self.event_tree = ttk.Treeview(card, columns=columns, show="headings", style="Vigil.Treeview")
        headers = {
            "time": "THỜI ĐIỂM",
            "type": "LOẠI SỰ KIỆN",
            "severity": "MỨC ĐỘ",
            "duration": "THỜI LƯỢNG",
            "confidence": "TIN CẬY",
            "review": "TRẠNG THÁI",
        }
        widths = {"time": 145, "type": 260, "severity": 110, "duration": 110, "confidence": 100, "review": 120}
        for key in columns:
            self.event_tree.heading(key, text=headers[key])
            self.event_tree.column(key, width=widths[key], minwidth=80, anchor="w", stretch=(key == "type"))
        scrollbar = ttk.Scrollbar(card, orient="vertical", command=self.event_tree.yview)
        self.event_tree.configure(yscrollcommand=scrollbar.set)
        self.event_tree.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.event_tree.bind("<Double-1>", lambda _event: self._review_selected_event())
        return page

    def _build_report_page(self) -> tk.Frame:
        page = self._new_page()
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)
        top = tk.Frame(page, bg=COLORS["bg"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.report_session_label = tk.Label(top, text="Chưa có phiên hoàn tất", bg=COLORS["bg"], fg=COLORS["text_muted"], font=FONTS["small"])
        self.report_session_label.pack(side="left")
        PillButton(top, text="XUẤT CSV", variant="secondary", command=self._export_csv).pack(side="right", padx=(8, 0))
        PillButton(top, text="XUẤT JSON", variant="secondary", command=self._export_json).pack(side="right")

        body = tk.Frame(page, bg=COLORS["bg"])
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=2)
        summary = Card(body)
        summary.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12))
        self.report_risk = tk.Label(summary, text="00", bg=COLORS["surface"], fg=COLORS["good"], font=("Segoe UI Semibold", 38))
        self.report_risk.pack(anchor="w", padx=20, pady=(18, 0))
        tk.Label(summary, text="ĐIỂM RỦI RO / 100", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(anchor="w", padx=20)
        self.report_meta = tk.Label(summary, text="Chưa có dữ liệu", justify="left", bg=COLORS["surface"], fg=COLORS["text_muted"], font=FONTS["small"])
        self.report_meta.pack(anchor="w", padx=20, pady=(14, 18))

        chart = Card(body)
        chart.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 12))
        tk.Label(chart, text="PHÂN BỐ SỰ KIỆN", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(anchor="w", padx=18, pady=(16, 4))
        self.report_canvas = tk.Canvas(chart, height=155, bg=COLORS["surface"], highlightthickness=0)
        self.report_canvas.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.report_canvas.bind("<Configure>", lambda _event: self._draw_report_chart())

        details = Card(body)
        details.grid(row=1, column=0, columnspan=2, sticky="nsew")
        details.grid_rowconfigure(1, weight=1)
        details.grid_columnconfigure(0, weight=1)
        tk.Label(details, text="NHẬN ĐỊNH HỆ THỐNG", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 6))
        self.report_text = tk.Text(
            details,
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            insertbackground=COLORS["text"],
            relief="flat",
            bd=0,
            wrap="word",
            font=FONTS["body"],
            padx=18,
            pady=8,
            height=8,
        )
        self.report_text.grid(row=1, column=0, sticky="nsew")
        self.report_text.insert("1.0", "Hoàn tất một phiên giám sát để xem tóm tắt.\n\nLưu ý: cảnh báo là tín hiệu hỗ trợ và không tự động kết luận gian lận.")
        self.report_text.configure(state="disabled")
        return page

    def _build_settings_page(self) -> tk.Frame:
        page = self._new_page()
        page.grid_columnconfigure(0, weight=1)
        card = Card(page)
        card.grid(row=0, column=0, sticky="new")
        card.grid_columnconfigure(1, weight=1)
        tk.Label(card, text="NGƯỠNG PHÁT HIỆN", bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["tiny"]).grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(15, 10))
        self.setting_vars = {}
        settings = [
            (EventType.LOOK_AWAY, "Mắt ra ngoài vùng nhìn an toàn", 0.4, 4.0),
            (EventType.HEAD_TURN, "Quay đầu khỏi màn hình", 0.4, 4.0),
            (EventType.TALKING, "Giọng nói + chuyển động môi", 0.3, 5.0),
            (EventType.NO_FACE, "Không thấy khuôn mặt", 1.0, 6.0),
            (EventType.MULTIPLE_FACES, "Từ 2 người trong khung hình", 0.2, 3.0),
            (EventType.SUSPICIOUS_OBJECT, "Điện thoại hoặc sách/tài liệu", 0.3, 4.0),
            (EventType.LOW_LIGHT, "Ánh sáng kém", 2.0, 10.0),
        ]
        for row, (event_type, label, low, high) in enumerate(settings, start=1):
            tk.Label(card, text=label, bg=COLORS["surface"], fg=COLORS["text"], font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=(20, 16), pady=10)
            value = self.settings.get(event_type.value, self.detector.rules[event_type].threshold_seconds)
            var = tk.DoubleVar(value=value)
            self.setting_vars[event_type] = var
            scale = tk.Scale(
                card,
                from_=low,
                to=high,
                resolution=0.1,
                orient="horizontal",
                variable=var,
                command=lambda _value, e=event_type: self._setting_changed(e),
                showvalue=False,
                bg=COLORS["surface"],
                fg=COLORS["text"],
                activebackground=COLORS["primary"],
                troughcolor=COLORS["surface_hover"],
                highlightthickness=0,
                bd=0,
                relief="flat",
                sliderrelief="flat",
                sliderlength=12,
                width=5,
            )
            scale.grid(row=row, column=1, sticky="ew", pady=10)
            label_value = tk.Label(card, text=f"{value:.1f} giây", bg=COLORS["surface"], fg=COLORS["text_muted"], font=FONTS["mono"], width=10)
            label_value.grid(row=row, column=2, sticky="e", padx=20, pady=10)
            var.trace_add("write", lambda *_args, v=var, target=label_value: target.configure(text=f"{v.get():.1f} giây"))

        note = Card(page)
        note.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        tk.Label(note, text="NGUYÊN TẮC SỬ DỤNG", bg=COLORS["surface"], fg=COLORS["warning"], font=FONTS["tiny"]).pack(anchor="w", padx=20, pady=(17, 7))
        tk.Label(
            note,
            text="Ngưỡng chỉ phục vụ bản demo. Hệ thống không dùng hướng nhìn như bằng chứng duy nhất và không tự động kết luận gian lận.\nDữ liệu sinh trắc học cần được quản lý theo chính sách của đơn vị tổ chức thi.",
            justify="left",
            anchor="w",
            wraplength=790,
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=FONTS["body"],
        ).pack(anchor="w", padx=20, pady=(0, 18))
        return page

    def _show_page(self, name: str) -> None:
        self._page_name = name
        titles = {
            "dashboard": ("Tổng quan", "Trung tâm điều khiển phiên giám sát cục bộ"),
            "monitor": ("Giám sát trực tiếp", "Mắt, hướng đầu và các tín hiệu rủi ro theo thời gian thực"),
            "events": ("Dòng sự kiện", "Bằng chứng có thể giải thích và quy trình giám thị xem lại"),
            "report": ("Báo cáo phiên thi", "Tóm tắt rủi ro, sự kiện và dữ liệu xuất cục bộ"),
            "settings": ("Cài đặt", "Điều chỉnh ngưỡng phát hiện cho môi trường demo"),
        }
        title, subtitle = titles[name]
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)
        self.pages[name].tkraise()
        for key, button in self.nav_buttons.items():
            selected = key == name
            button.configure(
                bg=COLORS["surface_alt"] if selected else COLORS["sidebar"],
                fg=COLORS["text"] if selected else COLORS["text_muted"],
                font=FONTS["body_medium"] if selected else FONTS["body"],
            )
        if name == "dashboard":
            self._refresh_dashboard()
        elif name == "events":
            self._refresh_events()
        elif name == "report":
            self._refresh_report()

    def _quick_demo(self) -> None:
        self.source_var.set("Mô phỏng có sẵn")
        self._show_page("monitor")
        if not self.monitoring:
            self.after(150, self.start_session)

    def _source_changed(self, _event=None) -> None:
        if self.source_var.get() == "Video từ máy":
            self.video_picker.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        else:
            self.video_picker.grid_forget()

    def _pick_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn video phiên thi",
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"), ("Tất cả tệp", "*.*")],
        )
        if path:
            self._video_path = Path(path)
            self.video_picker.configure(text=self._video_path.name[:28])

    def start_session(self) -> None:
        if self.monitoring:
            return
        if self._worker and self._worker.is_alive():
            messagebox.showwarning(
                "Đang đóng phiên trước",
                "Bộ phân tích đang giải phóng camera. Hãy thử lại sau một giây.",
                parent=self,
            )
            return
        candidate = self.candidate_entry.get().strip() or "SV-DEMO-001"
        exam = self.exam_entry.get().strip() or "Kỳ thi thử nghiệm"
        source_label = self.source_var.get()
        try:
            if source_label == "Camera trực tiếp":
                source: FrameSource = CameraSource(0)
            elif source_label == "Video từ máy":
                if not self._video_path:
                    self._pick_video()
                if not self._video_path:
                    return
                source = VideoSource(self._video_path)
            else:
                source = DemoSource()
        except Exception as exc:
            messagebox.showerror("Không thể mở nguồn hình ảnh", str(exc), parent=self)
            return

        if hasattr(source, "capture") and not source.capture.isOpened():
            source.release()
            messagebox.showerror("Không thể mở nguồn hình ảnh", "Hãy kiểm tra quyền camera hoặc chọn một video khác.", parent=self)
            return

        self.source = source
        self._save_evidence_enabled = bool(self.save_evidence_var.get())
        self._show_landmarks_enabled = bool(self.landmark_var.get())
        self._audio_confirmation_enabled = bool(self.audio_confirmation_var.get())
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4].upper()
        self.session = SessionInfo(
            session_id=session_id,
            candidate_id=candidate,
            exam_name=exam,
            started_at=datetime.now().isoformat(timespec="seconds"),
            source_name=source.label,
        )
        self.detector.reset()
        self._alert_toast_until = 0.0
        self._alert_toast_priority = -1
        self._drain_worker_queues()
        for event_type, var in self.setting_vars.items():
            self.detector.set_threshold(event_type, var.get())
        self.monitoring = True
        self._session_started_monotonic = time.monotonic()
        self._stop_worker.clear()
        self.start_button.configure(state="disabled")
        self.start_button.configure(text="ĐANG GIÁM SÁT…")
        self.start_button.grid_remove()
        self.stop_button.configure(state="normal")
        self.stop_button.grid(row=0, column=0, sticky="ew")
        self.source_combo.configure(state="disabled")
        self.candidate_entry.configure(state="disabled")
        self.exam_entry.configure(state="disabled")
        self.global_status_dot.configure(fg=COLORS["good"])
        self.global_status.configure(text="Đang giám sát", fg=COLORS["text"])
        self.live_dot.configure(fg=COLORS["danger"])
        self.live_title.configure(text=f"LIVE  •  {candidate}", fg=COLORS["text"])
        self.session_status.configure(text="Đang khởi tạo bộ phân tích cục bộ…", fg=COLORS["text"])
        self._worker = threading.Thread(target=self._monitoring_loop, daemon=True, name="monitoring-worker")
        self._worker.start()
        self._toast("Phiên giám sát đã bắt đầu")

    def _monitoring_loop(self) -> None:
        assert self.source is not None and self.session is not None
        source = self.source
        session = self.session
        is_demo = isinstance(source, DemoSource)
        audio_detector: VoiceActivityDetector | None = None
        if not is_demo:
            if self.analyzer is None:
                self._put_message("status", "Đang tải MediaPipe Face Landmarker…")
                self.analyzer = GazeAnalyzer()
            self.analyzer.begin_calibration()
            if self._audio_confirmation_enabled:
                audio_detector = VoiceActivityDetector()
                audio_state = audio_detector.start()
                if audio_state.available:
                    audio_note = f" Microphone: {audio_state.device_name}."
                else:
                    audio_note = " Không mở được microphone; dùng nhịp môi dự phòng."
            else:
                audio_note = " Xác nhận microphone đang tắt."
            self._put_message(
                "status",
                f"Đã khởi tạo {self.analyzer.backend_name}.{audio_note} Nhìn thẳng để hiệu chỉnh.",
            )
        else:
            self._put_message("status", "Đang chạy kịch bản mô phỏng 35 giây.")

        frames = 0
        fps_started = time.monotonic()
        last_emit = 0.0
        try:
            while not self._stop_worker.is_set():
                ok, frame = source.read()
                if not ok or frame is None:
                    interrupted = MonitoringEvent(
                        event_id=uuid.uuid4().hex[:10].upper(),
                        session_id=session.session_id,
                        event_type=EventType.CAMERA_INTERRUPTED,
                        severity=Severity.HIGH,
                        started_at=datetime.now().isoformat(timespec="seconds"),
                        ended_at=datetime.now().isoformat(timespec="seconds"),
                        duration_seconds=0.0,
                        reason="Nguồn camera hoặc video bị gián đoạn.",
                        confidence=1.0,
                    )
                    session.events.append(interrupted)
                    self._put_message("event", interrupted)
                    self._put_message("error", "Không nhận được khung hình từ nguồn đã chọn.")
                    break
                now = time.monotonic()
                result = source.last_result if is_demo else self.analyzer.analyze(frame, now)
                if audio_detector is not None:
                    audio_state = audio_detector.snapshot()
                    confirmed_talking = speech_is_confirmed(
                        result.talking_score,
                        result.is_talking,
                        audio_state,
                    )
                    status_text = self.analyzer._status_text(
                        result.direction,
                        result.face_count,
                        result.person_count,
                        result.brightness,
                        confirmed_talking,
                        result.suspicious_objects,
                        result.eye_direction,
                        result.head_direction,
                        result.eyes_outside_zone,
                    )
                    result = replace(
                        result,
                        is_talking=confirmed_talking,
                        audio_available=audio_state.available,
                        voice_detected=audio_state.voice_active,
                        audio_level_db=audio_state.level_db,
                        status_text=status_text,
                    )
                if self._stop_worker.is_set():
                    break
                created = self.detector.update(result, session.session_id, now)
                if created:
                    for event in created:
                        if self._save_evidence_enabled:
                            path = self.store.evidence_dir / f"{session.session_id}_{event.event_id}.jpg"
                            cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
                            event.evidence_path = str(path)
                        session.events.append(event)
                        self._put_message("event", event)

                display = OverlayRenderer.draw(frame, result, show_landmarks=self._show_landmarks_enabled)
                frames += 1
                elapsed = max(0.001, now - fps_started)
                fps = frames / elapsed
                session.frame_count = frames
                session.average_fps = fps
                if now - last_emit >= 1 / 24:
                    self._put_frame(display, frame, result, fps)
                    last_emit = now
                if is_demo:
                    time.sleep(1 / 30)
        except Exception as exc:
            self._put_message("error", f"Bộ phân tích gặp lỗi: {exc}")
        finally:
            if audio_detector is not None:
                audio_detector.stop()
            source.release()
            self._put_message("worker_stopped", None)

    def _put_frame(self, display, raw, result, fps) -> None:
        item = ("frame", display, raw, result, fps)
        try:
            self._frame_queue.put_nowait(item)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(item)
            except queue.Full:
                pass

    def _put_message(self, kind: str, payload) -> None:
        self._message_queue.put_nowait((kind, payload))

    def _drain_worker_queues(self) -> None:
        for worker_queue in (self._frame_queue, self._message_queue):
            try:
                while True:
                    worker_queue.get_nowait()
            except queue.Empty:
                pass

    def _poll_frames(self) -> None:
        try:
            while True:
                self._handle_worker_message(self._message_queue.get_nowait())
        except queue.Empty:
            pass
        latest_frame = None
        try:
            while True:
                item = self._frame_queue.get_nowait()
                if item[0] == "frame":
                    latest_frame = item
        except queue.Empty:
            pass
        if latest_frame:
            _, display, raw, result, fps = latest_frame
            self.last_display_frame = display
            self.last_raw_frame = raw
            self.last_result = result
            self._fps = fps
            self._render_video(display)
            self._update_live_metrics(result, fps)
        self.after(35, self._poll_frames)

    def _handle_worker_message(self, item) -> None:
        kind, payload = item
        if kind == "status":
            self.session_status.configure(text=payload, fg=COLORS["text_muted"])
        elif kind == "event":
            self._on_new_event(payload)
        elif kind == "error":
            self.session_status.configure(text=payload, fg=COLORS["danger"])
            self._toast(payload, danger=True)
        elif kind == "worker_stopped" and self.monitoring and not self._stop_worker.is_set():
            self.after(100, self.stop_session)

    def _render_video(self, frame) -> None:
        width = max(320, self.video_label.winfo_width())
        height = max(240, self.video_label.winfo_height())
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        scale = min(width / image.width, height / image.height)
        target = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        image = image.resize(target, Image.Resampling.LANCZOS)
        background = Image.new("RGB", (width, height), COLORS["video_bg"])
        background.paste(image, ((width - target[0]) // 2, (height - target[1]) // 2))
        self._photo = ImageTk.PhotoImage(background)
        self.video_label.configure(image=self._photo, text="")

    def _update_live_metrics(self, result, fps: float) -> None:
        labels = {"center": "THẲNG", "left": "TRÁI", "right": "PHẢI", "up": "TRÊN", "down": "DƯỚI", "unknown": "—"}
        people = result.detected_people_count
        if result.face_count == 0:
            eye_text = "—"
        elif not result.calibrated:
            eye_text = "ĐANG HỌC"
        elif result.eyes_outside_zone:
            eye_text = f"NGOÀI • {labels.get(result.eye_direction, '—')}"
        else:
            eye_text = "TRONG VÙNG"
        head_text = labels.get(result.head_direction, "—") if result.face_count else "—"
        self.metric_labels["eye"].configure(text=eye_text)
        self.metric_labels["head"].configure(text=head_text)
        self.metric_labels["faces"].configure(text=str(people))
        self.metric_labels["events"].configure(text=str(len(self.session.events) if self.session else 0))
        self.fps_label.configure(text=f"{fps:.1f} FPS")
        if isinstance(self.source, DemoSource):
            mic_text, mic_color = "MIC DEMO", COLORS["text_dim"]
        elif result.audio_available:
            mic_text = "VOICE" if result.voice_detected else f"MIC {result.audio_level_db:.0f}dB"
            mic_color = COLORS["warning"] if result.voice_detected else COLORS["good"]
        elif self._audio_confirmation_enabled:
            mic_text, mic_color = "MIC ERROR", COLORS["danger"]
        else:
            mic_text, mic_color = "MIC OFF", COLORS["text_dim"]
        self.mic_label.configure(text=mic_text, fg=mic_color)
        critical = result.face_count == 0 or people >= 2 or bool(result.suspicious_objects)
        eye_color = COLORS["danger"] if critical else (
            COLORS["warning"] if result.eyes_outside_zone else (
                COLORS["good"] if result.calibrated else COLORS["text_muted"]
            )
        )
        head_color = COLORS["danger"] if critical else (
            COLORS["warning"] if result.head_direction not in {"center", "unknown"} else COLORS["good"]
        )
        self.metric_labels["eye"].configure(fg=eye_color)
        self.metric_labels["head"].configure(fg=head_color)
        self.metric_labels["faces"].configure(fg=COLORS["good"] if people == 1 else COLORS["danger"])
        self.metric_labels["events"].configure(
            fg=COLORS["warning"] if self.session and self.session.events else COLORS["text"]
        )
        if isinstance(self.source, DemoSource):
            progress = 1.0
            text = "Đã hiệu chỉnh (mô phỏng)"
        elif self.analyzer:
            progress = self.analyzer.calibration_progress
            text = "Đã hiệu chỉnh" if self.analyzer.calibrated else f"Nhìn thẳng… {progress * 100:.0f}%"
        else:
            progress = 0.0
            text = "Đang khởi tạo"
        self.calibration_text.configure(text=text, fg=COLORS["good"] if progress >= 1 else COLORS["text_muted"])
        self._draw_progress(self.calibration_bar, progress)
        status_color = COLORS["danger"] if critical else (
            COLORS["warning"]
            if result.eyes_outside_zone or result.head_direction not in {"center", "unknown"} or result.is_talking
            else COLORS["good"]
        )
        self.session_status.configure(text=result.status_text, fg=status_color)

    def _draw_progress(self, canvas: tk.Canvas, progress: float) -> None:
        canvas.update_idletasks()
        canvas.delete("all")
        width = max(10, canvas.winfo_width())
        canvas.create_rectangle(0, 0, width * min(1.0, max(0.0, progress)), 4, fill=COLORS["good"], outline="")

    def _on_new_event(self, event: MonitoringEvent) -> None:
        self._show_event_toast(event)
        if self._page_name == "events":
            self._refresh_events()
        self.metric_labels["events"].configure(text=str(len(self.session.events) if self.session else 0))

    def _show_event_toast(self, event: MonitoringEvent) -> None:
        # Low-risk events remain in the timeline without interrupting the user.
        if event.severity in {Severity.INFO, Severity.LOW}:
            return
        priority = {
            Severity.INFO: 0,
            Severity.LOW: 1,
            Severity.MEDIUM: 2,
            Severity.HIGH: 3,
        }[event.severity]
        now = time.monotonic()
        if now < self._alert_toast_until and priority <= self._alert_toast_priority:
            return
        self._alert_toast_until = now + 2.8
        self._alert_toast_priority = priority
        self._toast(f"Cảnh báo: {event.label}", danger=event.severity is Severity.HIGH)

    def stop_session(self) -> None:
        if not self.monitoring:
            return
        self._stop_worker.set()
        self.monitoring = False
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=0.8)
        if self.session:
            self.session.ended_at = datetime.now().isoformat(timespec="seconds")
            self.store.save_session(self.session)
            self.last_session = self.session
        self.start_button.configure(state="normal")
        self.start_button.configure(text="BẮT ĐẦU GIÁM SÁT")
        self.start_button.grid(row=0, column=0, sticky="ew")
        self.stop_button.configure(state="disabled")
        self.stop_button.grid_remove()
        self.source_combo.configure(state="readonly")
        self.candidate_entry.configure(state="normal")
        self.exam_entry.configure(state="normal")
        self.global_status_dot.configure(fg=COLORS["text_dim"])
        self.global_status.configure(text="Sẵn sàng", fg=COLORS["text_muted"])
        self.live_dot.configure(fg=COLORS["text_dim"])
        self.live_title.configure(text="PHIÊN ĐÃ KẾT THÚC", fg=COLORS["text_muted"])
        self.session_status.configure(text="Phiên đã được lưu cục bộ. Mở Báo cáo để xem tóm tắt.", fg=COLORS["text_muted"])
        self._toast("Đã lưu phiên giám sát")
        self._refresh_dashboard()
        self._refresh_events()
        self._refresh_report()
        self.session = None

    def _refresh_dashboard(self) -> None:
        sessions = self.store.list_sessions()
        self.dashboard_values["sessions"].configure(text=str(len(sessions)))
        latest = self.last_session
        if latest is None and sessions:
            recent = sessions[0]
            events = recent.get("events", [])
            risk = recent.get("risk_score", 0)
        elif latest:
            events = latest.events
            risk = latest.risk_score
        else:
            events, risk = [], 0
        self.dashboard_values["events"].configure(text=str(len(events)))
        self.dashboard_values["risk"].configure(text=f"{risk:02d}")
        self.dashboard_values["risk"].configure(fg=self._risk_color(risk))
        for child in self.dashboard_recent.winfo_children():
            child.destroy()
        if not sessions:
            tk.Label(
                self.dashboard_recent,
                text="Chưa có phiên giám sát nào.\nBắt đầu bằng chế độ mô phỏng để xem thử.",
                justify="left",
                bg=COLORS["surface"],
                fg=COLORS["text_muted"],
                font=FONTS["small"],
            ).pack(anchor="w", pady=8)
            return
        for data in sessions[:4]:
            row = tk.Frame(self.dashboard_recent, bg=COLORS["surface"])
            row.pack(fill="x", pady=6)
            tk.Label(row, text=data.get("candidate_id", "—"), bg=COLORS["surface"], fg=COLORS["text"], font=FONTS["body_medium"]).pack(anchor="w")
            meta = f"{data.get('source_name', '—')}  •  {len(data.get('events', []))} sự kiện  •  risk {data.get('risk_score', 0):02d}"
            tk.Label(row, text=meta, bg=COLORS["surface"], fg=COLORS["text_dim"], font=FONTS["small"]).pack(anchor="w", pady=(2, 0))

    def _current_events(self) -> list[MonitoringEvent]:
        if self.session:
            return self.session.events
        if self.last_session:
            return self.last_session.events
        return []

    def _refresh_events(self) -> None:
        for item in self.event_tree.get_children():
            self.event_tree.delete(item)
        for event in reversed(self._current_events()):
            time_text = event.started_at.split("T")[-1]
            self.event_tree.insert(
                "",
                "end",
                iid=event.event_id,
                values=(time_text, event.label, event.severity_label, f"{event.duration_seconds:.1f}s", f"{event.confidence * 100:.0f}%", event.review_status),
            )

    def _review_selected_event(self) -> None:
        selected = self.event_tree.selection()
        if not selected:
            self._toast("Hãy chọn một sự kiện trước")
            return
        event_id = selected[0]
        events = self._current_events()
        event = next((item for item in events if item.event_id == event_id), None)
        if event is None:
            return
        note = simpledialog.askstring("Xem lại cảnh báo", f"{event.label}\n{event.reason}\n\nGhi chú của giám thị:", parent=self)
        if note is None:
            return
        event.review_status = "Đã xem"
        event.reviewer_note = note.strip()
        target = self.session or self.last_session
        if target:
            self.store.save_session(target)
        self._refresh_events()
        self._toast("Đã cập nhật trạng thái cảnh báo")

    def _refresh_report(self) -> None:
        session = self.session or self.last_session
        if not session:
            return
        risk = session.risk_score
        self.report_risk.configure(text=f"{risk:02d}", fg=self._risk_color(risk))
        duration = session.duration_seconds
        self.report_session_label.configure(text=f"{session.session_id}  •  {session.candidate_id}  •  {session.exam_name}")
        self.report_meta.configure(
            text=f"Thời lượng: {duration / 60:.1f} phút\nNguồn: {session.source_name}\nSự kiện: {len(session.events)}\nFPS trung bình: {session.average_fps:.1f}"
        )
        counts = {Severity.LOW: 0, Severity.MEDIUM: 0, Severity.HIGH: 0}
        for event in session.events:
            if event.severity in counts:
                counts[event.severity] += 1
        reviewed = sum(event.review_status == "Đã xem" for event in session.events)
        text = (
            f"Phiên thi ghi nhận {len(session.events)} sự kiện: {counts[Severity.HIGH]} mức cao, "
            f"{counts[Severity.MEDIUM]} mức trung bình và {counts[Severity.LOW]} mức thấp. "
            f"Hiện có {reviewed}/{len(session.events)} cảnh báo đã được giám thị xem lại.\n\n"
            "Điểm rủi ro dùng để ưu tiên phiên cần kiểm tra, không phải điểm kết luận gian lận. "
            "Cần xem ảnh bằng chứng, bối cảnh kỳ thi và ghi chú của giám thị trước khi đưa ra quyết định."
        )
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", "end")
        self.report_text.insert("1.0", text)
        self.report_text.configure(state="disabled")
        self._draw_report_chart()

    def _draw_report_chart(self) -> None:
        if not hasattr(self, "report_canvas"):
            return
        canvas = self.report_canvas
        canvas.delete("all")
        width = max(320, canvas.winfo_width())
        height = max(120, canvas.winfo_height())
        session = self.session or self.last_session
        severities = [
            (Severity.LOW, "THẤP", COLORS["info"]),
            (Severity.MEDIUM, "TRUNG BÌNH", COLORS["warning"]),
            (Severity.HIGH, "CAO", COLORS["danger"]),
        ]
        counts = {severity: 0 for severity, _, _ in severities}
        if session:
            for event in session.events:
                if event.severity in counts:
                    counts[event.severity] += 1
        max_count = max(1, max(counts.values(), default=1))
        bar_width = max(45, min(90, width // 7))
        gap = (width - bar_width * 3) / 4
        for idx, (severity, label, color) in enumerate(severities):
            x1 = gap + idx * (bar_width + gap)
            bar_h = (height - 55) * counts[severity] / max_count
            y1 = height - 28 - bar_h
            canvas.create_rectangle(x1, y1, x1 + bar_width, height - 28, fill=color, outline="")
            canvas.create_text(x1 + bar_width / 2, max(10, y1 - 10), text=str(counts[severity]), fill=COLORS["text"], font=FONTS["body_medium"])
            canvas.create_text(x1 + bar_width / 2, height - 13, text=label, fill=COLORS["text_dim"], font=FONTS["tiny"])

    def _export_json(self) -> None:
        session = self.session or self.last_session
        if not session:
            self._toast("Chưa có báo cáo để xuất")
            return
        path = filedialog.asksaveasfilename(
            title="Xuất báo cáo JSON",
            defaultextension=".json",
            initialfile=f"bao_cao_{session.session_id}.json",
            filetypes=[("JSON", "*.json")],
        )
        if path:
            Path(path).write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            self._toast("Đã xuất báo cáo JSON")

    def _export_csv(self) -> None:
        session = self.session or self.last_session
        if not session:
            self._toast("Chưa có báo cáo để xuất")
            return
        path = filedialog.asksaveasfilename(
            title="Xuất danh sách sự kiện",
            defaultextension=".csv",
            initialfile=f"su_kien_{session.session_id}.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Mã sự kiện", "Loại", "Mức độ", "Bắt đầu", "Thời lượng", "Tin cậy", "Lý do", "Trạng thái", "Ghi chú"])
                for event in session.events:
                    writer.writerow([event.event_id, event.label, event.severity_label, event.started_at, event.duration_seconds, event.confidence, event.reason, event.review_status, event.reviewer_note])
            self._toast("Đã xuất danh sách CSV")

    def _setting_changed(self, event_type: EventType) -> None:
        value = self.setting_vars[event_type].get()
        self.detector.set_threshold(event_type, value)
        self.settings[event_type.value] = round(value, 2)
        self._save_settings()

    def _save_settings(self) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_settings(self) -> dict:
        try:
            return json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _tick_clock(self) -> None:
        self.clock_label.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.after(1000, self._tick_clock)

    def _toast(self, text: str, danger: bool = False) -> None:
        if self._toast_after_id:
            self.after_cancel(self._toast_after_id)
        self.toast.configure(
            text=text,
            bg=COLORS["danger"] if danger else COLORS["primary"],
            fg=COLORS["white"] if danger else COLORS["primary_text"],
        )
        self.toast.place(relx=1.0, rely=1.0, x=-24, y=-24, anchor="se")
        self.toast.lift()
        self._toast_after_id = self.after(3200, self.toast.place_forget)

    @staticmethod
    def _risk_color(risk: int) -> str:
        if risk >= 60:
            return COLORS["danger"]
        if risk >= 25:
            return COLORS["warning"]
        return COLORS["good"]

    def _on_close(self) -> None:
        self._stop_worker.set()
        if self.source:
            try:
                self.source.release()
            except Exception:
                pass
        if self.analyzer:
            try:
                self.analyzer.close()
            except Exception:
                pass
        self.destroy()

    def _capture_screenshot(self) -> None:
        if not self._screenshot_path:
            return
        self.attributes("-topmost", True)
        self.lift()
        self.focus_force()
        self.update_idletasks()
        self.update()
        x = self.winfo_rootx()
        y = self.winfo_rooty()
        width = self.winfo_width()
        height = self.winfo_height()
        Path(self._screenshot_path).parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(self._screenshot_path)
        self.attributes("-topmost", False)
        self.after(200, self._on_close)


def run(auto_demo: bool = False, screenshot_path: str | None = None) -> None:
    app = ExamMonitorApp(auto_demo=auto_demo, screenshot_path=screenshot_path)
    app.mainloop()
