"""Stage 4: Extract — cache check → doc-extractor → save to Postgres."""

from __future__ import annotations

from typing import Any

from doc_extractor import extract, Document, Usage as ExtractorUsage

from ..config import Config
from ..cost import CostTracker
from ..db import get_connection, get_extraction_result, save_extraction_result, url_hash
from ..display import show_stage
from ..models import Asset


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

        extractor_usage = ExtractorUsage()
        new_assets = await extract(
            documents=documents, schema=Asset, prompt=prompt,
            model=config.extract_model, max_concurrency=config.extractor_default_concurrency,
            config=config.extractor_config(),
            usage=extractor_usage,
        )
        if costs:
            costs.track_llm(
                config.extract_model,
                extractor_usage.input_tokens,
                extractor_usage.output_tokens,
                "extract",
            )

        # Save extraction results per page.
        # Since doc-extractor batches pages together, we can't reliably map
        # individual assets to individual pages. Save all batch assets against
        # each page so the cache correctly detects "already extracted".
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
