"""FastAPI Enterprise Entry Application for VIGIL AI Exam Proctoring.

Implements SRS v1.0 specifications:
- REST API Routers under /api/v1 (and /api for backwards compatibility)
- Routes: sites, rooms, cameras, seats, sessions, events, workers, statistics, inference
- Health and readiness endpoints
- WebSocket endpoints for live event streaming
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import (
    cameras,
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


class ConnectionManager:
    """Manages active WebSocket connections for realtime dashboard notifications."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


ws_manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown hooks."""
    logger.info("Initializing VIGIL AI database schema...")
    init_db()
    logger.info("VIGIL AI Enterprise Proctoring Server is ready!")
    yield
    logger.info("Shutting down VIGIL AI server...")


app = FastAPI(
    title="VIGIL AI — Exam Cheating Surveillance API",
    description="Enterprise Multi-Room AI Proctoring and Cheating Detection REST API (SRS v1.0).",
    version="2.5.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable CORS for web dashboards and external microservices
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers under /api/v1 (SRS standard) and /api (legacy compatibility)
for prefix in ["/api/v1", "/api"]:
    app.include_router(sites.router, prefix=prefix)
    app.include_router(rooms.router, prefix=prefix)
    app.include_router(cameras.router, prefix=prefix)
    app.include_router(seats.router, prefix=prefix)
    app.include_router(sessions.router, prefix=prefix)
    app.include_router(events.router, prefix=prefix)
    app.include_router(workers.router, prefix=prefix)
    app.include_router(statistics.router, prefix=prefix)
    app.include_router(inference.router, prefix=prefix)


@app.get("/health", tags=["Health"])
@app.get("/api/v1/health", tags=["Health"])
def health_check():
    """Service liveness probe."""
    return {"status": "ok", "service": "VIGIL AI Proctoring Server", "version": "2.5.0"}


@app.get("/ready", tags=["Health"])
@app.get("/api/v1/ready", tags=["Health"])
def readiness_check():
    """Service readiness probe."""
    return {"status": "ready", "database": "connected"}


@app.websocket("/ws/events")
async def websocket_events_endpoint(websocket: WebSocket):
    """WebSocket stream for real-time proctoring event notifications."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection open and receive optional client ping
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


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

    @app.get("/calibration", tags=["Dashboard"])
    def serve_calibration_tool():
        """Serve the Interactive Seat ROI Calibration Tool UI."""
        calib_file = dashboard_dir / "calibration.html"
        if calib_file.exists():
            return FileResponse(str(calib_file))
        return {"message": "Calibration tool calibration.html not found, please visit /docs"}
