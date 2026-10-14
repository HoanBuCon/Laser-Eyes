"""Prepare a clean database for a VIGIL demo (safe to run again).

Creates, if missing: the three demo accounts (admin / chief / proctor), one
active demo exam, the calibrated Classroom rooms and the VIGIL One virtual
room, and prints a one-time pairing code for the VIGIL One computer.

Use a separate demo database so the working one keeps its history:

    venv\\Scripts\\python.exe scripts\\seed_demo.py --database data\\vigil_demo.db
    set DATABASE_URL=sqlite:///data/vigil_demo.db && start_vigil.bat

Passwords come from --password (default below); change them for any demo
outside your own machine.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PASSWORD = "vigil-demo-2026"
DEMO_EXAM = "Kỳ thi trình diễn VIGIL"
ACCOUNTS = (
    ("admin", "Quản trị hệ thống", "ADMIN"),
    ("chief", "Trưởng ban coi thi", "CHIEF"),
    ("proctor", "Giám thị phòng thi", "PROCTOR"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default="data/vigil_demo.db", help="SQLite file to prepare (created if missing)")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Password for the demo accounts")
    parser.add_argument("--no-pairing-code", action="store_true", help="Do not print a VIGIL One pairing code")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Vietnamese names on a cp1252 console

    db_path = Path(args.database)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # storage.database reads DATABASE_URL when it is imported
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    from server import ensure_calibration_sources
    from storage.auth_service import create_user
    from storage.database import SessionLocal, init_db
    from storage.db_models import Exam, User
    from storage.one_service import create_pairing_code, ensure_one_room

    init_db()
    db = SessionLocal()
    try:
        ensure_calibration_sources(db)
        ensure_one_room(db)
        made = []
        for username, display, role in ACCOUNTS:
            if db.query(User).filter(User.username == username).first() is None:
                create_user(db, username, display, role, args.password)
                made.append(username)
        exam = db.query(Exam).filter(Exam.name == DEMO_EXAM).first()
        if exam is None:
            exam = Exam(name=DEMO_EXAM, subject_code="DEMO", description="Ca thi trình diễn: VIGIL Classroom + VIGIL One",
                        status="ACTIVE", created_by="seed_demo")
            db.add(exam)
            db.commit()
        print(f"Database      : {db_path}")
        print(f"Accounts      : {', '.join(u for u, _, _ in ACCOUNTS)}"
              + (f"  (new: {', '.join(made)}; password: {args.password})" if made else "  (already present)"))
        print(f"Demo exam     : {exam.name}  [{exam.id}]")
        if not args.no_pairing_code:
            code, expires = create_pairing_code(db, "seed_demo")
            print(f"Pairing code  : {code}  (VIGIL One > Cài đặt > Ghép nối; valid 10 minutes, until {expires:%H:%M} UTC)")
        print(f"Start server  : set DATABASE_URL=sqlite:///{db_path.as_posix()} && start_vigil.bat")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
