#!/usr/bin/env python3
# ruff: noqa
"""Extract company locations from Overture Maps US places geoparquet.

Usage:
    uv run --with duckdb --with shapely scripts/overture_extract.py "Sprouts Farmers Market" --website sprouts.com
    uv run --with duckdb --with shapely scripts/overture_extract.py "Dollar General" --website dollargeneral.com
    uv run --with duckdb --with shapely scripts/overture_extract.py "AutoZone" --website autozone.com -o output/autozone.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time

PLACES_FILE = "/Users/nikolai.tennant/Documents/GitHub/asset-discovery/global_places.geoparquet"


def extract(
    company_name: str,
    website_domain: str | None = None,
    places_file: str = PLACES_FILE,
    limit: int = 0,
) -> list[dict]:
    import duckdb

    start = time.monotonic()
    con = duckdb.connect()
    con.install_extension("spatial")
    con.load_extension("spatial")

    where = f"names.\"primary\" ILIKE '%{company_name}%'"
    if website_domain:
        where += f" AND list_has(websites, (SELECT w FROM unnest(websites) t(w) WHERE w ILIKE '%{website_domain}%' LIMIT 1))"

    limit_clause = f"LIMIT {limit}" if limit else ""

    # Try simple website filter first, fall back if needed
    if website_domain:
        where = f"names.\"primary\" ILIKE '%{company_name}%' AND array_to_string(websites, ',') ILIKE '%{website_domain}%'"
    else:
        where = f"names.\"primary\" ILIKE '%{company_name}%'"

    sql = f"""
    SELECT
        names."primary" AS name,
        addresses[1].freeform AS address,
        addresses[1].locality AS city,
        addresses[1].postcode AS zip,
        addresses[1].region AS state,
        addresses[1].country AS country,
        phones[1] AS phone,
        ST_Y(geometry) AS latitude,
        ST_X(geometry) AS longitude
    FROM read_parquet('{places_file}')
    WHERE {where}
    {limit_clause}
    """

    result = con.execute(sql).fetchall()
    columns = ["name", "address", "city", "zip", "state", "country", "phone", "latitude", "longitude"]
    rows = [dict(zip(columns, r)) for r in result]

    elapsed = time.monotonic() - start
    print(f"Found {len(rows)} locations in {elapsed:.1f}s", file=sys.stderr)
    return rows


def main():
    parser = argparse.ArgumentParser(description="Extract company locations from Overture Maps")
    parser.add_argument("company", help="Company name to search")
    parser.add_argument("--website", "-w", help="Filter by website domain")
    parser.add_argument("--output", "-o", help="Output CSV path (default: stdout)")
    parser.add_argument("--places", "-p", default=PLACES_FILE, help="Path to places geoparquet")
    parser.add_argument("--limit", "-l", type=int, default=0, help="Max results (0=unlimited)")
    args = parser.parse_args()

    results = extract(args.company, args.website, args.places, args.limit)

    fields = ["name", "address", "city", "zip", "state", "country", "phone", "latitude", "longitude"]
    out = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    writer.writerows(results)
    if args.output:
        out.close()
        print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
