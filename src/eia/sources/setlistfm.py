"""Setlist.fm API source.

Pulls historical concert setlists by (country, state, year) partition into the
`setlistfm_setlists` staging table. A future `build_events.py` will UNION
this with the Ticketmaster staging table into the `events` table.

See docs/superpowers/specs/2026-05-10-setlistfm-source-design.md for design.

Free key: https://api.setlist.fm/docs/1.0/index.html
Polite rate: 1 req/sec.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml  # type: ignore[import-untyped]

from eia.clients import RateLimitedClient
from eia.config import settings
from eia.sources.base import Source
from eia.sources.registry import register

logger = logging.getLogger(__name__)


class SetlistFM(Source):
    name = "setlistfm"
    target_table = "setlistfm_setlists"
    raw_format = "json"

    BASE_URL = "https://api.setlist.fm"
    PAGE_SIZE = 20  # fixed by the API

    def __init__(
        self,
        country_code: str | None = None,
        years: list[int] | None = None,
        state_codes: list[str] | None = None,
        max_pages: int | None = None,
    ) -> None:
        cfg = self._load_config()
        self.api_key = settings.setlistfm_api_key
        self.country_code = country_code or cfg["country_code"]
        self.years = years or cfg["default_years"]
        self.state_codes = state_codes or cfg["default_states"]
        self.requests_per_second = cfg["requests_per_second"]
        self.max_pages_per_partition = (
            max_pages if max_pages is not None else cfg["max_pages_per_partition"]
        )

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["setlistfm"]  # type: ignore[no-any-return]

    def _check_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "SETLISTFM_API_KEY not set. Get a free key at "
                "https://api.setlist.fm/docs/1.0/index.html"
            )

    # ---- fetch ----

    def fetch(self) -> Path:
        """Pull all configured (country, state, year) partitions into raw_dir.

        Returns the raw_dir path; per-partition output lives in subdirs named
        `<country>_<state>_<year>/page_NNNN.json`.
        """
        self._check_key()
        out_root = self.raw_dir
        out_root.mkdir(parents=True, exist_ok=True)
        total_pages = 0
        with RateLimitedClient(
            self.BASE_URL,
            requests_per_second=self.requests_per_second,
            timeout_s=30.0,
        ) as client:
            for year in self.years:
                for state in self.state_codes:
                    partition_dir = out_root / f"{self.country_code}_{state}_{year}"
                    pages = self._fetch_partition(
                        client, self.country_code, state, year, partition_dir
                    )
                    total_pages += pages
        logger.info("Setlist.fm fetch complete: %d pages total", total_pages)
        return out_root

    def _fetch_page(
        self,
        client: RateLimitedClient,
        country: str,
        state: str,
        year: int,
        page: int,
    ) -> dict[str, Any]:
        """Single API call. Rate limiting is handled by the client."""
        params = {
            "countryCode": country,
            "stateCode": state,
            "year": year,
            "p": page,
        }
        headers = {"Accept": "application/json", "x-api-key": self.api_key}
        return client.get_json(  # type: ignore[no-any-return]
            "/rest/1.0/search/setlists",
            params=params,
            headers=headers,
        )

    def _fetch_partition(
        self,
        client: RateLimitedClient | None,
        country: str,
        state: str,
        year: int,
        out_dir: Path,
    ) -> int:
        """Paginate one (country, state, year) partition; write page files.

        Returns the number of pages written. Logs cap-warnings when the
        partition's `total` exceeds `max_pages_per_partition * PAGE_SIZE`.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        page = 1
        first = self._fetch_page(client, country, state, year, page)  # type: ignore[arg-type]
        total = int(first.get("total", 0))
        if total == 0:
            logger.info(
                "Setlist.fm %s %s %d: empty partition", country, state, year
            )
            return 0

        max_pages = self.max_pages_per_partition
        n_to_pull = min(
            max_pages,
            (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE,
        )
        if total > max_pages * self.PAGE_SIZE:
            logger.warning(
                "Setlist.fm %s %s %d: total %d exceeds cap (%d pages); "
                "some setlists unreachable in this partition",
                country,
                state,
                year,
                total,
                max_pages,
            )

        (out_dir / f"page_{page:04d}.json").write_text(json.dumps(first))
        for page in range(2, n_to_pull + 1):
            data = self._fetch_page(client, country, state, year, page)  # type: ignore[arg-type]
            (out_dir / f"page_{page:04d}.json").write_text(json.dumps(data))

        logger.info(
            "Setlist.fm %s %s %d: pulled %d pages (total=%d)",
            country,
            state,
            year,
            n_to_pull,
            total,
        )
        return n_to_pull

    # ---- clean ----

    def to_cleaned(self, raw_path: Path) -> Path:
        """Read all page JSONs under raw_path, flatten, dedup, write parquet."""
        rows: list[dict[str, Any]] = []
        for partition_dir in sorted(raw_path.iterdir()):
            if not partition_dir.is_dir():
                continue
            for page_file in sorted(partition_dir.glob("page_*.json")):
                data = json.loads(page_file.read_text())
                for sl in data.get("setlist", []):
                    row = self._parse_setlist(sl)
                    if row is not None:
                        rows.append(row)

        df = pl.DataFrame(
            rows,
            schema={
                "setlist_id": pl.Utf8,
                "artist_name": pl.Utf8,
                "artist_mbid": pl.Utf8,
                "event_date": pl.Date,
                "venue_name": pl.Utf8,
                "venue_id": pl.Utf8,
                "city_name": pl.Utf8,
                "state_code": pl.Utf8,
                "country_code": pl.Utf8,
                "venue_lat": pl.Float64,
                "venue_lon": pl.Float64,
                "tour_name": pl.Utf8,
                "info_text": pl.Utf8,
                "n_songs": pl.Int32,
                "raw_payload": pl.Utf8,
                "fetched_at": pl.Datetime,
            },
        )
        # Dedup on setlist_id, keep first occurrence
        if df.height > 0:
            df = df.unique(subset=["setlist_id"], keep="first")

        out = self.cleaned_dir / "setlists.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out)
        logger.info(
            "Setlist.fm to_cleaned: %d setlists written to %s", df.height, out
        )
        return out

    # ---- parsing helper ----

    @staticmethod
    def _parse_setlist(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize a single setlist response into the staging-schema row.

        Returns None if a required field (eventDate) is missing or unparseable.
        """
        event_date_raw = payload.get("eventDate")
        if not event_date_raw or not isinstance(event_date_raw, str):
            return None
        try:
            event_date = datetime.strptime(event_date_raw, "%d-%m-%Y").date()
        except ValueError:
            return None

        artist = payload.get("artist") or {}
        venue = payload.get("venue") or {}
        city = venue.get("city") or {}
        coords = city.get("coords") or {}
        tour = payload.get("tour") or {}
        sets_list = (payload.get("sets") or {}).get("set") or []
        n_songs = sum(len(s.get("song") or []) for s in sets_list)

        return {
            "setlist_id": payload.get("id"),
            "artist_name": artist.get("name"),
            "artist_mbid": artist.get("mbid"),
            "event_date": event_date,
            "venue_name": venue.get("name"),
            "venue_id": venue.get("id"),
            "city_name": city.get("name"),
            "state_code": city.get("stateCode"),
            "country_code": (city.get("country") or {}).get("code"),
            "venue_lat": coords.get("lat"),
            "venue_lon": coords.get("long"),
            "tour_name": tour.get("name"),
            "info_text": payload.get("info"),
            "n_songs": n_songs,
            "raw_payload": json.dumps(payload),
            "fetched_at": datetime.utcnow(),
        }


register(SetlistFM.name, SetlistFM)
