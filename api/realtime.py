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
from typing import Any, Dict, List, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("VigilRealtime")


class RealtimeManager:
    """Thread-safe WebSocket broadcast manager."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

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

    def broadcast_threadsafe(self, message: Dict[str, Any]) -> None:
        """Schedule broadcast from a synchronous/background worker thread."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(message), loop)
        except RuntimeError:
            pass


# Global singleton instance
realtime_manager = RealtimeManager()
