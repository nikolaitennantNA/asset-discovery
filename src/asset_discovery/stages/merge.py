"""Stage 5: Merge — cross-batch dedup and ALD dedup."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import litellm

from ..config import Config
from ..cost import CostTracker
from ..display import show_detail, show_spinner, show_stage
from ..models import Asset

MERGE_PROMPT = """\
You are deduplicating physical assets. You receive a list of assets that may \
contain duplicates — the same physical facility extracted from different pages \
with slightly different names, addresses, or levels of detail.

Find duplicates and merge them into one record each. When merging, combine ALL \
data — keep the richer value for each field. If one has an address and the other \
has coordinates, keep both. If one has capacity data the other lacks, keep it. \
Always prefer more complete, more specific information.

Return a JSON object with an "assets" key containing the deduplicated array.
Every input asset must appear in the output — either as itself or merged into another.
"""

FINAL_DEDUP_PROMPT = """\
You are doing a FINAL deduplication pass on a merged asset list.

These assets were merged in batches — some cross-batch duplicates may remain.
Identify duplicates: same physical facility appearing with different names, \
slight address variations, or different levels of detail.

For each group of duplicates, COMBINE all information into one record — merge \
the richest name, most complete address, all available coordinates, capacity data, \
and supplementary details from every duplicate.
Return JSON array of the deduplicated assets (Asset fields only, no matched_asset_id).
"""


async def run_merge(
    issuer_id: str, extracted_assets: list[Asset], config: Config,
    costs: CostTracker | None = None,
) -> list[Asset]:
    """Dedup extracted assets against each other."""
    show_stage(5, "Merging and deduplicating")
    if not extracted_assets:
        return []

    import asyncio

        batch_size = 50
        batches = [
            extracted_assets[i : i + batch_size]
            for i in range(0, len(extracted_assets), batch_size)
        ]
        show_detail(f"Merging {len(extracted_assets)} assets in {len(batches)} concurrent batches...")

        async def _merge_one(batch: list[Asset], batch_num: int) -> list[Asset]:
            merged = await _merge_batch(batch, [], [], config.merge_model, costs)
            if not merged:
                show_detail(f"Batch {batch_num}: merge returned empty — keeping originals")
                return batch
            show_detail(f"Batch {batch_num}: {len(batch)} → {len(merged)} after dedup")
            return merged

        with show_spinner(f"Merging {len(batches)} batches concurrently..."):
            batch_results = await asyncio.gather(*[
                _merge_one(batch, i + 1) for i, batch in enumerate(batches)
            ])

        final_assets: list[Asset] = []
        for batch_result in batch_results:
            final_assets.extend(batch_result)

        # Final cross-batch dedup pass
        if len(final_assets) > 1:
            with show_spinner(f"Final dedup across {len(final_assets)} assets..."):
                final_assets = await _final_dedup(final_assets, config.merge_model, costs)
            show_detail(f"Final: {len(final_assets)} unique assets")

    # Assign asset IDs and pipeline metadata after all dedup is done
    today = date.today().isoformat()
    for asset in final_assets:
        asset.asset_id = str(uuid.uuid4())
        asset.attribution_source = "asset_discovery"
        asset.date_researched = today

    return final_assets


async def _merge_batch(
    batch: list[Asset], existing: list[dict[str, Any]],
    prior_merged: list[Asset], model: str,
    costs: CostTracker | None = None,
) -> list[Asset]:
    batch_json = json.dumps([a.model_dump() for a in batch], default=str)

    response = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": MERGE_PROMPT},
            {"role": "user", "content": f"## Assets to deduplicate\n{batch_json}"},
        ],
        response_format={"type": "json_object"},
    )

    if costs:
        costs.track_litellm(response, model, "merge")

    try:
        result = json.loads(response.choices[0].message.content)
        assets_data = result if isinstance(result, list) else result.get("assets", [])
        return [
            Asset(**{
                k: v for k, v in item.items()
                if k in Asset.model_fields and k != "matched_asset_id"
            })
            for item in assets_data
        ]
    except Exception:
        return batch


async def _final_dedup(
    assets: list[Asset], model: str, costs: CostTracker | None = None,
) -> list[Asset]:
    """Final cross-batch dedup pass on the full merged asset list."""
    assets_json = json.dumps([a.model_dump() for a in assets], default=str)

    response = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": FINAL_DEDUP_PROMPT},
            {"role": "user", "content": f"## All merged assets ({len(assets)} total)\n{assets_json}"},
        ],
        response_format={"type": "json_object"},
    )

    if costs:
        costs.track_litellm(response, model, "merge")

    try:
        result = json.loads(response.choices[0].message.content)
        assets_data = result if isinstance(result, list) else result.get("assets", [])
        return [
            Asset(**{k: v for k, v in item.items() if k in Asset.model_fields})
            for item in assets_data
        ]
    except Exception:
        return assets
