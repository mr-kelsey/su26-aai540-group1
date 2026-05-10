"""Ticketmaster Discovery API source.

Free tier: ~5000 requests/day, 5 requests/second.
Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/

Phase 0 ships the client + transform skeleton. Real pulls require
TICKETMASTER_API_KEY set in `.env`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from eia.clients import RateLimitedClient
from eia.config import settings
from eia.sources.base import Source
from eia.sources.registry import register


class Ticketmaster(Source):
    name = "ticketmaster"
    # Land into a staging table; `build_events.py` UNIONs all event-source
    # staging tables into the canonical `events` table after enrichment.
    target_table = "ticketmaster_events"
    raw_format = "json"

    BASE_URL = "https://app.ticketmaster.com"

    # Country and segment filters; tweak per pull.
    COUNTRY_CODE = "US"
    PAGE_SIZE = 200  # API max

    def __init__(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        segment_ids: list[str] | None = None,
    ) -> None:
        if not settings.ticketmaster_api_key:
            self.api_key = None
        else:
            self.api_key = settings.ticketmaster_api_key
        self.start_date = start_date or (date.today() - timedelta(days=30))
        self.end_date = end_date or date.today()
        # Segment IDs from https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/#supported-segments
        # Music = KZFzniwnSyZfZ7v7nJ, Sports = KZFzniwnSyZfZ7v7nE, Arts = KZFzniwnSyZfZ7v7na
        self.segment_ids = segment_ids or [
            "KZFzniwnSyZfZ7v7nJ",  # Music
            "KZFzniwnSyZfZ7v7nE",  # Sports
        ]

    def _check_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "TICKETMASTER_API_KEY not set. Get a free key at "
                "https://developer.ticketmaster.com/ and add to .env."
            )

    def fetch(self) -> Path:
        self._check_key()
        out_dir = self.raw_dir / f"{self.start_date}_{self.end_date}"
        out_dir.mkdir(parents=True, exist_ok=True)

        with RateLimitedClient(self.BASE_URL, requests_per_second=5.0) as client:
            for segment in self.segment_ids:
                for page_data in self._iter_pages(client, segment):
                    self._dump_page(page_data, out_dir)
        return out_dir

    def _iter_pages(
        self, client: RateLimitedClient, segment: str
    ) -> Iterator[dict[str, Any]]:
        page = 0
        while True:
            params = {
                "apikey": self.api_key,
                "countryCode": self.COUNTRY_CODE,
                "segmentId": segment,
                "startDateTime": f"{self.start_date}T00:00:00Z",
                "endDateTime": f"{self.end_date}T23:59:59Z",
                "size": self.PAGE_SIZE,
                "page": page,
            }
            data = client.get_json("/discovery/v2/events.json", params=params)
            yield {"segment": segment, "page": page, "data": data}
            page_info = data.get("page", {})
            if page + 1 >= page_info.get("totalPages", 0):
                return
            page += 1
            # Discovery API caps at page 1000.
            if page >= 1000:
                return

    @staticmethod
    def _dump_page(page_data: dict[str, Any], out_dir: Path) -> None:
        import json

        seg = page_data["segment"]
        page = page_data["page"]
        (out_dir / f"{seg}_p{page:04d}.json").write_text(json.dumps(page_data["data"]))

    def to_cleaned(self, raw_path: Path) -> Path:
        """Flatten Ticketmaster event JSON into our `events` schema."""
        import json

        rows = []
        for page_file in sorted(raw_path.glob("*.json")):
            data = json.loads(page_file.read_text())
            for ev in data.get("_embedded", {}).get("events", []):
                rows.append(self._normalize_event(ev))
        df = pl.DataFrame(rows)
        out = self.cleaned_dir / f"{raw_path.name}.parquet"
        df.write_parquet(out)
        return out

    def _normalize_event(self, ev: dict[str, Any]) -> dict[str, Any]:
        venue = (ev.get("_embedded", {}).get("venues") or [{}])[0]
        loc = venue.get("location", {})
        seg_name = (
            (ev.get("classifications") or [{}])[0].get("segment", {}).get("name", "Other")
        )
        category = {
            "Music": "concert",
            "Sports": "sport",
            "Arts & Theatre": "performance",
        }.get(seg_name, "other")
        prices = ev.get("priceRanges") or [{}]
        return {
            "event_id": f"tm_{ev.get('id')}",
            "source": "ticketmaster",
            "category": category,
            "event_name": ev.get("name"),
            "event_date": (ev.get("dates", {}).get("start", {}).get("localDate")),
            "venue_name": venue.get("name"),
            "venue_address": (venue.get("address") or {}).get("line1"),
            "venue_city": (venue.get("city") or {}).get("name"),
            "venue_state": (venue.get("state") or {}).get("stateCode"),
            "venue_zip": venue.get("postalCode"),
            "venue_lat": float(loc["latitude"]) if loc.get("latitude") else None,
            "venue_lon": float(loc["longitude"]) if loc.get("longitude") else None,
            "county_fips": None,  # derived in a later transform step
            "period_id": None,    # derived
            "expected_attendance": None,  # not provided by TM
            "ticket_min_usd": prices[0].get("min"),
            "ticket_max_usd": prices[0].get("max"),
            "raw_payload": json.dumps(ev),
            "fetched_at": self.now_utc(),
        }


# Late import to avoid circular load
import json  # noqa: E402

register(Ticketmaster.name, Ticketmaster)
