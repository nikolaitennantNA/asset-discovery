"""Stage 2: Discover -- pydantic-ai agent finds and saves URLs autonomously."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import BuiltinToolCallPart, TextPart, ToolCallPart

from ..config import Config, _to_pydantic_ai_model
from ..cost import CostTracker
from ..db import get_connection, get_discovered_urls
from ..display import DiscoverDisplay, show_detail
from . import tools
from .prompts import DISCOVER_SYSTEM

_CONTEXT_TOKEN_CAP = 8_000


def _truncate_context(text: str, max_tokens: int = _CONTEXT_TOKEN_CAP) -> str:
    """Truncate text to roughly max_tokens using tiktoken."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated = enc.decode(tokens[:max_tokens])
        return truncated + "\n\n[...context truncated to fit token budget...]"
    except ImportError:
        return text


def _build_search_tools(config: Config) -> tuple[list, list]:
    """Build search tool functions and builtin tools based on search_provider config.

    Returns (tool_functions, builtin_tools) for pydantic-ai Agent.
    """
    tool_functions: list = []
    builtin_tools: list = []
    provider = config.search_provider

    if provider == "openai":
        from pydantic_ai import WebSearchTool
        builtin_tools.append(WebSearchTool())
    elif provider == "exa" and config.exa_api_key:
        from exa_py import Exa
        exa_client = Exa(api_key=config.exa_api_key)

        def web_search(query: str, num_results: int = 10) -> list[dict]:
            """Search the web using Exa. Returns list of results with title, url, and text."""
            results = exa_client.search(
                query,
                num_results=num_results,
                contents={"text": {"max_characters": 3000}},
            )
            return [
                {"title": r.title, "url": r.url, "text": getattr(r, "text", "")}
                for r in results.results
            ]

        tool_functions.append(web_search)
    elif provider == "brave":
        api_key = os.environ.get("BRAVE_API_KEY", "")
        if api_key:
            import httpx as _httpx

            def web_search(query: str, count: int = 10) -> list[dict]:
                """Search the web using Brave Search API."""
                resp = _httpx.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                    params={"q": query, "count": count},
                )
                resp.raise_for_status()
                return [
                    {"title": r["title"], "url": r["url"], "description": r.get("description", "")}
                    for r in resp.json().get("web", {}).get("results", [])
                ]

            tool_functions.append(web_search)
    elif provider == "tavily":
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if api_key:
            import httpx as _httpx

            def web_search(query: str, max_results: int = 10) -> list[dict]:
                """Search the web using Tavily Search API."""
                resp = _httpx.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": query, "max_results": max_results},
                )
                resp.raise_for_status()
                return [
                    {"title": r.get("title", ""), "url": r["url"], "content": r.get("content", "")}
                    for r in resp.json().get("results", [])
                ]

            tool_functions.append(web_search)

    return tool_functions, builtin_tools


async def run_discover(
    issuer_id: str,
    context_doc: str,
    config: Config,
    costs: CostTracker | None = None,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Run the discover agent. Returns list of discovered URLs from database."""
    # Extract company name from first markdown heading in context doc
    company_name = ""
    for line in context_doc.split("\n"):
        if line.startswith("# "):
            company_name = line[2:].strip()
            break

    display = DiscoverDisplay(company_name=company_name)
    display.show_header()

    tools.init_tools(config, issuer_id, costs, on_event=display.on_event)

    context_doc = _truncate_context(context_doc)
    system_prompt = f"{context_doc}\n\n---\n\n{DISCOVER_SYSTEM}"

    search_tools, builtin_tools = _build_search_tools(config)

    # If builtin_tools includes WebSearchTool, the model must use OpenAI Responses API
    model_str = _to_pydantic_ai_model(config.discover_model)
    if builtin_tools and model_str.startswith("openai:"):
        model_str = model_str.replace("openai:", "openai-responses:", 1)

    agent = Agent(
        model_str,
        system_prompt=system_prompt,
        tools=[
            tools.fetch_sitemap,
            tools.group_by_prefix,
            tools.crawl_page,
            tools.map_domain,
            tools.spider_links,
            tools.probe_urls,
            tools.save_urls,
            tools.save_sitemap_urls,
            tools.remove_urls,
            tools.get_saved_urls,
            tools.spawn_worker,
        ] + search_tools,
        builtin_tools=builtin_tools,
    )

    timeout = config.max_discover_minutes * 60

    async with agent:
        try:
            async with asyncio.timeout(timeout):
                async with agent.iter(
                    "Briefly state your approach for this company (1-2 sentences), then discover all URLs "
                    "containing physical asset information. Work systematically: primary site first, then "
                    "subsidiaries, then regulatory/external. Save URLs as you go.",
                    usage_limits=UsageLimits(
                        tool_calls_limit=config.max_discover_tool_calls,
                        request_limit=None,
                    ),
                ) as agent_run:
                    async for node in agent_run:
                        if hasattr(node, "model_response"):
                            for part in node.model_response.parts:
                                if isinstance(part, TextPart) and part.content.strip():
                                    display.on_agent_text(part.content.strip())
                                elif isinstance(part, BuiltinToolCallPart):
                                    # Surface web search queries from OpenAI builtin
                                    raw = part.args
                                    if isinstance(raw, str):
                                        import json
                                        try:
                                            raw = json.loads(raw)
                                        except (json.JSONDecodeError, TypeError):
                                            raw = {}
                                    args = raw if isinstance(raw, dict) else {}
                                    query = args.get("query", args.get("search_query", ""))
                                    if query:
                                        display.on_web_search(query)
            if costs and agent_run.result:
                costs.track_pydantic_ai(agent_run.result.usage(), config.discover_model, "discover")
        except (TimeoutError, asyncio.TimeoutError):
            show_detail(f"Discover timed out after {config.max_discover_minutes}m — using URLs saved so far")
        except UsageLimitExceeded:
            show_detail(f"Discover hit {config.max_discover_tool_calls} tool call limit — using URLs saved so far")

    conn = get_connection(config)
    try:
        discovered = get_discovered_urls(conn, issuer_id)
    finally:
        conn.close()

    display.show_footer(len(discovered))
    return discovered
