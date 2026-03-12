"""Pipeline configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    corpgraph_db_url: str = field(
        default_factory=lambda: os.environ.get(
            "CORPGRAPH_DB_URL",
            "postgresql://corpgraph:corpgraph@localhost:5432/corpgraph",
        )
    )
    crawl4ai_api_key: str = field(default_factory=lambda: os.environ.get("CRAWL4AI_API_KEY", ""))
    firecrawl_api_key: str = field(default_factory=lambda: os.environ.get("FIRECRAWL_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    cohere_api_key: str = field(default_factory=lambda: os.environ.get("COHERE_API_KEY", ""))
    exa_api_key: str = field(default_factory=lambda: os.environ.get("EXA_API_KEY", ""))

    discover_model: str = field(
        default_factory=lambda: os.environ.get("DISCOVER_MODEL", "bedrock/us.anthropic.claude-opus-4-6-20250219-v1:0")
    )
    extract_model: str = field(
        default_factory=lambda: os.environ.get("EXTRACT_MODEL", "bedrock/us.anthropic.claude-opus-4-6-20250219-v1:0")
    )
    merge_model: str = field(default_factory=lambda: os.environ.get("MERGE_MODEL", "openai/gpt-5-mini"))
    qa_model: str = field(
        default_factory=lambda: os.environ.get("QA_MODEL", "bedrock/us.anthropic.claude-opus-4-6-20250219-v1:0")
    )
    search_provider: str = field(default_factory=lambda: os.environ.get("SEARCH_PROVIDER", "exa"))

    max_scrape_concurrency: int = 100
    max_extract_concurrency: int = 10
    page_stale_days: int = 30
    max_discover_tool_calls: int = 200
    max_discover_minutes: int = 15
    max_qa_iterations: int = 2
    max_urls_per_run: int = 5000
