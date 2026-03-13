"""Tests for discover/QA agent tools."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from asset_search.stages import tools


@pytest.fixture(autouse=True)
def _init_tools():
    cfg = MagicMock()
    cfg.crawl4ai_api_key = "test-key"
    cfg.max_urls_per_run = 5000
    tools.init_tools(cfg, "issuer-1")


@pytest.mark.asyncio
@patch("asset_search.stages.tools._get_conn")
@patch("asset_search.stages.tools.get_discovered_urls", return_value=[])
@patch("asset_search.stages.tools.save_discovered_urls", return_value=1)
async def test_save_urls_passes_structured_fields(mock_save, mock_get, mock_conn):
    mock_conn.return_value = MagicMock()
    count = await tools.save_urls(urls=[{
        "url": "https://example.com/locations",
        "category": "facility_page",
        "notes": "JS-heavy SPA",
        "strategy": "browser",
        "proxy_mode": "auto",
        "wait_for": ".locations-list",
    }])
    assert count == 1
    saved = mock_save.call_args[0][2]  # third arg is the urls list
    assert saved[0]["strategy"] == "browser"
    assert saved[0]["proxy_mode"] == "auto"
    assert saved[0]["wait_for"] == ".locations-list"
