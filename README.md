# asset-search-v2

Web-based physical asset discovery pipeline for corporate entities.

## Setup

```bash
uv sync
cp .env.example .env  # fill in API keys
```

## Usage

```bash
# Run full pipeline for a single ISIN (requires corp-graph Postgres)
python -m asset_search run AU000000BLD2

# Run from a JSON profile (no corp-graph needed)
python -m asset_search run --from-file boral.json

# Stop after a specific stage
python -m asset_search run --from-file boral.json --stop-after discover
```

## Architecture

6-stage pipeline:

1. **Profile** — company context via corp-profile (corp-graph Postgres or JSON file)
2. **Discover** — pydantic-ai agent finds + classifies URLs
3. **Scrape** — Crawl4AI Cloud API, with Postgres page cache
4. **Extract** — instructor structured extraction (LLM map-reduce over pages)
5. **Merge** — cross-batch dedup + ALD dedup + naturesense classification
6. **QA** — pydantic-ai agent evaluates coverage, fills gaps via RAG + web search

### Future (not yet wired)

- **corp-enrich** — post-pipeline enrichment of newly discovered assets: Overture Maps
  (building footprints, height), facility registers, geocoding, address standardisation,
  coordinate validation. Runs on `discovered_assets` after the pipeline outputs. ALD
  assets already come enriched via corp-graph; only new web-discovered assets need this.
- **Batch mode** — `python -m asset_search run --portfolio --max-companies 100`

## Sub-modules

The pipeline is composed of 4 reusable packages (editable local deps):

| Package | Purpose | Repo |
|---|---|---|
| **corp-profile** | Company profiling from corp-graph + LLM enrichment | `../corp-profile` |
| **web-scraper** | Crawl4AI Cloud API wrapper with batching + proxy support | `../web-scraper` |
| **doc-extractor** | LLM structured extraction via instructor | `../doc-extractor` |
| **rag** | pgvector ingest + Cohere rerank retrieval | `../rag` |

## Configuration

All config is via environment variables (`.env`). Every sub-module option is surfaced
through the pipeline's `Config` class — see `src/asset_search/config.py` for the full
list with defaults.
