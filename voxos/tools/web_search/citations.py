import re
from typing import Sequence
from voxos.tools.web_search.base import SearchResult

def generate_citation_reference(url: str, title: str, index: int) -> str:
    """Generate a markdown reference link for a citation."""
    return f"[{index}]: {url} \"{title}\""

def append_citations(text: str, sources: Sequence[SearchResult]) -> str:
    """
    Appends a formatted citations section to the bottom of the text
    based on the provided sources.
    """
    if not sources:
        return text
        
    citations = ["\n\n### References"]
    for i, source in enumerate(sources, 1):
        citations.append(generate_citation_reference(source.url, source.title, i))
        
    return text + "\n".join(citations)

def extract_cited_urls(text: str) -> list[str]:
    """
    Extracts URLs from markdown reference links in the text.
    Matches: [1]: https://example.com "Title"
    """
    pattern = r'^\[\d+\]:\s*(https?://[^\s"]+)'
    urls = []
    for line in text.splitlines():
        match = re.match(pattern, line.strip())
        if match:
            urls.append(match.group(1))
    return urls
