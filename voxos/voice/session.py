"""
Voice Session Management.

Manages active voice sessions, linking WebSocket connections
to their corresponding VoicePipelines.
"""

from __future__ import annotations

import uuid
from typing import Any

from voxos.observability.logger import get_logger
from voxos.voice.pipeline import VoicePipeline

log = get_logger(__name__)


class VoiceSessionManager:
    """Manages active voice sessions."""

    def __init__(self) -> None:
        self.active_sessions: dict[str, VoicePipeline] = {}

    def create_session(self, session_id: str | None = None) -> VoicePipeline:
        """Create a new voice session."""
        sid = session_id or str(uuid.uuid4())
        pipeline = VoicePipeline(session_id=sid)
        self.active_sessions[sid] = pipeline
        log.info("voice_session_created", session_id=sid)
        return pipeline

    def get_session(self, session_id: str) -> VoicePipeline | None:
        """Get an active session by ID."""
        return self.active_sessions.get(session_id)

    async def end_session(self, session_id: str) -> bool:
        """End and cleanup a voice session."""
        if session_id in self.active_sessions:
            pipeline = self.active_sessions.pop(session_id)
            await pipeline.stop()
            log.info("voice_session_ended", session_id=session_id)
            return True
        return False


# Singleton
_session_manager: VoiceSessionManager | None = None

def get_voice_session_manager() -> VoiceSessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = VoiceSessionManager()
    return _session_manager
