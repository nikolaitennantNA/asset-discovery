# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A 6-stage async pipeline that discovers physical assets (facilities, plants, mines, warehouses, offices) for corporate entities using LLM agents, web scraping, and structured extraction. Output is TREX ALD-aligned asset records persisted to Postgres.

## Commands

```bash
# Install (uses uv, requires sibling repos for editable deps)
uv sync

# Run pipeline for a company (requires corp-graph Postgres)
python -m asset_search run AU000000BLD2

# Run from JSON profile (no corp-graph needed)
python -m asset_search run --from-file boral.json

# Partial run (stop after a specific stage)
python -m asset_search run --from-file boral.json --stop-after discover

# Initialize Postgres cache tables
psql $CORPGRAPH_DB_URL -f scripts/init_cache_db.sql
```

No test suite or linter is configured yet. `tests/` contains empty stubs.

## Architecture

### Pipeline Stages (`src/asset_search/pipeline.py`)

1. **Profile** — Load company context from corp-graph Postgres or JSON file. Optional LLM enrichment.
2. **Discover** — pydantic-ai agent with tools (`fetch_sitemap`, `crawl_page`, `map_domain`, `mark_url_found`) + pluggable web search (Exa/Brave/Tavily/OpenAI). Finds and classifies asset-related URLs.
3. **Scrape** — Crawl4AI Cloud API via `web-scraper` package. Caches pages in Postgres with staleness tracking. Parses scrape hints from discover agent notes (WAF detection, AJAX waits).
4. **Extract** — instructor structured extraction via `doc-extractor` package. Batches pages by token budget, deduplicates by (name, entity).
5. **Merge** — LLM dedup against existing ALD assets + cross-batch dedup. Naturesense classification maps raw types to 16 predefined categories.
6. **QA** — pydantic-ai agent evaluates coverage, fills gaps via RAG queries and `scrape_and_extract` tool. Iterates up to `max_qa_iterations`.

### Configuration (`src/asset_search/config.py` + `config.toml`)

Triple-layer resolution: **env var > config.toml > hardcoded default**.

- Secrets (API keys, DB URL) live in `.env` only
- Models, caps, and sub-module settings live in `config.toml`
- `Config` dataclass is constructed once and threaded through all stages
- Sub-module configs are built via `Config.scraper_config()`, `.extractor_config()`, `.rag_config()`, `.profile_enrich_config()`

Model strings use litellm format (e.g. `bedrock/us.anthropic.claude-opus-4-6-20250219-v1:0`). For pydantic-ai agents, these are wrapped as `litellm:<model>` via `_to_pydantic_ai_model()`.

### Editable Local Dependencies

Four sibling repos are linked as editable deps via `[tool.uv.sources]` in pyproject.toml:

| Package | Path | Purpose |
|---|---|---|
| `corp-profile` | `../corp-profile` | Company profiling from corp-graph + LLM enrichment |
| `web-scraper` | `../web-scraper` | Crawl4AI Cloud API wrapper with batching + proxy |
| `doc-extractor` | `../doc-extractor` | LLM structured extraction via instructor |
| `rag` | `../rag` | pgvector ingest + Cohere rerank retrieval |

### Key Patterns

- **Idempotent caching**: All stages check Postgres cache before doing work. Pages stale after `page_stale_days`. Extraction cache keyed on `(page_id, model, content_hash)`.
- **Cost tracking**: `CostTracker` (`cost.py`) tracks per-model tokens, per-stage tokens, and non-LLM API costs (Crawl4AI, Exa, embeddings, Cohere rerank). Summary printed at end.
- **Agent tools**: Discover and QA agents compose domain-specific tools from `stages/tools.py` with generic MCP-based web search tools.
- **Postgres persistence**: 5 tables — `discovered_urls`, `scraped_pages`, `extraction_results`, `discovered_assets` (with PostGIS geometry), `qa_results`. Schema in `scripts/init_cache_db.sql`.

### Key Files

| File | Role |
|---|---|
| `src/asset_search/__main__.py` | CLI entry point |
| `src/asset_search/pipeline.py` | 6-stage orchestrator |
| `src/asset_search/config.py` | Master config with triple-layer resolution |
| `src/asset_search/models.py` | Pydantic models (`Asset`, `QAReport`, `CoverageFlag`) |
| `src/asset_search/cost.py` | LLM + API cost tracking |
| `src/asset_search/db.py` | Postgres helper functions |
| `src/asset_search/stages/prompts.py` | System prompts for all LLM agents |
| `src/asset_search/stages/tools.py` | Agent tools (sitemap, crawl, map, mark_url) |
| `src/asset_search/stages/discover.py` | Stage 2: URL discovery agent |
| `src/asset_search/stages/scrape.py` | Stage 3: Web scraping |
| `src/asset_search/stages/extract.py` | Stage 4: Structured extraction |
| `src/asset_search/stages/merge.py` | Stage 5: Dedup + classification |
| `src/asset_search/stages/qa.py` | Stage 6: QA + gap-fill agent |

## Conventions

- Python 3.13+, async throughout (stages are `async def`)
- Pydantic v2 for all data models
- pydantic-ai for agent-based stages (discover, QA)
- instructor for structured extraction (extract stage)
- litellm as the unified LLM routing layer
- Rich for terminal display (`display.py`)
- All DB access via `psycopg` with raw SQL (no ORM)
