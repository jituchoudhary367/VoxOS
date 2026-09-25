"""
Web Tools.
Allows VoxOS to search the web and scrape basic text content.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

try:
    from ddgs import DDGS  # the maintained package
except ImportError:
    try:
        # Renamed to ddgs; the old package now returns no results.
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType

log = get_logger(__name__)


# --- Web Search Tool ---

class WebSearchArgs(BaseModel):
    query: str = Field(..., description="The search query string")
    max_results: int = Field(5, description="Maximum number of results to return (max 10)")


class WebSearchTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="web.search",
            description="Search the web using DuckDuckGo to find recent information or URLs.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return WebSearchArgs

    async def _execute(self, args: WebSearchArgs) -> ToolResult:
        if DDGS is None:
            return ToolResult.fail(
                error="ddgs is not installed. Install with 'pip install ddgs'.",
                error_code="DEPENDENCY_MISSING",
            )
            
        try:
            log.info("web_search_started", query=args.query)
            
            # DDGS is synchronous in its basic usage, run in executor
            loop = asyncio.get_running_loop()
            
            def _search():
                results = []
                with DDGS() as ddgs:
                    for r in ddgs.text(args.query, max_results=min(args.max_results, 10)):
                        results.append(r)
                return results

            results = await loop.run_in_executor(None, _search)
            
            log.info("web_search_completed", query=args.query, results_count=len(results))
            return ToolResult.ok(data={"results": results})
            
        except Exception as e:
            log.exception("web_search_error", query=args.query, error=str(e))
            return ToolResult.fail(error=str(e), error_code="SEARCH_ERROR")


# --- Web Scrape Tool ---

class WebScrapeArgs(BaseModel):
    url: str = Field(..., description="The URL of the webpage to scrape")


class WebScrapeTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="web.scrape",
            description="Scrape and extract the text content of a webpage.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return WebScrapeArgs

    async def _execute(self, args: WebScrapeArgs) -> ToolResult:
        try:
            log.info("web_scrape_started", url=args.url)
            
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(
                    args.url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VoxOS/1.0"}
                )
                response.raise_for_status()
                
            # Parse HTML and extract text
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Remove scripts and styles
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()
                
            text = soup.get_text(separator="\n")
            
            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)
            
            # Truncate to avoid blowing up context window (approx 15,000 chars)
            max_chars = 15000
            truncated = False
            if len(text) > max_chars:
                text = text[:max_chars] + "\n...[TRUNCATED]..."
                truncated = True
                
            log.info("web_scrape_completed", url=args.url, chars=len(text), truncated=truncated)
            
            return ToolResult.ok(
                data={
                    "url": args.url,
                    "title": soup.title.string if soup.title else "",
                    "content": text,
                    "truncated": truncated
                }
            )
            
        except httpx.HTTPStatusError as e:
            return ToolResult.fail(
                error=f"HTTP Error {e.response.status_code}: {e.response.reason_phrase}",
                error_code="HTTP_ERROR",
            )
        except Exception as e:
            log.exception("web_scrape_error", url=args.url, error=str(e))
            return ToolResult.fail(error=str(e), error_code="SCRAPE_ERROR")
