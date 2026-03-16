"""Stage 4: Extract — cache check → doc-extractor → save to Postgres."""

from __future__ import annotations

import logging
from typing import Any

from doc_extractor import (
    extract, extract_exhaustive, estimate_count,
    Document, Usage as ExtractorUsage, EXHAUSTIVE_THRESHOLD,
)

from ..config import Config
from ..cost import CostTracker
from ..db import get_connection, get_extraction_result, save_extraction_result, url_hash
from ..display import show_stage
from ..models import Asset

log = logging.getLogger(__name__)


_COORD_DEDUP_THRESHOLD = 0.0005  # ~55m at equator — matches signals.py


def _dedup_by_coords(assets: list[Asset], threshold: float = _COORD_DEDUP_THRESHOLD) -> list[Asset]:
    """Deduplicate assets whose coordinates are within ~55m of each other.

    Keeps the first occurrence (which typically has richer metadata from signal
    injection). This prevents the LLM from double-counting coordinates that
    appear both in the signal header and in the page body.
    """
    result: list[Asset] = []
    seen_coords: list[tuple[float, float]] = []
    for asset in assets:
        if asset.latitude is not None and asset.longitude is not None:
            is_dup = False
            for elat, elng in seen_coords:
                if abs(elat - asset.latitude) < threshold and abs(elng - asset.longitude) < threshold:
                    is_dup = True
                    break
            if is_dup:
                continue
            seen_coords.append((asset.latitude, asset.longitude))
        result.append(asset)
    return result


EXTRACT_PROMPT_TEMPLATE = """\
Extract all physical assets belonging to {company} and its subsidiaries from the
documents below. Only extract assets owned or operated by {company}.

Physical assets include: facilities, plants, factories, mines, quarries, offices,
warehouses, data centers, stores, properties, wind/solar farms, pipelines,
terminals, refineries, and any other permanent physical infrastructure.
{ald_summary}
"""

COUNT_PROMPT_TEMPLATE = """\
Count the number of distinct physical assets belonging to {company} mentioned
in this document. Physical assets include facilities, plants, factories, mines,
quarries, offices, warehouses, stores, wind/solar farms, pipelines, terminals,
refineries, etc. Count each unique asset once.
"""


async def run_extract(
    issuer_id: str, company_name: str, pages: list[dict[str, Any]],
    config: Config, existing_assets_summary: str | None = None,
    costs: CostTracker | None = None,
) -> list[Asset]:
    """Extract assets from scraped pages, skipping cached extractions."""
    show_stage(4, "Extracting assets")

    conn = get_connection(config)
    all_assets: list[Asset] = []

    try:
        to_extract: list[dict[str, Any]] = []
        # Dedup cached assets by (name, entity) since batched extraction
        # saves the full batch result against every page in the batch.
        seen_cached: set[tuple[str, str]] = set()
        for page in pages:
            pid = page.get("page_id") or url_hash(page["url"])
            cached = get_extraction_result(conn, pid, config.extract_model)
            if cached:
                for ad in (cached.get("assets_json") or []):
                    key = (ad.get("asset_name", ""), ad.get("entity_name", ""))
                    if key not in seen_cached:
                        seen_cached.add(key)
                        all_assets.append(Asset(**ad))
            else:
                to_extract.append(page)

        if not to_extract:
            return all_assets

        documents = [
            Document(
                content=p["markdown"],
                metadata={"url": p["url"], "page_id": p.get("page_id") or url_hash(p["url"])},
            )
            for p in to_extract if p.get("markdown")
        ]

        ald_summary = ""
        if existing_assets_summary:
            ald_summary = (
                f"\nThis company has existing known assets:\n{existing_assets_summary}\n"
                "Extract all assets you find -- dedup happens later."
            )

        prompt = EXTRACT_PROMPT_TEMPLATE.format(
            company=company_name,
            ald_summary=ald_summary,
        )
        count_prompt = COUNT_PROMPT_TEMPLATE.format(company=company_name)
        extractor_cfg = config.extractor_config()

        # --- Two-pass: count assets per page, route high-count pages to
        # exhaustive extraction ---
        normal_docs: list[Document] = []
        exhaustive_docs: list[tuple[Document, int]] = []

        for doc in documents:
            count_usage = ExtractorUsage()
            count = await estimate_count(
                doc, count_prompt, config.count_model,
                config=extractor_cfg, usage=count_usage,
            )
            if costs:
                costs.track_llm(
                    config.count_model,
                    count_usage.input_tokens, count_usage.output_tokens, "count",
                )
            if count > EXHAUSTIVE_THRESHOLD:
                log.info(
                    "Page %s: ~%d assets (above threshold %d), using exhaustive extraction",
                    doc.metadata.get("url", "?")[:80], count, EXHAUSTIVE_THRESHOLD,
                )
                exhaustive_docs.append((doc, count))
            else:
                normal_docs.append(doc)

        # Normal extraction for standard pages
        new_assets: list[Asset] = []
        if normal_docs:
            extractor_usage = ExtractorUsage()
            new_assets = await extract(
                documents=normal_docs, schema=Asset, prompt=prompt,
                model=config.extract_model, max_concurrency=config.extractor_default_concurrency,
                config=extractor_cfg, usage=extractor_usage,
            )
            if costs:
                costs.track_llm(
                    config.extract_model,
                    extractor_usage.input_tokens, extractor_usage.output_tokens, "extract",
                )

        # Exhaustive extraction for high-count pages
        for doc, estimated_count in exhaustive_docs:
            exhaust_usage = ExtractorUsage()
            exhaustive_assets = await extract_exhaustive(
                doc, Asset, prompt, config.extract_model, estimated_count,
                config=extractor_cfg, usage=exhaust_usage,
            )
            if costs:
                costs.track_llm(
                    config.extract_model,
                    exhaust_usage.input_tokens, exhaust_usage.output_tokens, "extract",
                )
            new_assets.extend(exhaustive_assets)

        # Save extraction results per page.
        all_dumped = [a.model_dump() for a in new_assets]
        for page in to_extract:
            pid = page.get("page_id") or url_hash(page["url"])
            save_extraction_result(
                conn, pid, issuer_id, page.get("content_hash", ""),
                config.extract_model, all_dumped,
            )

        all_assets.extend(new_assets)
    finally:
        conn.close()

    # Dedup assets with near-identical coordinates (signal injection double-count)
    all_assets = _dedup_by_coords(all_assets)
    return all_assets
