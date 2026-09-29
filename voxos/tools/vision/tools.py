"""
vision.look — answer a question about what is on screen.

Takes a screenshot of the browser tab Vox is working in (default) or of
the whole screen, and asks a vision model about it. Text on a page is
cheaper to read with browser.extract; this is for layout, images, video
frames and apps outside the browser.

The screenshot goes to the configured LLM provider (the same one every
request already goes to); the whole screen is only captured when asked.
"""

from __future__ import annotations

import base64
import io
import re
from typing import Literal

from pydantic import BaseModel, Field

from voxos.llm.base import LLMConfig, LLMMessage
from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationType

log = get_logger(__name__)

MAX_SIDE = 1280
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class VisionLookArgs(BaseModel):
    question: str = Field(..., description="What to find out from the screen")
    target: Literal["browser", "screen"] = Field(
        default="browser", description="'browser' = the current browser tab, 'screen' = the whole screen"
    )


def _to_jpeg_data_url(png_or_image: object) -> str:
    from PIL import Image

    image = png_or_image if isinstance(png_or_image, Image.Image) else Image.open(io.BytesIO(png_or_image))
    image = image.convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


async def capture(target: str) -> tuple[str, str]:
    """(data URL, what was captured). Falls back to the screen without a browser tab."""
    if target == "browser":
        from voxos.tools.browser import tools as browser
        from voxos.tools.browser.tabs import current_tab

        # The worker's own tab first: asked about the YouTube tab, it used to
        # photograph whichever tab was used last (Amazon), nine times.
        key = current_tab()
        page = (browser.page_for_tab(key) if key else None) or browser._last_page
        if page is not None and not page.is_closed() and page.url != "about:blank":
            return _to_jpeg_data_url(await page.screenshot(type="png")), f"browser tab {page.url}"
    from PIL import ImageGrab

    return _to_jpeg_data_url(ImageGrab.grab()), "whole screen"


class VisionLookTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="vision.look",
            description=(
                "Look at the current browser tab (or the whole screen) and answer a question about "
                "what is visible: layout, images, video, buttons, apps outside the browser."
            ),
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=45,
            category="vision",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return VisionLookArgs

    async def _execute(self, args: VisionLookArgs) -> ToolResult:
        from voxos.llm.router import get_llm_router

        try:
            image_url, source = await capture(args.target)
        except Exception as e:
            return ToolResult.fail(f"Could not take a screenshot: {e}", "SCREENSHOT_FAILED")

        message = LLMMessage(role="user", content=[  # type: ignore[arg-type]
            {"type": "text", "text": f"{args.question}\nAnswer briefly and only from what is visible."},
            {"type": "image_url", "image_url": {"url": image_url}},
        ])
        router = get_llm_router()
        config = LLMConfig(model=router.vision_model, temperature=0.2, max_tokens=700)
        try:
            response = await router.provider.complete([message], config)
        except Exception as e:
            log.warning("vision_look_failed", error=str(e))
            return ToolResult.fail(f"The vision model failed: {e}", "VISION_ERROR")

        answer = _THINK.sub("", response.content or "").strip()
        log.info("vision_look", source=source, question=args.question[:120], answer=answer[:200])
        return ToolResult.ok(data={"answer": answer, "looked_at": source})
