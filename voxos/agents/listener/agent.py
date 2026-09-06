"""
Listener Agent — "What does the user want?"

Responsibilities:
- Understand the user's request
- Detect intent and language
- Extract entities
- Normalize to language-independent internal representation
- Handle ambiguous or multi-part requests
- NEVER execute tools

Input: raw text or voice transcript
Output: structured intent + entities + language
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from voxos.agents.base import BaseAgent
from voxos.core.events.bus import JarvisEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.llm.base import LLMMessage
from voxos.llm.router import TaskComplexity, get_llm_router
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()


# ─────────────────────────────────────────────────────────────────────────────
# I/O schemas
# ─────────────────────────────────────────────────────────────────────────────

class ListenerInput(BaseModel):
    text: str
    session_id: str | None = None
    language: str = "auto"
    conversation_history: list[dict[str, str]] = Field(default_factory=list)


class ListenerOutput(BaseModel):
    intent: str
    language: str = "en"
    entities: dict[str, Any] = Field(default_factory=dict)
    requires_execution: bool = True
    confidence: float = 1.0
    clarification_needed: bool = False
    clarification_question: str | None = None
    raw_text: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Listener Agent for VoxOS, an autonomous AI assistant.

Your ONLY job is to understand the user's request and extract:
1. INTENT — what the user wants to accomplish (language-independent)
2. LANGUAGE — detected language code (e.g. "en", "hi", "auto")
3. ENTITIES — key named entities in the request
4. REQUIRES_EXECUTION — does this need a tool or can it be answered directly?

IMPORTANT: You must respond ONLY with a valid JSON object. No explanation, no markdown.

Intent naming rules:
- Use snake_case
- Be specific: "open_application", "search_web", "get_system_info", "get_current_time"
- Multilingual equivalents map to the SAME intent:
  "Open Chrome" = "Chrome kholo" = "Chrome खोलो" → intent: "open_application", entity: {application: "chrome"}

Example response:
{
  "intent": "get_current_time",
  "language": "en",
  "entities": {"timezone": "IST"},
  "requires_execution": true,
  "confidence": 0.95,
  "clarification_needed": false
}

Common intents:
- get_current_time, get_system_info, get_resources
- open_application, close_application, list_applications
- search_web, browse_url
- list_files, read_file, create_file, delete_file, move_file
- run_command, check_process
- take_screenshot
- general_conversation (for greetings, questions that need no tool)
- research_topic (complex multi-step research)
- create_document, summarize_document
- check_email, send_email (requires explicit user authorization)
"""


class ListenerAgent(BaseAgent[ListenerInput, ListenerOutput]):
    """
    Understands user intent from voice transcripts or text.
    Never executes tools — only classifies and extracts.
    """

    def __init__(self) -> None:
        self._router = get_llm_router()
        self._bus = get_event_bus()

    @property
    def name(self) -> str:
        return "listener"

    async def process(self, input: ListenerInput) -> ListenerOutput:
        """Classify user intent and extract entities."""
        log.info("listener_processing", session_id=input.session_id, text_length=len(input.text))

        async with metrics.async_timer("agent.latency", agent=self.name):
            output = await self._classify_intent(input)

        # Emit event
        await self._bus.publish(JarvisEvent(
            event_type=EventType.INTENT_DETECTED,
            payload={
                "intent": output.intent,
                "language": output.language,
                "entities": output.entities,
                "confidence": output.confidence,
            },
            session_id=input.session_id,
            agent=self.name,
        ))

        metrics.increment("listener.intents", intent=output.intent)
        log.info(
            "listener_output",
            intent=output.intent,
            language=output.language,
            requires_execution=output.requires_execution,
            confidence=output.confidence,
        )
        return output

    async def _classify_intent(self, input: ListenerInput) -> ListenerOutput:
        """Use LLM to classify intent (or fast-path for obvious patterns)."""
        # Fast path: deterministic patterns that don't need LLM
        fast_result = self._fast_classify(input.text)
        if fast_result:
            log.debug("listener_fast_path", intent=fast_result.intent)
            return fast_result

        # LLM classification
        messages = [
            LLMMessage(role="user", content=input.text),
        ]
        # Add recent conversation context
        for turn in input.conversation_history[-4:]:  # Last 2 turns
            messages.insert(-1, LLMMessage(role=turn["role"], content=turn["content"]))

        config = self._router.config_for(
            complexity=TaskComplexity.SIMPLE,
            response_format={"type": "json_object"},
            system_prompt=SYSTEM_PROMPT,
            temperature=0.0,  # Deterministic classification
            max_tokens=256,
        )

        try:
            response = await self._router.provider.complete(messages, config)
            data = json.loads(response.content)
            return ListenerOutput(
                intent=data.get("intent", "general_conversation"),
                language=data.get("language", "en"),
                entities=data.get("entities", {}),
                requires_execution=data.get("requires_execution", True),
                confidence=float(data.get("confidence", 0.9)),
                clarification_needed=data.get("clarification_needed", False),
                clarification_question=data.get("clarification_question"),
                raw_text=input.text,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning("listener_llm_parse_error", error=str(e), content=response.content[:200])
            # Fallback: treat as general conversation
            return ListenerOutput(
                intent="general_conversation",
                language="en",
                entities={},
                requires_execution=False,
                confidence=0.5,
                raw_text=input.text,
            )

    @staticmethod
    def _fast_classify(text: str) -> ListenerOutput | None:
        """
        Deterministic fast-path for common patterns.
        Avoids an LLM call for simple, obvious requests.
        """
        text_lower = text.lower().strip()

        GREETINGS = {"hi", "hello", "hey", "hey voxos", "hi voxos", "hello voxos"}
        if text_lower in GREETINGS:
            return ListenerOutput(
                intent="general_conversation",
                language="en",
                entities={"type": "greeting"},
                requires_execution=False,
                confidence=1.0,
            )

        TIME_PHRASES = {
            "what time is it", "what's the time", "current time", "time", "time?",
            "what time", "samay kya hai", "time bolo",
        }
        if text_lower in TIME_PHRASES or text_lower.replace("?", "") in TIME_PHRASES:
            return ListenerOutput(
                intent="get_current_time",
                language="en" if not any(w in text_lower for w in ["kya", "bolo", "hai"]) else "hi",
                entities={},
                requires_execution=True,
                confidence=1.0,
            )

        return None
