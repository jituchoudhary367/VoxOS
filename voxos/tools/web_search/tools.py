from __future__ import annotations

from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType
from voxos.tools.web_search.provider import DuckDuckGoSearchProvider
from voxos.tools.web_search.ranking import rank_results
from voxos.tools.web_search.extraction import extract_content_from_url

log = get_logger(__name__)


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="The search query string")
    max_results: int = Field(5, description="Maximum number of results to return (max 10)")


class WebSearchTool(BaseTool):
    def __init__(self):
        self._provider = DuckDuckGoSearchProvider()
        
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="web.search",
            description="Search the web to find recent information or URLs.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="web",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return WebSearchArgs

    async def _execute(self, args: WebSearchArgs) -> ToolResult:
        try:
            log.info("web_search_started", query=args.query)
            
            raw_results = await self._provider.search(args.query, args.max_results)
            ranked_results = rank_results(raw_results, args.query)
            
            # Format results into dictionaries
            formatted = [
                {
                    "title": r.title,
                    "url": r.url,
                    "snippet": r.body
                }
                for r in ranked_results
            ]
            
            log.info("web_search_completed", query=args.query, results_count=len(formatted))
            return ToolResult.ok(data={"results": formatted})
            
        except Exception as e:
            log.exception("web_search_error", query=args.query, error=str(e))
            error_str = str(e)
            if "202 Ratelimit" in error_str or "RateLimit" in type(e).__name__:
                return ToolResult.fail(
                    error="Search provider rate limit exceeded. Please try again later.",
                    error_code="RATE_LIMITED"
                )
            return ToolResult.fail(error=error_str, error_code="SEARCH_ERROR")


class WebExtractArgs(BaseModel):
    url: str = Field(..., description="The URL of the webpage to extract content from")


class WebExtractTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="web.extract",
            description="Extract the main text content of a webpage (no Javascript execution).",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="web",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return WebExtractArgs

    async def _execute(self, args: WebExtractArgs) -> ToolResult:
        try:
            log.info("web_extract_started", url=args.url)
            
            title, content = await extract_content_from_url(args.url)
            
            truncated = content.endswith("...[TRUNCATED]...")
            log.info("web_extract_completed", url=args.url, chars=len(content), truncated=truncated)
            
            return ToolResult.ok(
                data={
                    "url": args.url,
                    "title": title or "",
                    "content": content,
                    "truncated": truncated
                }
            )
            
        except Exception as e:
            log.exception("web_extract_error", url=args.url, error=str(e))
            return ToolResult.fail(error=str(e), error_code="EXTRACT_ERROR")
