"""Prepare an online exam on the Hub's database: a proctor account, the exam, its join code, candidates.

    python scripts/setup_exam.py --demo --proctor hoanbucon --password "<mật khẩu>" \\
        --exam "Kỳ thi cuối học kỳ" --join-code DTC --candidate "HS01,Nguyễn Văn A,1111"

The database follows the same rule as vigil.py: with ``--demo`` the demo database
(``python vigil.py --demo``), without it the working database (``python vigil.py``).
Safe to run again: an existing account gets the new password, an existing exam (same name) keeps its
candidates and gets the join code.  Works while the Hub is running (SQLite, same file).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare an online exam: proctor account, exam, join code, candidates.")
    parser.add_argument("--demo", action="store_true", help="The demo database, as python vigil.py --demo (default: the working one)")
    parser.add_argument("--database", help="Another SQLite file of the Hub")
    parser.add_argument("--proctor", help="Username of the proctor account to create or update")
    parser.add_argument("--proctor-name", help="Display name (default: the username)")
    parser.add_argument("--role", default="PROCTOR", choices=("PROCTOR", "CHIEF", "ADMIN"),
                        help="PROCTOR = giám thị, CHIEF = trưởng ban coi thi, ADMIN")
    parser.add_argument("--password", help="Password of that account (at least 8 characters)")
    parser.add_argument("--exam", help="Exam name (created if missing)")
    parser.add_argument("--subject", help="Subject / course code shown with the exam")
    parser.add_argument("--join-code", help="Join code the candidates type in VIGIL One (3-12 letters or digits)")
    parser.add_argument("--candidate", action="append", default=[], metavar="CODE,NAME,PIN",
                        help="A candidate (repeatable); PIN optional (then one is generated and printed)")
    args = parser.parse_args(argv)
    if args.proctor and not args.password:
        parser.error("--proctor needs --password")
    if (args.join_code or args.candidate) and not args.exam:
        parser.error("--join-code and --candidate need --exam")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    db_path = Path(args.database or ("data/vigil_demo.db" if args.demo else "data/vigil_proctoring.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"  # read when storage.database is imported
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    from storage import online_exam_service as online
    from storage.auth_service import AuthError, create_user, normalize_username, set_password
    from storage.database import SessionLocal, init_db
    from storage.db_models import Exam, User

    init_db()
    db = SessionLocal()
    try:
        print(f"Database  : {db_path}")
        if args.proctor:
            username = normalize_username(args.proctor)
            user = db.query(User).filter(User.username == username).first()
            try:
                if user is None:
                    user = create_user(db, username, args.proctor_name or args.proctor, args.role, args.password)
                    print(f"Account   : {user.username} ({user.role}) created")
                else:
                    set_password(db, user, args.password)
                    user.role = args.role
                    if args.proctor_name:
                        user.display_name = args.proctor_name
                    db.commit()
                    print(f"Account   : {user.username} ({user.role}) already existed: password and role updated")
            except AuthError as exc:
                print(f"Account   : not created: {exc}")
                return 2
        if args.exam:
            exam = db.query(Exam).filter(Exam.name == args.exam).first()
            if exam is None:
                exam = Exam(name=args.exam, subject_code=args.subject, status="ACTIVE", created_by="setup_exam")
                db.add(exam)
                db.commit()
                print(f"Exam      : “{exam.name}” created")
            else:
                if args.subject:
                    exam.subject_code = args.subject
                exam.status = "ACTIVE"
                db.commit()
                print(f"Exam      : “{exam.name}” already existed")
            try:
                if args.join_code:
                    online.set_join_code(db, exam, args.join_code)
                exam = online.set_joining(db, exam, True)
                print(f"Join code : {exam.join_code} (joining open)")
                rows = []
                for item in args.candidate:
                    parts = [p.strip() for p in item.split(",")]
                    rows.append({"candidate_code": parts[0], "full_name": parts[1] if len(parts) > 1 and parts[1] else None,
                                 "pin": parts[2] if len(parts) > 2 and parts[2] else None})
                if rows:
                    issued = {r["candidate_code"]: r["pin"] for r in online.import_candidates(db, exam, rows)}
                    for row in rows:
                        pin = issued.get(row["candidate_code"]) or row["pin"] or "(unchanged)"
                        print(f"Candidate : {row['candidate_code']}  {row['full_name'] or ''}  PIN {pin}")
            except online.OnlineExamError as exc:
                print(f"Exam      : {exc}")
                return 2
    finally:
        db.close()
    if not args.database:
        print(f"Start     : python vigil.py{' --demo' if args.demo else ''}   (the Hub must use this same database)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
