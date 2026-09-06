from __future__ import annotations

from abc import ABC, abstractmethod
from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    body: str


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Execute a search query and return a list of SearchResults."""
        pass
