"""
VoxOS main entry point.

Usage:
    python -m voxos
    uvicorn voxos.main:app --host 0.0.0.0 --port 8000 --reload
"""

from voxos.api.app import create_app

app = create_app()


def run() -> None:
    """Run the VoxOS API using the configured server settings."""
    import uvicorn
    from voxos.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "voxos.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
        access_log=False,
    )

if __name__ == "__main__":
    run()
