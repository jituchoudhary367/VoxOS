"""
"What's on my screen?" for the Chat page: capture the whole screen, then ask
the configured vision model about it (Groq, or your local model in Local
mode). The screenshot is the same one the agents' vision.look tool takes.
"""

from __future__ import annotations

import asyncio
import base64

PROMPT = (
    "You are looking at the user's computer screen. {question} "
    "Describe what is open and what the user seems to be doing, in two to four short sentences. "
    "Name apps, sites and any clearly readable headline or error. Don't guess at tiny text."
)


async def capture_screen() -> tuple[bytes, str]:
    """(JPEG bytes for the chat thumbnail, data URL for the model)."""
    from voxos.tools.vision.tools import capture

    data_url, _source = await capture("screen")
    return base64.b64decode(data_url.split(",", 1)[1]), data_url


async def describe_screen(data_url: str, question: str, timeout: float = 90.0) -> str:
    from voxos.llm.base import LLMConfig, LLMMessage
    from voxos.llm.router import get_llm_router
    from voxos.tools.vision.tools import _THINK

    router = get_llm_router()
    message = LLMMessage(role="user", content=[  # type: ignore[arg-type]
        {"type": "text", "text": PROMPT.format(question=question)},
        {"type": "image_url", "image_url": {"url": data_url}},
    ])
    config = LLMConfig(model=router.vision_model, temperature=0.2, max_tokens=400)
    response = await asyncio.wait_for(router.provider.complete([message], config), timeout)
    return _THINK.sub("", response.content or "").strip() or "I couldn't make out the screen."
