"""
System Tools — safe, read-only system information.

These are Stage 1's demonstration tools: LOW risk, no side effects,
no approval required. They verify the full tool → Overseer pipeline.

Tools:
    system.get_info       — OS, Python, hostname, uptime
    system.get_time       — Current datetime in requested timezone/format
    system.get_resources  — CPU, memory, disk usage
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from typing import Any, Literal

import psutil
from pydantic import BaseModel, Field

from voxos.tools.base import (
    BaseTool,
    RiskLevel,
    ToolMetadata,
    ToolResult,
    VerificationResult,
    VerificationType,
)


# ─────────────────────────────────────────────────────────────────────────────
# system.get_info
# ─────────────────────────────────────────────────────────────────────────────

class GetSystemInfoInput(BaseModel):
    """No input required for system info."""
    pass


class GetSystemInfoTool(BaseTool):
    """Returns OS, Python, hostname, and uptime information."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="system.get_info",
            description="Get system information: OS, Python version, hostname, uptime",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=5,
            category="system",
            tags=["read-only", "safe", "info"],
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return GetSystemInfoInput

    async def _execute(self, args: GetSystemInfoInput) -> ToolResult:
        boot_time = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        uptime_seconds = (now - boot_time).total_seconds()

        data = {
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor() or "unknown",
            },
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "executable": sys.executable,
            },
            "host": {
                "hostname": platform.node(),
                "uptime_seconds": int(uptime_seconds),
                "uptime_human": _format_uptime(uptime_seconds),
                "boot_time": boot_time.isoformat(),
            },
            "voxos": {
                "version": "0.1.0",
                "pid": os.getpid(),
            },
        }
        return ToolResult.ok(data=data)

    async def verify(self, args: GetSystemInfoInput, result: ToolResult) -> VerificationResult:
        if not result.success or not result.data:
            return VerificationResult.fail("No data returned")
        data = result.data
        checks = [
            {"check": "os_present", "passed": "os" in data},
            {"check": "host_present", "passed": "host" in data},
            {"check": "hostname_non_empty", "passed": bool(data.get("host", {}).get("hostname"))},
        ]
        failed = [c for c in checks if not c["passed"]]
        if failed:
            return VerificationResult.fail(
                reason=f"Failed checks: {[c['check'] for c in failed]}",
                checks=checks,
            )
        return VerificationResult.ok(checks=checks)


# ─────────────────────────────────────────────────────────────────────────────
# system.get_time
# ─────────────────────────────────────────────────────────────────────────────

class GetCurrentTimeInput(BaseModel):
    format: str = Field(
        default="%Y-%m-%d %H:%M:%S %Z",
        description="strftime format string",
    )
    timezone_name: str = Field(
        default="UTC",
        description="Timezone name (e.g. 'UTC', 'Asia/Kolkata', 'US/Eastern')",
    )


