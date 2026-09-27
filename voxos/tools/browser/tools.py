"""
Browser Automation Tools — First-Class Browser Control.

All browser tools share a singleton Playwright browser session.
The browser runs VISIBLE (headless=False) so the user sees what happens.

Tools:
    browser.open         — Navigate to a URL
    browser.search       — Search Google or another engine
    browser.click        — Click an element by text, role, or selector
    browser.type         — Type text into an element
    browser.extract      — Extract text from the page
    browser.new_tab      — Open a new browser tab
    browser.close_tab    — Close the current tab
    browser.back         — Navigate back
    browser.scroll       — Scroll the page
    browser.screenshot   — Take a screenshot of the browser
"""

from __future__ import annotations

import asyncio
from typing import Any
from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.browser.tabs import current_tab
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationResult, VerificationType

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Playwright Session Manager
# ---------------------------------------------------------------------------

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

_playwright_context_manager = None
_playwright_instance = None
_browser_instance = None
_context_instance = None
_page_instance = None
_init_lock = asyncio.Lock()
# One tab per worker / command (see voxos.tools.browser.tabs).
_tabs: dict[str, Any] = {}
_last_page = None


async def _get_browser_session(navigate: bool = False) -> "Page":
    """The page the current worker or command should use.

    A tool that navigates (open, search) gets its own tab the first time it
    runs under a tab key, so parallel workers and consecutive commands no
    longer overwrite each other's pages; the untouched start page is used
    for the first one. A tool that only acts on a page (click, scroll,
    extract) uses its key's tab, or else the most recently used tab, so
    "now scroll down" follows the last search.
    """
    global _last_page
    main = await _ensure_browser()
    key = current_tab()
    page = _tabs.get(key) if key is not None else None
    if page is not None and page.is_closed():
        page = None
    if page is None:
        if key is not None and navigate:
            claimed = any(p is main for p in _tabs.values())
            if not claimed and not main.is_closed() and main.url == "about:blank":
                page = main
            else:
                page = await _context_instance.new_page()
            _tabs[key] = page
            log.info("browser_tab_assigned", key=key, tabs=len(_tabs), reused_start_page=page is main)
            try:
                await page.bring_to_front()
            except Exception:
                pass
        elif _last_page is not None and not _last_page.is_closed():
            page = _last_page
        else:
            page = main
    _last_page = page
    return page


