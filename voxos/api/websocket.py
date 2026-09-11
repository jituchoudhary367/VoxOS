"""
WebSocket Server.

Handles real-time streaming audio interactions and event bridging
from the EventBus to connected clients.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from voxos.config import get_settings
from voxos.core.events.bus import get_event_bus
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.security.auth import verify_token

log = get_logger(__name__)
metrics = get_metrics()

router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections."""
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        metrics.increment("ws.connections.active")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            metrics.decrement("ws.connections.active")

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass


manager = ConnectionManager()


async def _authorize_websocket(websocket: WebSocket) -> bool:
    """Require a valid bearer/query token for production WebSocket clients."""
    if get_settings().env != "production":
        return True

    authorization = websocket.headers.get("authorization", "")
    token = websocket.query_params.get("token")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if not token or verify_token(token) is None:
        await websocket.close(code=1008, reason="Authentication required")
        return False
    return True


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket, session_id: str | None = None):
    """
    WebSocket endpoint that streams VoxOS events to the UI.
    """
    if not await _authorize_websocket(websocket):
        return

    await manager.connect(websocket)
    bus = get_event_bus()

    # Create a queue to receive events from the bus
    queue: asyncio.Queue = asyncio.Queue()

    async def event_callback(event):
        # Filter by session_id if provided
        if session_id and event.session_id and event.session_id != session_id:
            return
        await queue.put(event)

    # Subscribe to all VoxOS events
    bus.subscribe_all(event_callback)
    
    try:
        while True:
            # Wait for an event from the bus
            event = await queue.get()
            
            # Send to client
            await websocket.send_text(json.dumps(event.to_dict()))
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        log.error("ws_error", error=str(e))
        manager.disconnect(websocket)
    finally:
        # Cleanup subscription
        bus.unsubscribe_all(event_callback)
