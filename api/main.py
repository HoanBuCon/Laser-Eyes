"""FastAPI Enterprise Entry Application for VIGIL AI Exam Proctoring.

Implements SRS v1.0 specifications:
- REST API Routers under /api/v1 (and /api for backwards compatibility)
- Routes: sites, rooms, cameras, seats, sessions, events, workers, statistics, inference
- Health and readiness endpoints
- WebSocket endpoints for live event streaming
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from api.realtime import realtime_manager
from classroom_monitor.demo.runtime import DemoRuntime
from api.routes import (
    cameras,
    data_workbench,
    demo,
    events,
    inference,
    rooms,
    seats,
    sessions,
    sites,
    statistics,
    workers,
)
from storage.database import init_db

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
    logger.info("VIGIL AI Enterprise Proctoring Server is ready!")
    try:
        yield
    finally:
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
    """Silence browser default favicon requests."""
    return Response(status_code=204)


# Enable CORS for web dashboards and external microservices
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
    app.include_router(data_workbench.router, prefix=prefix)


@app.get("/health", tags=["Health"])
@app.get("/api/v1/health", tags=["Health"])
def health_check():
    """Service liveness probe."""
    return {"status": "ok", "service": "VIGIL AI Proctoring Server", "version": "2.6.0"}


@app.get("/ready", tags=["Health"])
@app.get("/api/v1/ready", tags=["Health"])
def readiness_check():
    """Service readiness probe."""
    return {"status": "ready", "database": "connected"}


@app.websocket("/ws/events")
@app.websocket("/ws/demo")
async def websocket_events_endpoint(websocket: WebSocket):
    """WebSocket stream for real-time proctoring event notifications & live demo telemetry."""
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
