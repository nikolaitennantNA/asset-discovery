"""Pydantic models for the asset discovery pipeline — TREX ALD aligned."""

from __future__ import annotations

from pydantic import BaseModel


class Asset(BaseModel):
    """Asset extraction model — TREX ALD aligned."""

    # --- TREX fields ---
    asset_name: str
    entity_name: str = ""
    entity_isin: str = ""
    parent_name: str = ""
    parent_isin: str = ""
    entity_stake_pct: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    status: str = ""
    capacity: float | None = None
    capacity_units: str = ""
    asset_type_raw: str = ""
    supplementary_details: dict = {}

    # --- Set by pipeline, not by LLM ---
    asset_id: str = ""
    naturesense_asset_type: str = ""
    industry_code: str = ""
    date_researched: str = ""
    attribution_source: str = ""

    # --- Pipeline working fields (not in TREX export) ---
    address: str = ""
    source_url: str = ""
    domain_source: str = ""


class CoverageFlag(BaseModel):
    flag_type: str
    description: str
    severity: str = "medium"


class QAReport(BaseModel):
    quality_score: float = 0.0
    missing_types: list[str] = []
    missing_regions: list[str] = []
    issues: list[str] = []
    should_enrich: bool = False
    coverage_flags: list[CoverageFlag] = []