class GetCurrentTimeTool(BaseTool):
    """Returns the current date and time."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="system.get_time",
            description="Get the current date and time in a specified timezone",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=2,
            category="system",
            tags=["read-only", "safe", "time"],
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return GetCurrentTimeInput

    async def _execute(self, args: GetCurrentTimeInput) -> ToolResult:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(args.timezone_name)
        except Exception:
            tz = timezone.utc

        now = datetime.now(tz)
        return ToolResult.ok(data={
            "datetime": now.isoformat(),
            "formatted": now.strftime(args.format),
            "timezone": str(tz),
            "unix_timestamp": int(now.timestamp()),
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "hour": now.hour,
            "minute": now.minute,
            "weekday": now.strftime("%A"),
        })


# ─────────────────────────────────────────────────────────────────────────────
# system.get_resources
# ─────────────────────────────────────────────────────────────────────────────

class GetSystemResourcesInput(BaseModel):
    include_processes: bool = Field(
        default=False,
        description="Include top 5 CPU-consuming processes",
    )


class GetSystemResourcesTool(BaseTool):
    """Returns CPU, memory, and disk usage statistics."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="system.get_resources",
            description="Get current CPU, memory, and disk usage statistics",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=5,
            category="system",
            tags=["read-only", "safe", "resources", "monitoring"],
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return GetSystemResourcesInput

    async def _execute(self, args: GetSystemResourcesInput) -> ToolResult:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        battery = psutil.sensors_battery()
        net_io = psutil.net_io_counters()
        disk_io = psutil.disk_io_counters()

        data: dict[str, Any] = {
            "cpu": {
                "percent": cpu_percent,
                "count_logical": psutil.cpu_count(logical=True),
                "count_physical": psutil.cpu_count(logical=False),
            },
            "memory": {
                "total_gb": round(memory.total / 1_073_741_824, 2),
                "available_gb": round(memory.available / 1_073_741_824, 2),
                "used_gb": round(memory.used / 1_073_741_824, 2),
                "percent": memory.percent,
            },
            "disk": {
                "total_gb": round(disk.total / 1_073_741_824, 2),
                "free_gb": round(disk.free / 1_073_741_824, 2),
                "used_gb": round(disk.used / 1_073_741_824, 2),
                "percent": disk.percent,
            },
            "battery": {
                "percent": battery.percent if battery else None,
                "power_plugged": battery.power_plugged if battery else None,
            },
            "network": {
                "bytes_sent": net_io.bytes_sent if net_io else 0,
                "bytes_recv": net_io.bytes_recv if net_io else 0,
            },
            "disk_io": {
                "read_bytes": disk_io.read_bytes if disk_io else 0,
                "write_bytes": disk_io.write_bytes if disk_io else 0,
            }
        }

        if args.include_processes:
            procs = []
            for proc in sorted(
                psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
                key=lambda p: p.info.get("cpu_percent") or 0,
                reverse=True,
            )[:5]:
                procs.append({
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "cpu_percent": proc.info.get("cpu_percent", 0),
                    "memory_percent": round(proc.info.get("memory_percent") or 0, 2),
                })
            data["top_processes"] = procs

        return ToolResult.ok(data=data)

    async def verify(self, args: GetSystemResourcesInput, result: ToolResult) -> VerificationResult:
        if not result.success or not result.data:
            return VerificationResult.fail("No data returned")
        data = result.data
        checks = [
            {"check": "cpu_present", "passed": "cpu" in data},
            {"check": "memory_present", "passed": "memory" in data},
            {"check": "cpu_valid_range", "passed": 0 <= data.get("cpu", {}).get("percent", -1) <= 100},
        ]
        failed = [c for c in checks if not c["passed"]]
        if failed:
            return VerificationResult.fail(
                reason=f"Invalid resource data: {[c['check'] for c in failed]}",
                checks=checks,
            )
        return VerificationResult.ok(checks=checks)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _format_uptime(seconds: float) -> str:
    """Format uptime seconds as human-readable string."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ─────────────────────────────────────────────────────────────────────────────
# Volume Control Tools
# ─────────────────────────────────────────────────────────────────────────────

class SetVolumeArgs(BaseModel):
    level: int = Field(..., description="Volume level from 0 to 100")

class SetVolumeTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.set_volume",
            description="Set the system audio volume (0-100).",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=5,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return SetVolumeArgs

    async def _execute(self, args: SetVolumeArgs) -> ToolResult:
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            
            level = max(0, min(100, args.level))
            scalar_vol = level / 100.0
            volume.SetMasterVolumeLevelScalar(scalar_vol, None)
            
            return ToolResult.ok(data={"volume": level})
        except ImportError:
            return ToolResult.fail("pycaw is not installed.", "MISSING_DEP")
        except Exception as e:
            return ToolResult.fail(f"Failed to set volume: {str(e)}")

class MuteVolumeArgs(BaseModel):
    mute: bool = Field(True, description="True to mute, False to unmute.")

class MuteVolumeTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.mute_volume",
            description="Mute or unmute the system audio.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=5,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MuteVolumeArgs

    async def _execute(self, args: MuteVolumeArgs) -> ToolResult:
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            
            volume.SetMute(1 if args.mute else 0, None)
            return ToolResult.ok(data={"muted": args.mute})
        except Exception as e:
            return ToolResult.fail(f"Failed to mute volume: {str(e)}")
