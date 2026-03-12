"""6-stage asset discovery pipeline orchestrator."""

from __future__ import annotations

from .config import Config


async def run(isin: str, config: Config | None = None, stop_after: str | None = None) -> None:
    """Run the full 6-stage pipeline for a company.

    Stages: 1.Profile 2.Discover 3.Scrape 4.Extract 5.Merge 6.QA
    """
    raise NotImplementedError("Phase 2/3 — wire stages together")
