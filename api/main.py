"""FastAPI Enterprise Entry Application for VIGIL AI Exam Proctoring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import cameras, events, inference, rooms, sessions, sites, statistics
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
    logger.info("VIGIL AI Enterprise Proctoring Server is ready!")
    yield
    logger.info("Shutting down VIGIL AI server...")


app = FastAPI(
    title="VIGIL AI — Exam Cheating Surveillance API",
    description="Enterprise Multi-Room AI Proctoring and Cheating Detection REST API.",
    version="2.0.0",
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

# Register API Routers
app.include_router(sites.router, prefix="/api")
app.include_router(rooms.router, prefix="/api")
app.include_router(cameras.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(statistics.router, prefix="/api")
app.include_router(inference.router, prefix="/api")


@app.get("/health", tags=["Health"])
def health_check():
    """Service liveness probe."""
    return {"status": "ok", "service": "VIGIL AI Proctoring Server", "version": "2.0.0"}


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
