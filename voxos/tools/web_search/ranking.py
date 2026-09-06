from typing import Sequence
from urllib.parse import urlparse
from voxos.tools.web_search.base import SearchResult

def deduplicate_results(results: Sequence[SearchResult]) -> list[SearchResult]:
    """
    Deduplicate search results by URL.
    Keeps the first occurrence of each URL.
    """
    seen_urls = set()
    deduped = []
    
    for r in results:
        # Normalize URL by removing fragments and query params for basic deduplication
        # (Though query params are important for many dynamic sites, so we'll just use the raw URL for now)
        url = r.url
        if url not in seen_urls:
            seen_urls.add(url)
            deduped.append(r)
            
    return deduped

def rank_results(results: Sequence[SearchResult], query: str) -> list[SearchResult]:
    """
    Rank search results based on basic heuristics.
    (Placeholder for more advanced TF-IDF or embedding-based ranking).
    """
    deduped = deduplicate_results(results)
    
    query_terms = set(query.lower().split())
    
    def score_result(r: SearchResult) -> float:
        score = 0.0
        title_lower = r.title.lower()
        body_lower = r.body.lower()
        
        # Exact term matches
        for term in query_terms:
            if term in title_lower:
                score += 2.0
            if term in body_lower:
                score += 1.0
                
        # Down-rank typical spam/SEO domains if we had a blacklist
        # Example: if 'pinterest' in r.url: score -= 5.0
        
        return score
        
    return sorted(deduped, key=score_result, reverse=True)
