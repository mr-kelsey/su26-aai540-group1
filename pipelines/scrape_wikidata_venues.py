"""Pull venue capacities from WikiData via SPARQL.

Complements the hand-curated SEED dict in build_venue_capacities.py by
auto-discovering capacities for venues we didn't know about. WikiData has a
maximum-capacity property (P1083) attached to a few thousand US music
venues, stadiums, and arenas. This script queries the public SPARQL
endpoint and writes a CSV that build_venue_capacities.py can merge in as a
new capacity source.

Default scope: California venues. Override with --state-code (and adjust
the WikiData admin-entity QID in `STATE_QIDS` below if you need a state
that isn't pre-mapped).

Output: data/curated/_wikidata_venues.csv

Note: WikiData coverage is uneven — major venues (Hollywood Bowl, Kia
Forum, Chase Center) are well-tagged; small clubs are often missing or
have stale numbers. Treat the result as a starting point, not authoritative.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "USD-AAI540-VenueCapacityScraper/1.0 (https://github.com/mr-kelsey/su26-aai540-group1)"

# WikiData QIDs for US states. Extend as needed.
STATE_QIDS: dict[str, str] = {
    "CA": "Q99",
    "NY": "Q1384",
    "TX": "Q1439",
    "FL": "Q812",
    "IL": "Q1204",
    "PA": "Q1400",
    "OH": "Q1397",
    "GA": "Q1428",
    "NC": "Q1454",
    "MA": "Q771",
    "MI": "Q1166",
    "NV": "Q1227",
}


def build_query(state_qid: str) -> str:
    """SPARQL query: every venue in the state with a max-capacity claim.

    Uses a single transitive `wdt:P131*` (admin-located-in, any depth). The
    double-transitive form was too expensive for the public endpoint and
    timed out. This form catches venues whose direct city, county, region,
    or higher tagging eventually rolls up to the state. Misses venues where
    the chain is broken (rare) or that are tagged only by US (not state)."""
    return f"""
SELECT ?venue ?venueLabel ?cityLabel ?capacity ?venueDescription
WHERE {{
  ?venue wdt:P1083 ?capacity .
  ?venue wdt:P131* wd:{state_qid} .
  OPTIONAL {{ ?venue wdt:P131 ?city . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY DESC(?capacity)
LIMIT 5000
"""


def fetch(state_qid: str) -> list[dict]:
    """Submit the SPARQL query and return parsed bindings."""
    query = build_query(state_qid)
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": USER_AGENT,
    }
    # WikiData asks polite scrapers to back off; one query at a time is fine.
    resp = requests.get(
        SPARQL_ENDPOINT,
        params={"query": query},
        headers=headers,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def normalize(bindings: list[dict], state_code: str) -> list[dict]:
    """Flatten WikiData bindings into row dicts."""
    rows = []
    for b in bindings:
        venue_uri = b.get("venue", {}).get("value", "")
        wikidata_id = venue_uri.rsplit("/", 1)[-1] if venue_uri else None
        capacity_raw = b.get("capacity", {}).get("value")
        try:
            capacity = int(float(capacity_raw)) if capacity_raw else None
        except (TypeError, ValueError):
            capacity = None
        if capacity is None or capacity <= 0:
            continue
        rows.append({
            "wikidata_id":  wikidata_id,
            "venue_name":   b.get("venueLabel", {}).get("value"),
            "city_name":    b.get("cityLabel", {}).get("value"),
            "state_code":   state_code,
            "capacity":     capacity,
            "description":  b.get("venueDescription", {}).get("value"),
            "capacity_source": "wikidata_sparql",
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-code", default="CA", help="2-letter state code")
    ap.add_argument(
        "--output",
        default="data/curated/_wikidata_venues.csv",
        help="Output CSV path",
    )
    args = ap.parse_args()

    state_qid = STATE_QIDS.get(args.state_code.upper())
    if not state_qid:
        raise SystemExit(
            f"unknown state {args.state_code!r}; add a QID to STATE_QIDS"
        )

    print(f"querying WikiData for venues in {args.state_code} (QID {state_qid})...")
    t0 = time.time()
    bindings = fetch(state_qid)
    print(f"  got {len(bindings)} raw bindings in {time.time() - t0:.1f}s")

    rows = normalize(bindings, args.state_code.upper())
    print(f"  {len(rows)} venues after dedup/filter")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Write CSV without polars dependency (this is a small one-off)
    import csv
    if rows:
        keys = list(rows[0].keys())
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"wrote {out}")
        # Quick top-10
        print("\n=== top 10 by capacity ===")
        for r in rows[:10]:
            print(f"  {r['capacity']:>8,}  {r['venue_name']}  ({r['city_name']})")
    else:
        print("no rows; CSV not written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
