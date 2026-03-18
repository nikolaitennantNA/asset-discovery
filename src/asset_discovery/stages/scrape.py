"""Stage 3: Scrape -- cache check -> web-scraper -> save to Postgres + RAG ingest."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any


from rich.text import Text
from web_scraper import scrape_stream, ScrapeConfig, Usage as ScraperUsage

from ..config import Config
from ..cost import CostTracker
from ..db import get_connection, get_cached_page, get_cached_page_batches, save_scraped_page, url_hash
from ..display import console, show_detail, show_warning, stage_progress
from ..helpers import normalize_url


def _config_from_url(url_row: dict[str, Any]) -> ScrapeConfig | None:
    """Build per-URL ScrapeConfig from discovered URL row.

    Spider's smart mode handles rendering detection, proxy rotation, and
    lazy loading automatically. The only per-URL override is automation_scripts
    for pages requiring specific interaction (e.g. clicking "Show all locations").
    """
    if url_row.get("automation_scripts"):
        return ScrapeConfig(automation_scripts=url_row["automation_scripts"])
    return None  # use global defaults


async def run_scrape(
    issuer_id: str,
    discovered_urls: list[dict[str, Any]],
    config: Config,
    rag_store=None,
    costs: CostTracker | None = None,
    no_cache: bool = False,
) -> list[dict[str, Any]]:
    """Scrape URLs, skip cached fresh pages. Returns list of page dicts.

    Uses scrape_stream() for per-page processing as pages arrive from Spider.
    """
    from rich.panel import Panel
    from rich.padding import Padding
    import logging

    # Suppress web-scraper batch retry messages (they appear unindented)
    logging.getLogger("web_scraper.scraper").setLevel(logging.ERROR)

    start = time.monotonic()

    # Normalize URLs and collapse http/https duplicates from the DB.
    # Old runs may have saved http:// URLs that are now https:// after
    # the normalize_url fix — dedup here so Spider doesn't scrape both.
    seen_norm: set[str] = set()
    deduped_urls: list[dict[str, Any]] = []
    for url_row in discovered_urls:
        norm = normalize_url(url_row["url"]) or url_row["url"]
        if norm not in seen_norm:
            seen_norm.add(norm)
            url_row = {**url_row, "url": norm}
            deduped_urls.append(url_row)
    if len(deduped_urls) < len(discovered_urls):
        show_detail(
            f"Deduplicated {len(discovered_urls) - len(deduped_urls)} "
            f"http/https duplicate URLs"
        )
    discovered_urls = deduped_urls

    conn = get_connection(config)
    try:
        to_scrape: list[dict[str, Any]] = []
        cached_pages: list[dict[str, Any]] = []

        if no_cache:
            to_scrape = list(discovered_urls)
        else:
            for url_row in discovered_urls:
                batches = get_cached_page_batches(conn, url_row["url"], issuer_id=issuer_id)
                if batches:
                    cached_pages.extend(batches)
                else:
                    to_scrape.append(url_row)

        # Panel header
        header = Text()
        header.append("[3/6]", style="bold cyan")
        header.append(" Scraping pages", style="bold")
        header.append("  ·  ", style="dim")
        header.append(f"{len(discovered_urls)} urls")
        if cached_pages:
            header.append(f" ({len(cached_pages)} cached)", style="dim")
        console.print(Panel(header, border_style="dim", padding=(0, 1)))

        configs: dict[str, ScrapeConfig] = {}
        for url_row in to_scrape:
            cfg = _config_from_url(url_row)
            if cfg is not None:
                configs[url_row["url"]] = cfg

        all_pages: list[dict[str, Any]] = list(cached_pages)
        scraper_usage = ScraperUsage()

        # Create RAG usage tracker once (not per page)
        rag_usage = None
        if rag_store:
            from rag import Usage as RAGUsage

            rag_usage = RAGUsage()

        succeeded = 0
        failed = 0

        if to_scrape:
            total = len(to_scrape)
            stall_timeout = 20
            stream = scrape_stream(
                urls=[u["url"] for u in to_scrape],
                api_key=config.spider_api_key,
                configs=configs if configs else None,
                scraper_config=config.scraper_config(),
                usage=scraper_usage,
            )

            stall_strikes = 0
            max_stall_strikes = 3
            slow_strikes = 0
            max_slow_strikes = 8  # bail if 8 pages in a row are slow
            page_times: list[float] = []
            last_page_time = time.monotonic()
            # Track signal-batch indices: when the web-scraper splits a
            # page with many embedded locations into N batches, the stream
            # yields N ScrapedPage objects with the same URL but different
            # markdown.  We save each batch with a unique page_id and only
            # advance the progress bar for the first occurrence.
            url_batch_count: dict[str, int] = {}

            with stage_progress(total, "Scraping", "pages") as (progress, task):
                try:
                    while True:
                        try:
                            page = await asyncio.wait_for(
                                stream.__anext__(),
                                timeout=stall_timeout,
                            )
                        except StopAsyncIteration:
                            break
                        except (TimeoutError, asyncio.TimeoutError):
                            stall_strikes += 1
                            failed += 1
                            progress.advance(task)
                            if stall_strikes >= max_stall_strikes:
                                remaining = total - (succeeded + failed)
                                if remaining > 0:
                                    failed += remaining
                                show_warning(
                                    f"Stalled {max_stall_strikes}x — "
                                    f"skipping {remaining} remaining pages"
                                )
                                break
                            continue

                        stall_strikes = 0

                        # Track page speed — bail if consistently slow
                        now = time.monotonic()
                        gap = now - last_page_time
                        last_page_time = now
                        page_times.append(gap)
                        if len(page_times) > 20:
                            avg = sum(page_times[-20:]) / 20
                            if gap > max(avg * 5, 5):
                                slow_strikes += 1
                            else:
                                slow_strikes = 0
                            if slow_strikes >= max_slow_strikes:
                                remaining = total - (succeeded + failed)
                                if remaining > 0:
                                    failed += remaining
                                show_warning(
                                    f"Scraping degraded ({slow_strikes} slow pages) "
                                    f"— skipping {remaining} remaining"
                                )
                                break
                        if page.success and page.markdown:
                            batch_idx = url_batch_count.get(page.url, 0)
                            url_batch_count[page.url] = batch_idx + 1
                            is_first_batch = batch_idx == 0

                            if is_first_batch:
                                succeeded += 1

                            pid, chash = save_scraped_page(
                                conn,
                                issuer_id,
                                page.url,
                                page.markdown,
                                # Only store raw_html/signals on first batch
                                # (identical across batches, saves DB space)
                                page.raw_html if is_first_batch else "",
                                page.signals if is_first_batch else None,
                                None,
                                stale_days=config.page_stale_days,
                                batch_index=batch_idx,
                            )
                            all_pages.append(
                                {
                                    "page_id": pid,
                                    "url": page.url,
                                    "markdown": page.markdown,
                                    "raw_html": page.raw_html if is_first_batch else None,
                                    "signals": page.signals if is_first_batch else None,
                                    "content_hash": chash,
                                }
                            )
                        else:
                            failed += 1

                        # Only advance progress for new URLs (not signal batches)
                        if not page.success or url_batch_count.get(page.url, 0) <= 1:
                            progress.advance(task)
                except Exception as e:
                    show_warning(f"Stream error: {e}")

        if costs and scraper_usage.pages_scraped:
            costs.track_spider(
                scraper_usage.pages_scraped, cost_usd=scraper_usage.total_cost
            )

    finally:
        conn.close()

    # RAG ingestion — batch after scraping completes
    if rag_store:
        new_pages = [
            p for p in all_pages if p.get("markdown") and p not in cached_pages
        ]
        if new_pages:
            from rag import Usage as RAGUsage
            from ..display import show_spinner

            rag_usage = RAGUsage()
            docs = [
                {
                    "id": p.get("page_id", ""),
                    "content": p["markdown"],
                    "metadata": {"url": p["url"]},
                }
                for p in new_pages
            ]
            try:
                with show_spinner(f"  Ingesting {len(new_pages)} pages into RAG..."):
                    await rag_store.ingest(docs, namespace=issuer_id, usage=rag_usage)
                if costs and rag_usage.embedding_tokens:
                    costs.track_embedding(rag_usage.embedding_tokens)
            except Exception as e:
                show_warning(f"RAG ingestion failed: {e}")

    # Footer
    from ..display import show_done

    elapsed = time.monotonic() - start
    pct = (succeeded / (succeeded + failed) * 100) if (succeeded + failed) else 100
    parts = []
    if cached_pages:
        parts.append(f"{len(cached_pages)} cached + {succeeded} scraped ({pct:.0f}%)")
    else:
        parts.append(f"{succeeded} scraped ({pct:.0f}%)")
    if failed:
        parts.append(f"{failed} failed")
    show_done(parts, elapsed=elapsed)

    return all_pages
