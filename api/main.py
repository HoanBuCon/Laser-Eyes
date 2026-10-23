"""FastAPI Enterprise Entry Application for VIGIL AI Exam Proctoring.

Implements SRS v1.0 specifications:
- REST API Routers under /api/v1 (and /api for backwards compatibility)
- Routes: sites, rooms, cameras, seats, sessions, events, workers, statistics, inference
- Health and readiness endpoints
- WebSocket endpoints for live event streaming
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import secrets
from typing import Optional
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles

from api.realtime import realtime_manager
from classroom_monitor.demo.runtime import DemoRuntime
from api.routes import (
    admin,
    auth as auth_routes,
    exams,
    reports,
    cameras,
    data_workbench,
    demo,
    events,
    inference,
    one,
    proctor,
    rooms,
    seats,
    sessions,
    sites,
    statistics,
    workers,
)
from api.auth import COOKIE_NAME, auth_mode
from storage.auth_service import user_for_token
from storage.database import SessionLocal, get_db, init_db
from storage.session_lifecycle import close_interrupted_sessions

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("VigilAPI")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown hooks."""
    logger.info("Initializing VIGIL AI database schema...")
    init_db()
    # No analysis is running yet: sessions still RUNNING belong to a previous process
    db = SessionLocal()
    try:
        close_interrupted_sessions(db)
    finally:
        db.close()
    loop = asyncio.get_running_loop()
    realtime_manager.set_event_loop(loop)
    runtime = DemoRuntime.get_instance()

    def publish_event(_event, payload):
        realtime_manager.broadcast_threadsafe({"type": "REVIEW_INCIDENT", "event": payload})

    def publish_status(status):
        payload = status.to_dict() if hasattr(status, "to_dict") else dict(status)
        realtime_manager.broadcast_threadsafe({"type": "DEMO_STATUS", "status": payload})

    runtime.register_event_callback(publish_event)
    runtime.register_status_callback(publish_status)

    async def watch_one_connections():
        """Online exams: flag candidates whose VIGIL One went silent (every 10 s)."""
        from storage import one_analysis as one_analysis_mod
        from storage import one_video
        from storage.db_models import ExamSession
        from storage.online_exam_service import check_connections, close_abandoned

        def session_provider():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        def check():
            session = SessionLocal()
            try:
                opened = check_connections(session)
                closed = close_abandoned(session)
                for session_id in closed:  # as if the candidate had stopped: last clips, then the whole video
                    one_analysis_mod.notify(session_id)
                    row = session.get(ExamSession, session_id)
                    if row is not None and one_video.needs_merge(session, row):
                        row.video_status = "MERGING"
                        session.commit()
                        one_video.merge_in_background(session_id, session_provider)
                return opened, closed
            finally:
                session.close()

        while True:
            await asyncio.sleep(10)
            try:
                opened, closed = await asyncio.to_thread(check)
                if opened:
                    realtime_manager.broadcast_threadsafe({"type": "ONE_EVENTS", "reason": "connection_lost"})
                if closed:
                    logger.info("VIGIL One: closed %d abandoned session(s): %s", len(closed), ", ".join(closed))
                    realtime_manager.broadcast_threadsafe({"type": "ONE_EVENTS", "reason": "abandoned", "sessions": closed})
            except Exception as exc:  # never let the watchdog take the server down
                logger.warning("VIGIL One connection watchdog: %s", exc)

    watchdog = asyncio.create_task(watch_one_connections())

    async def apply_retention_periodically():
        """Media past VIGIL_RETENTION_DAYS is deleted: 2 minutes after start, then every 6 hours."""
        from storage.retention import run_retention

        def run():
            db = SessionLocal()
            try:
                return run_retention(db)
            finally:
                db.close()

        await asyncio.sleep(120)
        while True:
            try:
                await asyncio.to_thread(run)
            except Exception as exc:  # never let retention take the server down
                logger.warning("Retention: %s", exc)
            await asyncio.sleep(6 * 3600)

    retention = asyncio.create_task(apply_retention_periodically())

    # VIGIL One sessions in analysis_mode SERVER are analysed here, next to Classroom
    from storage import one_analysis

    one_analysis.start_service(
        SessionLocal,
        on_events=lambda session_id, count: realtime_manager.broadcast_threadsafe(
            {"type": "ONE_EVENTS", "session_id": session_id, "created": count, "source": "server_analysis"}),
    )
    logger.info("VIGIL AI Enterprise Proctoring Server is ready!")
    try:
        yield
    finally:
        watchdog.cancel()
        retention.cancel()
        one_analysis.stop_service()
        runtime.unregister_event_callback(publish_event)
        runtime.unregister_status_callback(publish_status)
        realtime_manager.clear_event_loop(loop)
        logger.info("Shutting down VIGIL AI server...")


