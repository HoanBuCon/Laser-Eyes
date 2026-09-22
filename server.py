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
    except Exception as exc:
        logger.warning("Could not seed demo data: %s", exc)
        db.rollback()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="VIGIL AI Enterprise Server Launcher")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Binding host IP (localhost by default)")
    parser.add_argument("--port", type=int, default=8000, help="Listening port")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument(
        "--demo-token",
        default=os.getenv("VIGIL_DEMO_TOKEN"),
        help="Required lightweight API token when binding beyond localhost",
    )
    args = parser.parse_args()

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

    # 3. Print Banner
    print("\n" + "=" * 70)
    print("      🚀 VIGIL AI ENTERPRISE EXAM PROCTORING SERVER RUNNING")
    print("=" * 70)
    print(f"  * Web Demo (ICTU) : http://localhost:{args.port}/demo")
    print(f"  * Web Dashboard   : http://localhost:{args.port}/")
    print(f"  * REST API Docs   : http://localhost:{args.port}/docs")
    print(f"  * ReDoc Schema    : http://localhost:{args.port}/redoc")
    if args.demo_token:
        print("  * Network access  : protected by X-Vigil-Demo-Token")
    print("=" * 70 + "\n")

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
