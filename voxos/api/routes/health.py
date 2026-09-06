"""Health check routes."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from voxos.llm.router import get_llm_router
from voxos.observability.metrics import get_metrics
from voxos.tools.registry import get_tool_registry

router = APIRouter()
metrics = get_metrics()


class HealthResponse(BaseModel):
    status: str
    version: str = "0.1.0"
    components: dict[str, str]
    metrics: dict | None = None


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Comprehensive health check endpoint.
    
    Returns status of all critical components.
    """
    components: dict[str, str] = {}

    # LLM Router
    try:
        router_singleton = get_llm_router()
        llm_ok = await router_singleton.health_check()
        components["llm"] = "ok" if llm_ok else "degraded"
    except Exception as e:
        components["llm"] = f"error: {str(e)[:50]}"

    # Tool Registry
    try:
        registry = get_tool_registry()
        components["tools"] = f"ok ({registry.tool_count()} registered)"
    except Exception as e:
        components["tools"] = f"error: {str(e)[:50]}"

    # Event Bus
    try:
        from voxos.core.events.bus import get_event_bus
        bus = get_event_bus()
        components["event_bus"] = "ok" if bus._connected else "degraded (no redis)"
    except Exception:
        components["event_bus"] = "unavailable"

    all_ok = all(v.startswith("ok") for v in components.values())
    overall_status = "healthy" if all_ok else "degraded"

    return HealthResponse(
        status=overall_status,
        components=components,
        metrics=metrics.snapshot(),
    )


@router.get("/", include_in_schema=False)
async def root():
    return {"message": "VoxOS API — v0.1.0", "docs": "/docs"}
