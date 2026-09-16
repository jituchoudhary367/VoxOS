"""
VoxOS FastAPI Application Factory.

Creates and configures the FastAPI app with:
- CORS middleware
- Structured logging middleware
- Request ID tracking
- Lifespan management (startup/shutdown)
- Route registration
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from voxos.config import get_settings
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.api.deps import get_current_user_permissions

log = get_logger(__name__)
metrics = get_metrics()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    settings = get_settings()
    log.info(
        "vox_starting",
        version="0.1.0",
        environment=settings.env,
        debug=settings.debug,
    )

    # Initialize event bus
    try:
        from voxos.core.events.bus import get_event_bus
        bus = get_event_bus()
        await bus.connect()
        log.info("event_bus_ready")
    except Exception as e:
        log.warning("event_bus_unavailable", error=str(e), note="Running without Redis pub/sub")

    # Initialize tool registry
    from voxos.tools.registry import get_tool_registry
    registry = get_tool_registry()
    log.info("tool_registry_ready", tools=registry.tool_count())

    # Warm up LLM router
    from voxos.llm.router import get_llm_router
    router = get_llm_router()
    log.info("llm_router_ready", provider=router.provider.name)

    log.info("vox_ready", tools=registry.tool_count())

    yield

    # Shutdown
    log.info("vox_shutting_down")
    try:
        from voxos.core.events.bus import get_event_bus
        await get_event_bus().disconnect()
    except Exception:
        pass
    log.info("vox_stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="VoxOS API",
        description="Autonomous Multilingual Desktop AI Agent",
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else ["http://localhost:3000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID + timing middleware
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        start = time.monotonic()

        request_log = log.bind(request_id=request_id)
        request_log.info(
            "request_started",
            method=request.method,
            path=request.url.path,
        )
        response: Response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{duration_ms:.1f}ms"

        request_log.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration_ms, 1),
        )
        metrics.record_latency("api.latency", duration_ms, path=request.url.path)

        return response

    # Register routers
    from voxos.api.routes.chat import router as chat_router
    from voxos.api.routes.health import router as health_router
    from voxos.api.routes.tasks import router as tasks_router
    from voxos.api.routes.approvals import router as approvals_router
    from voxos.api.routes.tools import router as tools_router
    from voxos.api.routes.system import router as system_router
    from voxos.api.routes.memory import router as memory_router
    from voxos.api.routes.audit import router as audit_router
    from voxos.api.websocket import router as ws_router

    API_PREFIX = "/api/v1"
    app.include_router(health_router, tags=["Health"])
    protected = [Depends(get_current_user_permissions)]
    app.include_router(chat_router, prefix=API_PREFIX, tags=["Chat"], dependencies=protected)
    app.include_router(tasks_router, prefix=API_PREFIX, tags=["Tasks"], dependencies=protected)
    app.include_router(approvals_router, prefix=API_PREFIX, tags=["Approvals"], dependencies=protected)
    app.include_router(tools_router, prefix=API_PREFIX, tags=["Tools"], dependencies=protected)
    app.include_router(system_router, prefix=API_PREFIX, tags=["System"], dependencies=protected)
    app.include_router(memory_router, prefix=API_PREFIX, dependencies=protected)
    app.include_router(audit_router, prefix=API_PREFIX, dependencies=protected)
    app.include_router(ws_router, tags=["WebSocket"])

    return app
