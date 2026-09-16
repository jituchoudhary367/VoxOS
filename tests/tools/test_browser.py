import pytest
from unittest.mock import AsyncMock, patch

from voxos.tools.web_search.tools import WebSearchTool, WebSearchArgs, WebExtractTool, WebExtractArgs
from voxos.tools.browser.tools import (
    BrowserExtractArgs,
    BrowserExtractTool,
    BrowserOpenArgs,
    BrowserOpenTool,
    BrowserSearchTool,
)

@pytest.mark.asyncio
@patch("voxos.tools.web_search.provider.DDGS")
async def test_web_search(mock_ddgs):
    # The actual implementation calls DDGS synchronously via run_in_executor
    from unittest.mock import MagicMock
    mock_instance = MagicMock()
    mock_instance.__enter__.return_value = mock_instance
    mock_instance.text.return_value = [
        {"title": "Test Result 1", "body": "Snippet 1", "href": "https://example.com/1"},
        {"title": "Test Result 2", "body": "Snippet 2", "href": "https://example.com/2"},
    ]
    mock_ddgs.return_value = mock_instance

    tool = WebSearchTool()
    args = WebSearchArgs(query="test query")
    result = await tool.execute(args)
    
    assert result.success is True
    assert len(result.data["results"]) == 2
    assert result.data["results"][0]["title"] == "Test Result 1"


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_web_extract(mock_get):
    class MockResponse:
        def __init__(self):
            self.text = "<html><head><title>Test Title</title></head><body><p>Hello world!</p><script>alert('bad');</script></body></html>"
        def raise_for_status(self):
            pass
            
    mock_get.return_value = MockResponse()
    
    tool = WebExtractTool()
    args = WebExtractArgs(url="https://example.com")
    result = await tool.execute(args)
    
    assert result.success is True
    assert result.data["title"] == "Test Title"
    assert "Hello world!" in result.data["content"]
    assert "alert('bad')" not in result.data["content"] # Script should be stripped


@pytest.mark.asyncio
@patch("voxos.tools.browser.tools._get_browser_session")
async def test_browser_open(mock_get_browser_session):
    mock_page = AsyncMock()
    mock_page.title.return_value = "Mock Page Title"
    mock_page.url = "https://mock.example.com"
    mock_get_browser_session.return_value = mock_page

    tool = BrowserOpenTool()
    args = BrowserOpenArgs(url="https://mock.example.com")
    result = await tool.execute(args)
    
    assert result.success is True
    assert result.data["title"] == "Mock Page Title"
    assert result.data["url"] == "https://mock.example.com"


@pytest.mark.asyncio
@patch("voxos.tools.browser.tools._get_browser_session")
async def test_opening_a_blank_browser_verifies_successfully(mock_get_browser_session):
    page = AsyncMock()
    page.title.return_value = "New Tab"
    page.url = "about:blank"
    mock_get_browser_session.return_value = page

    tool = BrowserOpenTool()
    args = BrowserOpenArgs(url="about:blank")
    result = await tool.execute(args)
    verification = await tool.verify(args, result)

    assert result.success is True
    assert verification.passed is True


@pytest.mark.asyncio
@patch("voxos.tools.browser.tools._get_browser_session")
async def test_browser_extract(mock_get_browser_session):
    mock_page = AsyncMock()
    mock_page.url = "https://mock.example.com"
    mock_page.evaluate.return_value = "Mock extracted text"
    mock_get_browser_session.return_value = mock_page

    tool = BrowserExtractTool()
    args = BrowserExtractArgs(selector="body")
    result = await tool.execute(args)
    
    assert result.success is True
    assert result.data["text"] == "Mock extracted text"


@pytest.mark.asyncio
async def test_closed_page_creates_a_fresh_browser_session(monkeypatch):
    from voxos.tools.browser import tools as browser_tools

    class ClosedPage:
        url = "https://stale.example"

        def is_closed(self):
            return True

    fresh_page = object()

    class Context:
        async def new_page(self):
            return fresh_page

    class Browser:
        def is_connected(self):
            return True

        async def new_context(self, **_kwargs):
            return Context()

    monkeypatch.setattr(browser_tools, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(browser_tools, "_browser_instance", Browser())
    monkeypatch.setattr(browser_tools, "_page_instance", ClosedPage())
    monkeypatch.setattr(browser_tools, "_context_instance", object())

    page = await browser_tools._get_browser_session()

    assert page is fresh_page


@pytest.mark.asyncio
@patch("voxos.tools.browser.tools._get_browser_session")
async def test_default_search_engine_is_bing(mock_get_browser_session):
    page = AsyncMock()
    page.title.return_value = "Bing"
    page.url = "https://www.bing.com/search?q=whatsapp"
    mock_get_browser_session.return_value = page

    result = await BrowserSearchTool().execute({"query": "whatsapp"})

    assert result.success is True
    assert result.data["engine"] == "bing"


@pytest.mark.asyncio
@patch("voxos.tools.browser.tools._get_browser_session")
async def test_google_captcha_returns_a_friendly_blocked_result(mock_get_browser_session):
    page = AsyncMock()
    page.title.return_value = "Sorry..."
    page.url = "https://www.google.com/sorry/index?continue=search"
    mock_get_browser_session.return_value = page

    result = await BrowserSearchTool().execute({"query": "voxos", "engine": "google"})

    assert result.success is False
    assert result.error_code == "SEARCH_BLOCKED"
