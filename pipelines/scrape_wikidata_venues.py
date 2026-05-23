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
    ap.add_argument(
        "--state-codes",
        default="CA",
        help="Comma-separated 2-letter state codes (default: CA). Use 'all' to pull every state in STATE_QIDS.",
    )
    ap.add_argument(
        "--output",
        default="data/curated/_wikidata_venues.csv",
        help="Output CSV path (all states UNIONed)",
    )
    args = ap.parse_args()

    if args.state_codes.lower() == "all":
        states = list(STATE_QIDS.keys())
    else:
        states = [s.strip().upper() for s in args.state_codes.split(",") if s.strip()]

    all_rows: list[dict] = []
    for state in states:
        qid = STATE_QIDS.get(state)
        if not qid:
            print(f"  [skip] unknown state {state!r}")
            continue
        print(f"querying WikiData for venues in {state} (QID {qid})...")
        t0 = time.time()
        try:
            bindings = fetch(qid)
        except Exception as e:
            print(f"  [skip] {state} query failed: {e}")
            continue
        rows = normalize(bindings, state)
        print(f"  {state}: {len(rows)} venues (raw={len(bindings)}) in {time.time() - t0:.1f}s")
        all_rows.extend(rows)
        # Polite pacing between SPARQL queries
        time.sleep(2)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    import csv
    if all_rows:
        keys = list(all_rows[0].keys())
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in all_rows:
                w.writerow(r)
        print(f"\nwrote {out}  ({len(all_rows)} venues total)")
        # Summary by state
        by_state: dict[str, int] = {}
        for r in all_rows:
            by_state[r["state_code"]] = by_state.get(r["state_code"], 0) + 1
        for s, n in sorted(by_state.items(), key=lambda x: -x[1]):
            print(f"  {s}: {n}")
    else:
        print("no rows; CSV not written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
