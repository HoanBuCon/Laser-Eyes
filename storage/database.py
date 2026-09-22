"""Database Connection and Session Management for VIGIL AI."""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Environment variable override or default local SQLite database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/vigil_proctoring.db")
logger = logging.getLogger("VigilDatabase")

# Ensure local data directory exists if using SQLite
if DATABASE_URL.startswith("sqlite"):
    db_file = DATABASE_URL.replace("sqlite:///", "")
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
else:
    # PostgreSQL / MySQL enterprise connection pool
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        echo=False,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Dependency for obtaining database sessions in FastAPI routes or workers."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(custom_engine=None) -> None:
    """Create all database schema tables and ensure newly added columns exist."""
    target_engine = custom_engine or engine
    Base.metadata.create_all(bind=target_engine)

    # Automatic lightweight column migration for SQLite development DB
    try:
        with target_engine.connect() as conn:
            tables = [r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()]
            
            # 1. Seats table migrations
            if "seats" in tables:
                res = conn.execute(text("PRAGMA table_info(seats)")).fetchall()
                seat_cols = {row[1] for row in res}
                if "context_json" not in seat_cols:
                    conn.execute(text("ALTER TABLE seats ADD COLUMN context_json TEXT"))
                    conn.commit()

            # 2. Detection events table migrations (SRS v2 columns)
            if "detection_events" in tables:
                res = conn.execute(text("PRAGMA table_info(detection_events)")).fetchall()
                event_cols = {row[1] for row in res}
                if "primary_signal" not in event_cols:
                    conn.execute(text("ALTER TABLE detection_events ADD COLUMN primary_signal VARCHAR(50) DEFAULT 'NO_CHEATING'"))
                if "primary_pattern" not in event_cols:
                    conn.execute(text("ALTER TABLE detection_events ADD COLUMN primary_pattern VARCHAR(50)"))
                if "supporting_patterns_json" not in event_cols:
                    conn.execute(text("ALTER TABLE detection_events ADD COLUMN supporting_patterns_json TEXT"))
                if "observation_quality" not in event_cols:
                    conn.execute(text("ALTER TABLE detection_events ADD COLUMN observation_quality FLOAT DEFAULT 1.0"))
                conn.commit()

                duplicate_event_ids = conn.execute(
                    text("SELECT event_id FROM detection_events GROUP BY event_id HAVING COUNT(*) > 1 LIMIT 1")
                ).fetchone()
                if duplicate_event_ids is None:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_detection_events_event_id "
                        "ON detection_events(event_id)"
                    ))
                else:
                    logger.error(
                        "Cannot enforce event_id uniqueness on existing DB; duplicate key %s requires human cleanup",
                        duplicate_event_ids[0],
                    )

            if "evidence_files" in tables:
                duplicate_evidence = conn.execute(
                    text("SELECT event_id FROM evidence_files GROUP BY event_id HAVING COUNT(*) > 1 LIMIT 1")
                ).fetchone()
                if duplicate_evidence is None:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_files_event_id "
                        "ON evidence_files(event_id)"
                    ))
                else:
                    logger.error(
                        "Cannot enforce one evidence row per event on existing DB; event %s is duplicated",
                        duplicate_evidence[0],
                    )
            conn.commit()
    except Exception as exc:
        logger.exception("Database schema initialization failed: %s", exc)
        raise