app = FastAPI(
    title="VIGIL AI — AI-Assisted Exam Monitoring API",
    description="Enterprise Multi-Room AI Proctoring and Review Incident Management REST API (SRS v2.0).",
    version="2.6.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """The VIGIL AI logo for browsers that ask for /favicon.ico directly."""
    icon = Path("dashboard/assets/brand/favicon.ico")
    if icon.is_file():
        return FileResponse(str(icon), media_type="image/x-icon")
    return Response(status_code=204)


def _cors_origins() -> list[str]:
    configured = os.getenv("VIGIL_CORS_ORIGINS", "")
    if configured.strip():
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return ["http://127.0.0.1:8000", "http://localhost:8000"]


_DEVICE_PATHS = ("/api/v1/one/client/", "/api/one/client/")
DEMO_TOKEN_COOKIE = "vigil_demo_token"


def _is_loopback(host: Optional[str]) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


@app.middleware("http")
async def protect_network_demo(request: Request, call_next):
    """Require the opt-in demo token for API calls when one is configured."""
    token = os.getenv("VIGIL_DEMO_TOKEN")
    # The token keeps other computers out; a browser on the Hub computer itself needs none
    if not token or _is_loopback(request.client.host if request.client else None):
        return await call_next(request)
    # VIGIL One desktops authenticate with their own device token instead
    if request.url.path.startswith(("/api/", "/demo/")) and not request.url.path.startswith(_DEVICE_PATHS):
        supplied = (request.headers.get("X-Vigil-Demo-Token") or request.query_params.get("token")
                    or request.cookies.get(DEMO_TOKEN_COOKIE))
        if not supplied or not secrets.compare_digest(supplied, token):
            return JSONResponse(status_code=401, content={"detail": "Valid demo token required"})
    response = await call_next(request)
    # Opening any page once with ?token=... remembers it, so every page, image and live stream works
    supplied = request.query_params.get("token")
    if supplied and secrets.compare_digest(supplied, token) and request.cookies.get(DEMO_TOKEN_COOKIE) != supplied:
        response.set_cookie(DEMO_TOKEN_COOKIE, supplied, httponly=True, samesite="strict")
    return response


@app.middleware("http")
async def revalidate_dashboard_files(request: Request, call_next):
    """Pages, CSS and JS change with every release: the browser must check its
    copy (ETag, a cheap 304) instead of reusing a stale one for hours."""
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if request.url.path.startswith("/static/") or content_type.startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


_OPEN_PATHS = ("/api/v1/one/client/", "/api/one/client/", "/login", "/static/", "/api/v1/auth/", "/health", "/ready", "/api/v1/health", "/api/v1/ready", "/favicon.ico")


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    """With VIGIL_AUTH_MODE=required, every page and API needs a signed-in user."""
    path = request.url.path
    if auth_mode() != "required" or path.startswith(_OPEN_PATHS):
        return await call_next(request)
    provider = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = provider()
    db = next(db_gen)
    try:
        user = user_for_token(db, request.cookies.get(COOKIE_NAME))
    finally:
        db_gen.close()
    if user is not None:
        return await call_next(request)
    if path.startswith("/api/") or path.startswith("/ws"):
        return JSONResponse(status_code=401, content={"detail": "Sign in to continue."})
    return RedirectResponse(url=f"/login?next={quote(path)}", status_code=303)


# Dashboard origins are explicit. Credentials are not needed by this prototype.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register demo router at root as well for direct dashboard convenience
app.include_router(demo.router)

# Register API Routers under /api/v1 (SRS standard) and /api (legacy compatibility)
for prefix in ["/api/v1", "/api"]:
    app.include_router(demo.router, prefix=prefix)
    app.include_router(sites.router, prefix=prefix)
    app.include_router(rooms.router, prefix=prefix)
    app.include_router(cameras.router, prefix=prefix)
    app.include_router(seats.router, prefix=prefix)
    app.include_router(sessions.router, prefix=prefix)
    app.include_router(events.router, prefix=prefix)
    app.include_router(workers.router, prefix=prefix)
    app.include_router(statistics.router, prefix=prefix)
    app.include_router(inference.router, prefix=prefix)
    app.include_router(proctor.router, prefix=prefix)
    app.include_router(data_workbench.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)
    app.include_router(auth_routes.router, prefix=prefix)
    app.include_router(exams.router, prefix=prefix)
    app.include_router(reports.router, prefix=prefix)
    app.include_router(one.router, prefix=prefix)


try:
    from vigil_build import fingerprint as _fingerprint

    _BUILD = _fingerprint()  # the code this Hub started with
except Exception:  # pragma: no cover - a packaged Hub without the source tree
    _BUILD = "unknown"


def _database_name() -> str:
    from storage.database import DATABASE_URL as url

    return Path(url.split("///", 1)[-1]).name if url.startswith("sqlite") else url.split("@")[-1].split("?")[0]


@app.get("/health", tags=["Health"])
@app.get("/api/v1/health", tags=["Health"])
def health_check():
    """Service liveness probe; ``build`` and ``database`` let vigil.py spot a Hub left over from older code."""
    return {"status": "ok", "service": "VIGIL AI Proctoring Server", "version": "2.6.0", "build": _BUILD,
            "database": _database_name(), "features": ["one-live-stream", "one-server-analysis"]}


@app.get("/ready", tags=["Health"])
@app.get("/api/v1/ready", tags=["Health"])
def readiness_check():
    """Service readiness probe."""
    return {"status": "ready", "database": "connected"}


def _signed_in(websocket: WebSocket) -> bool:
    provider = websocket.app.dependency_overrides.get(get_db, get_db)
    gen = provider()
    db = next(gen)
    try:
        return user_for_token(db, websocket.cookies.get(COOKIE_NAME)) is not None
    finally:
        gen.close()


@app.websocket("/ws/events")
@app.websocket("/ws/demo")
async def websocket_events_endpoint(websocket: WebSocket):
    """WebSocket stream for real-time proctoring event notifications & live demo telemetry."""
    token = os.getenv("VIGIL_DEMO_TOKEN")
    supplied = (websocket.headers.get("X-Vigil-Demo-Token") or websocket.query_params.get("token")
                or websocket.cookies.get(DEMO_TOKEN_COOKIE))
    local = _is_loopback(websocket.client.host if websocket.client else None)
    if token and not local and (not supplied or not secrets.compare_digest(supplied, token)):
        await websocket.close(code=1008, reason="Valid demo token required")
        return
    if auth_mode() == "required" and not await run_in_threadpool(_signed_in, websocket):
        # The same rule as every page and API: live incidents are not for anonymous viewers
        await websocket.close(code=1008, reason="Sign in to continue")
        return
    await realtime_manager.connect(websocket)
    try:
        while True:
            # Keep connection open and receive optional client ping
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        realtime_manager.disconnect(websocket)


# Mount Dashboard Static Directory
dashboard_dir = Path("dashboard")
if dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory="dashboard"), name="static")

    @app.get("/", tags=["Dashboard"])
    def serve_dashboard():
        """Serve the realtime monitoring web dashboard UI."""
        index_file = dashboard_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Dashboard index.html not found, please visit /docs"}

    @app.get("/demo", tags=["Dashboard"])
    def serve_demo_page():
        """Serve the Unified Competition Demo Page."""
        demo_file = dashboard_dir / "demo.html"
        if demo_file.exists():
            return FileResponse(str(demo_file))
        return {"message": "Competition demo page demo.html not found, please visit /docs"}

    @app.get("/review", tags=["Dashboard"])
    def serve_review_queue():
        """Serve the cross-session human review queue."""
        review_file = dashboard_dir / "review.html"
        if review_file.exists():
            return FileResponse(str(review_file))
        return {"message": "Review queue review.html not found, please visit /docs"}

    @app.get("/wall", tags=["Dashboard"])
    def serve_video_wall():
        """Serve the multi-camera video wall."""
        wall_file = dashboard_dir / "wall.html"
        if wall_file.exists():
            return FileResponse(str(wall_file))
        return {"message": "Video wall wall.html not found, please visit /docs"}

    @app.get("/admin", tags=["Dashboard"])
    def serve_admin_page():
        """Serve the system maintenance page (demo and testing)."""
        admin_file = dashboard_dir / "admin.html"
        if admin_file.exists():
            return FileResponse(str(admin_file))
        return {"message": "System page admin.html not found, please visit /docs"}

    def _page(name: str):
        def serve():
            page = dashboard_dir / name
            if page.exists():
                return FileResponse(str(page))
            return {"message": f"{name} not found, please visit /docs"}
        return serve

    for route, page in (
        ("/login", "login.html"),
        ("/sessions", "sessions.html"),
        ("/playback", "playback.html"),
        ("/reports", "reports.html"),
        ("/reports/print", "report-print.html"),
        ("/users", "users.html"),
        ("/online", "online.html"),
        ("/one-video", "one_video.html"),
        ("/one-monitor", "one_monitor.html"),
    ):
        app.add_api_route(route, _page(page), methods=["GET"], tags=["Dashboard"], include_in_schema=False)

    @app.get("/calibration", tags=["Dashboard"])
    def serve_calibration_tool():
        """Serve the Interactive Seat ROI Calibration Tool UI."""
        calib_file = dashboard_dir / "calibration.html"
        if calib_file.exists():
            return FileResponse(str(calib_file))
        return {"message": "Calibration tool calibration.html not found, please visit /docs"}

    @app.get("/data-workbench", tags=["Dashboard"])
    def serve_data_workbench():
        """Serve the Human Data Operations Workbench UI."""
        workbench_file = dashboard_dir / "data_workbench.html"
        if workbench_file.exists():
            return FileResponse(str(workbench_file))
        return {"message": "Data Workbench data_workbench.html not found, please visit /docs"}
