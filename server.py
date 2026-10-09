"""VIGIL AI Enterprise Server Launcher.

Bootstraps database schema, seeds default demonstration entities (if empty),
and runs the FastAPI ASGI server with real-time proctoring telemetry.

Usage:
    python server.py [--host 0.0.0.0] [--port 8000] [--reload]
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys

import uvicorn

from storage.database import SessionLocal, init_db
from storage.db_models import Camera, ExamRoom, ExamSession, ExamSite

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("VigilServer")

def _calibration_sources() -> tuple:
    """One calibration room and camera per demo video (classroom_monitor.demo.config.DEMO_PRESETS)."""
    from classroom_monitor.demo.config import DEMO_PRESETS

    return tuple(
        {
            "room_code": preset["room_code"],
            "room_name": preset.get("title", name),
            "capacity": None,
            "camera_name": f"VIGIL {preset.get('title', name)} Video",
            "source_uri": preset["video_path"],
            "resolution": preset.get("resolution"),
        }
        for name, preset in DEMO_PRESETS.items()
    )


CALIBRATION_SOURCES = _calibration_sources()


def ensure_calibration_sources(db) -> None:
    """Idempotently expose both competition videos to the calibration UI."""
    site = db.query(ExamSite).order_by(ExamSite.created_at.asc()).first()
    if site is None:
        site = ExamSite(name="VIGIL Competition Demo")
        db.add(site)
        db.flush()

    for spec in CALIBRATION_SOURCES:
        room = (
            db.query(ExamRoom)
            .filter(ExamRoom.room_code == spec["room_code"])
            .first()
        )
        if room is None:
            room = ExamRoom(
                site_id=site.id,
                room_code=spec["room_code"],
                name=spec["room_name"],
                capacity=spec["capacity"],
                description="Competition video Seat ROI calibration workspace",
            )
            db.add(room)
            db.flush()

        camera = (
            db.query(Camera)
            .filter(
                Camera.room_id == room.id,
                Camera.name == spec["camera_name"],
            )
            .first()
        )
        if camera is None:
            camera = Camera(room_id=room.id, name=spec["camera_name"])
            db.add(camera)
        camera.source_uri = spec["source_uri"]
        camera.rtsp_url_protected = spec["source_uri"]
        camera.resolution = spec["resolution"]
        camera.position = "competition_video"
        camera.enabled = True
        camera.status = "online" if os.path.isfile(spec["source_uri"]) else "error"
    db.commit()


def seed_initial_demo_data() -> None:
    """Pre-populate sample campus and classroom records if database is fresh."""
    db = SessionLocal()
    try:
        site_count = db.query(ExamSite).count()
        if site_count == 0:
            logger.info("Database is empty. Populating default demonstration sites & rooms...")
            site = ExamSite(
                name="Đại học Bách Khoa TP.HCM",
                address="268 Lý Thường Kiệt, Phường 14, Quận 10, TP.HCM",
                contact_info="proctoring@hcmut.edu.vn",
            )
            db.add(site)
            db.commit()
            db.refresh(site)

            room_a = ExamRoom(
                site_id=site.id,
                name="Phòng Hội trường A1-302",
                capacity=45,
                description="Phòng thi góc rộng camera trung tâm",
            )
            room_b = ExamRoom(
                site_id=site.id,
                name="Phòng Máy B2-105",
                capacity=30,
                description="Phòng máy thi trắc nghiệm trực tuyến",
            )
            db.add_all([room_a, room_b])
            db.commit()
            db.refresh(room_a)
            db.refresh(room_b)

            cam_a = Camera(
                room_id=room_a.id,
                name="Camera Góc Rộng 01",
                source_uri="0",
                position="front_center",
                status="online",
            )
            db.add(cam_a)
            db.commit()
            db.refresh(cam_a)

            # Sample active demo session
            sess = ExamSession(
                room_id=room_a.id,
                camera_id=cam_a.id,
                exam_name="Thi Đánh Giá Năng Lực 2026 - Đợt 1",
                total_frames=1250,
                avg_fps=29.4,
                total_events=3,
                risk_score=36,
                status="running",
            )
            db.add(sess)
            db.commit()
            logger.info("Successfully initialized default demonstration entities!")
        ensure_calibration_sources(db)
    except Exception as exc:
        logger.warning("Could not seed demo data: %s", exc)
        db.rollback()
    finally:
        db.close()


def preflight_report() -> dict:
    """Check what a Classroom run needs and print it; nothing here is fatal."""
    import shutil
    from pathlib import Path

    from classroom_monitor.config import DEFAULT_CONFIG
    from classroom_monitor.demo.config import DEMO_PRESETS, get_demo_config
    from classroom_monitor.demo.seating import CalibrationMismatchError, build_scene_seating

    def line(ok: bool, label: str, detail: str) -> None:
        print(f"  [{'OK' if ok else '!!'}] {label:<22} {detail}")

    print("\n  Preflight")
    try:
        import torch

        cuda = torch.cuda.is_available()
        line(True, "Compute", torch.cuda.get_device_name(0) if cuda else "CPU only (slow)")
    except Exception as exc:  # pragma: no cover - environment specific
        line(False, "Compute", f"PyTorch unavailable: {exc}")

    pose = Path(DEFAULT_CONFIG.pose_model_path)
    line(pose.is_file(), "Pose model", str(pose) if pose.is_file() else f"{pose} missing (downloaded on first run if online)")
    hpe_cache = Path.home() / ".cache" / "torch" / "hub" / "checkpoints" / "6DRepNet_300W_LP_AFLW2000.pth"
    line(hpe_cache.is_file(), "Head pose weights", "cached" if hpe_cache.is_file() else "not cached (downloaded on first run if online)")
    ffmpeg_ok = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
    line(ffmpeg_ok, "ffmpeg / ffprobe", "on PATH" if ffmpeg_ok else "missing: evidence clips may not play in the browser")

    uncalibrated = []
    for name in DEMO_PRESETS:
        try:
            config = get_demo_config(name)
            video_ok = True
        except FileNotFoundError as exc:
            line(False, f"Video {name}", str(exc))
            continue
        try:
            seating = build_scene_seating(config)
            line(video_ok, f"Room {config.room_code}", f"{len(seating.seat_defs)} Seat ROIs ({name} video)")
        except CalibrationMismatchError:
            uncalibrated.append(name)
            line(False, f"Room {config.room_code}", f"no Seat ROI yet — draw them at /calibration ({name} video)")
    return {"uncalibrated": uncalibrated}


def _open_browser_when_ready(url: str, port: int) -> None:
    import threading
    import time
    import urllib.request
    import webbrowser

    def wait_and_open() -> None:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        webbrowser.open(url)

    threading.Thread(target=wait_and_open, daemon=True).start()


def cleanup_test_data(confirm: bool) -> None:
    """Preview, or with confirm=True delete, rooms that have no Seat ROIs."""
    from classroom_monitor.demo.config import DEMO_PRESETS
    from storage.database import DATABASE_URL, SessionLocal
    from storage.maintenance import apply_cleanup, backup_sqlite, plan_test_room_cleanup

    init_db()
    db = SessionLocal()
    try:
        plan = plan_test_room_cleanup(db, keep_room_codes=[p["room_code"] for p in DEMO_PRESETS.values()])
        print(plan.summary())
        if not plan.rooms:
            print("Nothing to clean up.")
            return
        if not confirm:
            print()
            print("Preview only. Run again with --yes to delete these records (a backup is made first).")
            return
        backup = backup_sqlite(DATABASE_URL)
        if backup is not None:
            print(f"Backup: {backup}")
        apply_cleanup(db, plan)
        print("Deleted. Audit-log rows and evidence files on disk were kept.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="VIGIL AI Classroom server (single entry point)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Binding host IP (localhost by default)")
    parser.add_argument("--port", type=int, default=8000, help="Listening port")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--open-browser", action="store_true", help="Open the web interface once the server is ready")
    parser.add_argument(
        "--demo-token",
        default=os.getenv("VIGIL_DEMO_TOKEN"),
        help="Required lightweight API token when binding beyond localhost",
    )
    parser.add_argument(
        "--cleanup-test-data",
        action="store_true",
        help="List rooms without Seat ROIs (left by tests) and their records, then exit; add --yes to delete them",
    )
    parser.add_argument("--yes", action="store_true", help="Confirm --cleanup-test-data (a database backup is made first)")
    args = parser.parse_args()

    if args.cleanup_test_data:
        cleanup_test_data(confirm=args.yes)
        return

    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if args.host not in loopback_hosts and not args.demo_token:
        parser.error("A --demo-token (or VIGIL_DEMO_TOKEN) is required for LAN/public binding")
    os.environ["VIGIL_BIND_HOST"] = args.host
    if args.demo_token:
        os.environ["VIGIL_DEMO_TOKEN"] = args.demo_token

    # 1. Initialize DB Schema
    logger.info("Initializing schema tables...")
    init_db()

    # 2. Seed initial data if necessary
    seed_initial_demo_data()

    # 3. Print Banner (the website is the only operator interface)
    print("\n" + "=" * 70)
    print("      VIGIL AI CLASSROOM — open the website to operate the system")
    print("=" * 70)
    preflight = preflight_report()
    print()
    print(f"  * Seat calibration : http://localhost:{args.port}/calibration")
    print(f"  * Live monitor     : http://localhost:{args.port}/demo")
    print(f"  * Operations       : http://localhost:{args.port}/")
    if args.demo_token:
        print("  * Network access   : protected by X-Vigil-Demo-Token")
    print("  * Stop the server  : Ctrl+C in this window")
    print("=" * 70 + "\n")

    if args.open_browser:
        start_page = "/calibration" if preflight["uncalibrated"] else "/demo"
        _open_browser_when_ready(f"http://localhost:{args.port}{start_page}", args.port)

    # 4. Start Uvicorn Server
    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
