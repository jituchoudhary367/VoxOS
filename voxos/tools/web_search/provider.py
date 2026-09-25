from __future__ import annotations

import asyncio
from typing import Any

try:
    from ddgs import DDGS  # the maintained package
except ImportError:
    try:
        # Renamed to ddgs; the old package now returns no results.
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

from voxos.tools.web_search.base import SearchProvider, SearchResult
from voxos.observability.logger import get_logger

log = get_logger(__name__)


class DuckDuckGoSearchProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if DDGS is None:
            raise RuntimeError("ddgs is not installed.")
            
        loop = asyncio.get_running_loop()
        
        def _search() -> list[SearchResult]:
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=min(max_results, 10)):
                    results.append(SearchResult(
                        title=r.get("title", ""),
                        url=r.get("href", ""),
                        body=r.get("body", "")
                    ))
            return results

        try:
            results = await loop.run_in_executor(None, _search)
            return results
        except Exception as e:
            log.error("duckduckgo_search_failed", query=query, error=str(e))
            raise
