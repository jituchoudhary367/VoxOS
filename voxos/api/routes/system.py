"""System status route."""

from __future__ import annotations

import psutil
from fastapi import APIRouter

from voxos.config import get_settings

router = APIRouter()


@router.get("/system")
async def system_status():
    """Get high-level host system status."""
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage('/').percent,
        "environment": get_settings().environment,
    }
