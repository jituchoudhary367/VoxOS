"""
YouTube Automation Tools.

Provides deterministic YouTube search and playback via browser automation.
"""

from __future__ import annotations

import asyncio
from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationResult, VerificationType

log = get_logger(__name__)


class YouTubeSearchArgs(BaseModel):
    query: str = Field(..., description="Search query for YouTube.")
    play_first: bool = Field(False, description="Automatically play the first result.")


class YouTubeSearchTool(BaseTool):
    """Search YouTube and optionally play the first result."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.youtube_search",
            description="Search YouTube for a video. Optionally play the first result automatically.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=45,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return YouTubeSearchArgs

    async def _execute(self, args: YouTubeSearchArgs) -> ToolResult:
        from voxos.tools.browser.tools import _get_browser_session
        import urllib.parse

        try:
            q = urllib.parse.quote_plus(args.query)
            url = f"https://www.youtube.com/results?search_query={q}"

            page = await _get_browser_session(navigate=True)
            log.info("youtube_search_started", query=args.query)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)

            # Extract video results
            results_raw = await page.evaluate("""() => {
                const items = document.querySelectorAll('ytd-video-renderer #video-title');
                return Array.from(items).slice(0, 5).map(el => ({
                    title: el.textContent.trim(),
                    href: el.href
                }));
            }""")

            results = [r for r in results_raw if r.get("title") and r.get("href")]

            if args.play_first and results:
                first = results[0]
                await page.goto(first["href"], wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1)
                # Try to click the play button if not auto-playing
                try:
                    await page.click("button.ytp-play-button", timeout=3000)
                except Exception:
                    pass
                return ToolResult.ok(data={
                    "action": "playing",
                    "title": first["title"],
                    "url": page.url,
                    "results": results,
                })

            return ToolResult.ok(data={
                "query": args.query,
                "results": results,
                "url": page.url,
            })

        except Exception as e:
            log.exception("youtube_search_error", query=args.query, error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")

    async def verify(self, args: YouTubeSearchArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("YouTube search failed")
        data = result.data or {}
        url = data.get("url", "")
        if "youtube.com" in url:
            return VerificationResult.ok(checks=[{"check": "on_youtube", "passed": True}])
        return VerificationResult.fail("Not on YouTube after search")


class YouTubePlayArgs(BaseModel):
    video_title: str | None = Field(None, description="Title keyword of the video to play from current search results.")
    video_url: str | None = Field(None, description="Direct YouTube URL to play.")


class YouTubePlayTool(BaseTool):
    """Play a YouTube video from search results or by URL."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.youtube_play",
            description="Play a YouTube video. Use after youtube_search or provide a direct URL.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=30,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return YouTubePlayArgs

    async def _execute(self, args: YouTubePlayArgs) -> ToolResult:
        from voxos.tools.browser.tools import _get_browser_session

        try:
            page = await _get_browser_session(navigate=True)

            if args.video_url:
                url = args.video_url
                if not url.startswith("http"):
                    url = "https://www.youtube.com/watch?v=" + url
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1)
                title = await page.title()
                return ToolResult.ok(data={"playing": True, "title": title, "url": page.url})

            if args.video_title:
                # Find matching link on current results page
                target = args.video_title.lower()
                links = await page.evaluate("""() => {
                    const items = document.querySelectorAll('ytd-video-renderer #video-title');
                    return Array.from(items).map(el => ({title: el.textContent.trim(), href: el.href}));
                }""")

                match = None
                for link in links:
                    if target in link.get("title", "").lower():
                        match = link
                        break

                if match is None and links:
                    match = links[0]  # Play the first result

                if match:
                    await page.goto(match["href"], wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(1)
                    title = await page.title()
                    return ToolResult.ok(data={"playing": True, "title": title, "url": page.url})

                return ToolResult.fail("No matching video found on current page", "NOT_FOUND")

            return ToolResult.fail("Provide either video_title or video_url", "MISSING_ARGS")

        except Exception as e:
            log.exception("youtube_play_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")

    async def verify(self, args: YouTubePlayArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("YouTube play failed")
        from voxos.tools.browser.tools import _get_browser_session
        try:
            page = await _get_browser_session()
            url = page.url
            if "youtube.com/watch" in url:
                return VerificationResult.ok(checks=[{"check": "on_video_page", "passed": True, "url": url}])
            return VerificationResult.fail(f"Not on a YouTube watch page: {url}")
        except Exception as e:
            return VerificationResult.fail(str(e))