async def _ensure_browser() -> "Page":
    """Get or create a persistent visible Playwright session; returns its start page."""
    global _playwright_context_manager, _playwright_instance, _browser_instance, _context_instance, _page_instance

    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "playwright is not installed. Run: pip install playwright && playwright install chromium"
        )

    async with _init_lock:
        # Reuse existing page only while both the page and browser are alive.
        if _page_instance is not None:
            try:
                page_closed = _page_instance.is_closed()
                browser_connected = (
                    _browser_instance is not None and _browser_instance.is_connected()
                )
                if not page_closed and browser_connected:
                    return _page_instance
            except Exception:
                pass
            _page_instance = None
            _context_instance = None

        if _browser_instance is not None and not _browser_instance.is_connected():
            _browser_instance = None
            _context_instance = None

        if _playwright_context_manager is None:
            _playwright_context_manager = async_playwright()
            _playwright_instance = await _playwright_context_manager.start()

        if _browser_instance is None or not _browser_instance.is_connected():
            _browser_instance = await _playwright_instance.chromium.launch(
                headless=False,  # Visible browser
                channel="chrome", # Use the system's Google Chrome
                args=["--start-maximized"],
            )

        _context_instance = await _browser_instance.new_context(
            viewport=None,  # Use maximized window
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        _page_instance = await _context_instance.new_page()
        _tabs.clear()  # pages of a previous browser are gone

    return _page_instance


async def _close_browser_session() -> None:
    """Clean up the browser session."""
    global _playwright_context_manager, _playwright_instance, _browser_instance, _context_instance, _page_instance
    if _browser_instance:
        await _browser_instance.close()
    if _playwright_context_manager:
        await _playwright_context_manager.stop()
    _playwright_context_manager = _playwright_instance = _browser_instance = _context_instance = _page_instance = None
    _tabs.clear()
    global _last_page
    _last_page = None


# ---------------------------------------------------------------------------
# browser.open
# ---------------------------------------------------------------------------

class BrowserOpenArgs(BaseModel):
    url: str = Field(..., description="The URL to navigate to.")


class BrowserOpenTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.open",
            description="Open a URL in the browser and wait for it to load.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=30,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserOpenArgs

    async def _execute(self, args: BrowserOpenArgs) -> ToolResult:
        try:
            url = args.url
            if url != "about:blank" and not url.startswith("http"):
                url = "https://" + url

            page = await _get_browser_session(navigate=True)
            log.info("browser_navigate", url=url)
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            title = await page.title()
            actual_url = page.url
            return ToolResult.ok(data={"title": title, "url": actual_url})
        except Exception as e:
            log.exception("browser_open_error", url=args.url, error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")

    async def verify(self, args: BrowserOpenArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Navigation failed")
        try:
            page = await _get_browser_session()
            current_url = page.url
            if args.url == "about:blank" and current_url == "about:blank":
                return VerificationResult.ok(checks=[{"check": "browser_opened", "passed": True}])
            if current_url and current_url != "about:blank":
                return VerificationResult.ok(checks=[{"check": "page_loaded", "passed": True, "url": current_url}])
            return VerificationResult.fail("Page URL is blank after navigation")
        except Exception as e:
            return VerificationResult.fail(str(e))


# ---------------------------------------------------------------------------
# browser.search
# ---------------------------------------------------------------------------

class BrowserSearchArgs(BaseModel):
    query: str = Field(..., description="The search query.")
    engine: str = Field(default="bing", description="Search engine: 'google', 'youtube', 'wikipedia', 'bing'")


class BrowserSearchTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.search",
            description="Search the web using Google, YouTube, Wikipedia, or Bing.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserSearchArgs

    async def _execute(self, args: BrowserSearchArgs) -> ToolResult:
        import urllib.parse
        q = urllib.parse.quote_plus(args.query)

        search_urls = {
            "google": f"https://www.google.com/search?q={q}",
            "youtube": f"https://www.youtube.com/results?search_query={q}",
            "wikipedia": f"https://en.wikipedia.org/wiki/Special:Search?search={q}",
            "bing": f"https://www.bing.com/search?q={q}",
        }

        engine = args.engine.lower()
        url = search_urls.get(engine, search_urls["bing"])

        try:
            page = await _get_browser_session(navigate=True)
            log.info("browser_search", engine=engine, query=args.query)
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            title = await page.title()
            if engine == "google" and "/sorry/" in page.url:
                return ToolResult.fail(
                    "Google blocked this automated search. Try again with Bing.",
                    "SEARCH_BLOCKED",
                )
            return ToolResult.ok(data={"title": title, "url": page.url, "engine": engine, "query": args.query})
        except Exception as e:
            log.exception("browser_search_error", query=args.query, error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")


# ---------------------------------------------------------------------------
# browser.click
# ---------------------------------------------------------------------------

class BrowserClickArgs(BaseModel):
    selector: str | None = Field(None, description="CSS selector to click.")
    text: str | None = Field(None, description="Text of the element to click (uses :has-text).")
    role: str | None = Field(None, description="ARIA role (e.g. 'button', 'link').")


class BrowserClickTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.click",
            description="Click an element on the page by CSS selector, text content, or ARIA role.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=15,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserClickArgs

    async def _execute(self, args: BrowserClickArgs) -> ToolResult:
        try:
            page = await _get_browser_session()

            if args.selector:
                await page.click(args.selector, timeout=10000)
                return ToolResult.ok(data={"clicked": args.selector})

            if args.text:
                locator = page.get_by_text(args.text, exact=False)
                await locator.first.click(timeout=10000)
                return ToolResult.ok(data={"clicked_text": args.text})

            if args.role:
                locator = page.get_by_role(args.role)
                await locator.first.click(timeout=10000)
                return ToolResult.ok(data={"clicked_role": args.role})

            return ToolResult.fail("Must specify selector, text, or role.", "MISSING_TARGET")

        except Exception as e:
            log.exception("browser_click_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")


# ---------------------------------------------------------------------------
# browser.type
# ---------------------------------------------------------------------------

class BrowserTypeArgs(BaseModel):
    text: str = Field(..., description="Text to type into the active element or selector.")
    selector: str | None = Field(None, description="CSS selector to focus before typing. If None, types at current focus.")
    press_enter: bool = Field(False, description="Press Enter after typing.")


class BrowserTypeTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.type",
            description="Type text into an input field. Optionally press Enter after.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=15,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserTypeArgs

    async def _execute(self, args: BrowserTypeArgs) -> ToolResult:
        try:
            page = await _get_browser_session()

            if args.selector:
                await page.fill(args.selector, args.text, timeout=10000)
            else:
                await page.keyboard.type(args.text)

            if args.press_enter:
                await page.keyboard.press("Enter")
                await asyncio.sleep(0.5)

            return ToolResult.ok(data={"typed": args.text, "pressed_enter": args.press_enter})
        except Exception as e:
            log.exception("browser_type_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")


# ---------------------------------------------------------------------------
# browser.extract
# ---------------------------------------------------------------------------

class BrowserExtractArgs(BaseModel):
    selector: str | None = Field(None, description="CSS selector to extract text from. Default: body.")
    max_chars: int = Field(10000, description="Maximum characters to return.")


class BrowserExtractTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.extract",
            description="Extract visible text content from the current browser page.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserExtractArgs

    async def _execute(self, args: BrowserExtractArgs) -> ToolResult:
        try:
            page = await _get_browser_session()
            if not page.url or page.url == "about:blank":
                return ToolResult.fail("No page loaded", "NO_PAGE")

            selector = args.selector or "body"
            text = await page.evaluate(f"""() => {{
                if ("{selector}" === "body") {{
                    return document.body.innerText;
                }}
                const els = document.querySelectorAll("{selector}");
                return Array.from(els).map(el => el.innerText).join('\\n');
            }}""")

            if not text:
                return ToolResult.fail(f"Selector '{selector}' not found or empty", "NOT_FOUND")

            return ToolResult.ok(data={"text": text[:args.max_chars], "url": page.url})
        except Exception as e:
            log.exception("browser_extract_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")


# ---------------------------------------------------------------------------
# browser.new_tab
# ---------------------------------------------------------------------------

class BrowserNewTabArgs(BaseModel):
    url: str | None = Field(None, description="Optional URL to navigate to in the new tab.")


class BrowserNewTabTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.new_tab",
            description="Open a new browser tab, optionally navigating to a URL.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=15,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserNewTabArgs

    async def _execute(self, args: BrowserNewTabArgs) -> ToolResult:
        global _page_instance
        try:
            page = await _get_browser_session()
            ctx = page.context
            new_page = await ctx.new_page()
            _page_instance = new_page  # Switch active page

            if args.url:
                url = args.url if args.url.startswith("http") else "https://" + args.url
                await new_page.goto(url, wait_until="domcontentloaded", timeout=25000)

            return ToolResult.ok(data={"opened_tab": True, "url": new_page.url})
        except Exception as e:
            log.exception("browser_new_tab_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")


# ---------------------------------------------------------------------------
# browser.back
# ---------------------------------------------------------------------------

class BrowserBackArgs(BaseModel):
    pass


class BrowserBackTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.back",
            description="Navigate back in the browser history.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=15,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserBackArgs

    async def _execute(self, args: BrowserBackArgs) -> ToolResult:
        try:
            page = await _get_browser_session()
            await page.go_back(timeout=10000)
            return ToolResult.ok(data={"url": page.url})
        except Exception as e:
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")


# ---------------------------------------------------------------------------
# browser.scroll
# ---------------------------------------------------------------------------

class BrowserScrollArgs(BaseModel):
    direction: str = Field(default="down", description="'down', 'up', 'left', 'right'")
    amount: int = Field(default=500, description="Pixels to scroll")


class BrowserScrollTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.scroll",
            description="Scroll the browser page up or down.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserScrollArgs

    async def _execute(self, args: BrowserScrollArgs) -> ToolResult:
        try:
            page = await _get_browser_session()
            direction = args.direction.lower()
            amount = args.amount

            delta_x, delta_y = 0, 0
            if direction == "down":
                delta_y = amount
            elif direction == "up":
                delta_y = -amount
            elif direction == "right":
                delta_x = amount
            elif direction == "left":
                delta_x = -amount

            await page.evaluate(f"window.scrollBy({delta_x}, {delta_y})")
            return ToolResult.ok(data={"scrolled": direction, "pixels": amount})
        except Exception as e:
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")


# ---------------------------------------------------------------------------
# browser.screenshot
# ---------------------------------------------------------------------------

class BrowserScreenshotArgs(BaseModel):
    save_path: str | None = Field(None, description="File path to save screenshot. Defaults to temp file.")


class BrowserScreenshotTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.screenshot",
            description="Take a screenshot of the current browser page.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=15,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserScreenshotArgs

    async def _execute(self, args: BrowserScreenshotArgs) -> ToolResult:
        import tempfile
        import os
        from datetime import datetime as dt
        try:
            page = await _get_browser_session()
            if args.save_path:
                filepath = args.save_path
            else:
                timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
                filepath = os.path.join(tempfile.gettempdir(), f"voxos_browser_{timestamp}.png")

            await page.screenshot(path=filepath, full_page=False)
            return ToolResult.ok(data={"filepath": filepath, "url": page.url})
        except Exception as e:
            log.exception("browser_screenshot_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")
