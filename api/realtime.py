"""WebSocket & Realtime Notification Manager for VIGIL AI Proctoring.

Handles high-throughput, low-latency distribution of:
- DEMO_STATUS (Processing FPS, frame index, risk counters)
- REVIEW_INCIDENT (New/Updated review incidents pending human decision)
- REVIEW_DECISION (Human proctor confirm/reject/inconclusive actions)
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import Future
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("VigilRealtime")


class RealtimeManager:
    """Thread-safe WebSocket broadcast manager."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_lock = threading.Lock()

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._loop_lock:
            self._loop = loop

    def clear_event_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        with self._loop_lock:
            if loop is None or self._loop is loop:
                self._loop = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info("WebSocket client connected. Total active: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket client disconnected. Total active: %d", len(self.active_connections))

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Asynchronously send JSON payload to all active connections."""
        for ws in list(self.active_connections):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)

    def broadcast_threadsafe(self, message: Dict[str, Any]) -> Optional[Future]:
        """Schedule broadcast from a synchronous/background worker thread."""
        with self._loop_lock:
            loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            logger.debug("Realtime broadcast skipped because the server event loop is unavailable")
            return None
        return asyncio.run_coroutine_threadsafe(self.broadcast(message), loop)


# Global singleton instance
realtime_manager = RealtimeManager()
