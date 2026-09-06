from __future__ import annotations

import asyncio
from typing import Any
from pydantic import BaseModel, Field

try:
    from playwright.async_api import async_playwright, Browser, Page, Playwright
except ImportError:
    async_playwright = None

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType

log = get_logger(__name__)

# --- Global Browser State ---
_playwright: Playwright | None = None
_browser: Browser | None = None
_page: Page | None = None

async def _get_page() -> Page:
    """Get or create the singleton browser page."""
    global _playwright, _browser, _page
    
    if async_playwright is None:
        raise RuntimeError("playwright is not installed. Install with 'pip install playwright' and 'playwright install chromium'")
        
    if _page is None:
        _playwright = await async_playwright().start()
        # Headless for background operation, but can be configured
        _browser = await _playwright.chromium.launch(headless=True)
        context = await _browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) VoxOS/1.0'
        )
        _page = await context.new_page()
        
    return _page


class BrowserOpenArgs(BaseModel):
    url: str = Field(..., description="The URL to navigate to")


class BrowserOpenTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.open",
            description="Open a URL in the browser and wait for it to load.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserOpenArgs

    async def _execute(self, args: BrowserOpenArgs) -> ToolResult:
        try:
            log.info("browser_open_started", url=args.url)
            page = await _get_page()
            await page.goto(args.url, wait_until="domcontentloaded")
            title = await page.title()
            return ToolResult.ok(data={"title": title, "url": page.url})
        except Exception as e:
            log.exception("browser_open_error", url=args.url, error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")


class BrowserExtractArgs(BaseModel):
    selector: str = Field(None, description="CSS selector to extract text from. If null, extracts from body.")


class BrowserExtractTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.extract",
            description="Extract text content from the currently open browser page.",
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
            page = await _get_page()
            if not page.url or page.url == "about:blank":
                return ToolResult.fail("No page is currently open", "NO_PAGE")
                
            selector = args.selector or "body"
            log.info("browser_extract_started", selector=selector)
            
            # Using evaluate to get clean text
            text = await page.evaluate(f'''() => {{
                const el = document.querySelector("{selector}");
                return el ? el.innerText : "";
            }}''')
            
            if not text:
                return ToolResult.fail(f"Selector '{selector}' not found or empty", "NOT_FOUND")
                
            return ToolResult.ok(data={"text": text[:15000]}) # Truncate to save tokens
        except Exception as e:
            log.exception("browser_extract_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")
