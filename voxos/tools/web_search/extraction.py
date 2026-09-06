from bs4 import BeautifulSoup
import httpx
from typing import Optional

async def extract_content_from_url(url: str, max_chars: int = 15000) -> tuple[Optional[str], str]:
    """
    Fetches a URL and extracts its main text content using BeautifulSoup.
    Returns (title, content).
    """
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VoxOS/1.0"}
        )
        response.raise_for_status()

    return extract_content_from_html(response.text, max_chars)

def extract_content_from_html(html: str, max_chars: int = 15000) -> tuple[Optional[str], str]:
    """
    Parses HTML and extracts text, removing scripts, styles, and boilerplate.
    Returns (title, content).
    """
    soup = BeautifulSoup(html, "html.parser")
    
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    
    # Remove unwanted tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
        
    text = soup.get_text(separator="\n")
    
    # Clean up whitespace
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = "\n".join(chunk for chunk in chunks if chunk)
    
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[TRUNCATED]..."
        
    return title, text
