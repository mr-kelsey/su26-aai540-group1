"""RunSignUp races API source.

Free tier: generous; key + secret required for some endpoints.
Docs: https://runsignup.com/API
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, ClassVar

import polars as pl

from eia.clients import RateLimitedClient
from eia.config import settings
from eia.sources.base import Source
from eia.sources.registry import register


class RunSignUp(Source):
    name = "runsignup"
    # Staging table (NOT the unified `events` table). build_events.py will
    # UNION rows from here into `events` alongside setlistfm and ticketmaster.
    target_table = "runsignup_races"
    raw_format = "json"

    BASE_URL = "https://runsignup.com"
    PAGE_SIZE = 100  # API max per page

    # RunSignUp event-type IDs of interest. The published mapping (per
    # https://runsignup.com/API/race) is:
    #   1 = marathon            (~30-50K participants for majors)
    #   2 = half marathon       (~20-40K)
    #   3 = 10K                 (~5-15K)
    #   4 = 5K                  (~3-15K; by far the most common race format)
    #   5 = relay/team
    #   ...higher = ultras, trail, kids, virtual, etc.
    # 5Ks are the highest-volume category in the US — including them roughly
    # 10xs the race count. Lift this list to broaden coverage once we have
    # API access to validate the exact IDs (which can drift over time).
    EVENT_TYPE_IDS_OF_INTEREST: ClassVar[list[int]] = [1, 2, 3, 4]

    def __init__(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> None:
        self.api_key = settings.runsignup_api_key
        self.api_secret = settings.runsignup_api_secret
        # Default to the panel's full historical window. Override via constructor
        # args for ad-hoc pulls. The CLI's auto-generated `eia pull runsignup`
        # uses these defaults.
        self.start_date = start_date or date(2015, 1, 1)
        self.end_date = end_date or date.today()

    def _check_keys(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "RUNSIGNUP_API_KEY and RUNSIGNUP_API_SECRET not set. "
                "Get free credentials at https://runsignup.com/API"
            )

    def fetch(self) -> Path:
        self._check_keys()
        out_dir = self.raw_dir / f"{self.start_date}_{self.end_date}"
        out_dir.mkdir(parents=True, exist_ok=True)

        page = 1
        with RateLimitedClient(self.BASE_URL, requests_per_second=2.0) as client:
            while True:
                params = {
                    "api_key": self.api_key,
                    "api_secret": self.api_secret,
                    "format": "json",
                    "results_per_page": self.PAGE_SIZE,
                    "page": page,
                    "start_date": self.start_date.isoformat(),
                    "end_date": self.end_date.isoformat(),
                    "country_code": "US",
                    "include_event_days": "T",
                }
                data = client.get_json("/Rest/races", params=params)
                (out_dir / f"races_p{page:04d}.json").write_text(json.dumps(data))
                races = data.get("races") or []
                if len(races) < self.PAGE_SIZE:
                    break
                page += 1
                if page > 200:
                    # Defensive cap — RunSignUp surfaces ~20k races/year.
                    break
        return out_dir

    def to_cleaned(self, raw_path: Path) -> Path:
        rows = []
        for page_file in sorted(raw_path.glob("races_p*.json")):
            data = json.loads(page_file.read_text())
            for wrapper in data.get("races") or []:
                race = wrapper.get("race", {})
                rows.extend(self._normalize_race(race))
        df = (
            pl.DataFrame(rows)
            if rows
            else pl.DataFrame(
                schema={
                    "event_id": pl.Utf8,
                    "source": pl.Utf8,
                    "category": pl.Utf8,
                }
            )
        )
        out = self.cleaned_dir / f"{raw_path.name}.parquet"
        df.write_parquet(out)
        return out

    def _normalize_race(self, race: dict[str, Any]) -> list[dict[str, Any]]:
        """A RunSignUp 'race' may contain multiple sub-events (5k, 10k, marathon).
        We emit one row per (race, event-of-interest) combination.
        """
        address = race.get("address") or {}
        city = address.get("city")
        state = address.get("state")
        zip_ = address.get("zipcode")
        lat = race.get("latitude")
        lon = race.get("longitude")

        out = []
        for ev in race.get("events") or []:
            type_id = ev.get("event_type_id")
            if type_id not in self.EVENT_TYPE_IDS_OF_INTEREST:
                continue

            # Prefer ACTUAL attendance signals over capacity. For past races the
            # API usually exposes a `registration_count` (actual sign-ups); for
            # future races we fall back to `max_capacity` (the cap, an upper
            # bound). The model can treat these differently via a confidence
            # flag if needed.
            actual = (
                ev.get("registration_count")
                or ev.get("registrants_count")
                or ev.get("participants_count")
                or ev.get("finishers")
            )
            capacity = ev.get("max_capacity")
            expected = actual if actual is not None else capacity

            category = {
                1: "marathon",
                2: "half_marathon",
                3: "10k",
                4: "5k",
            }.get(type_id, f"race_type_{type_id}")

            out.append(
                {
                    "event_id": f"rsu_{race.get('race_id')}_{ev.get('event_id')}",
                    "source": "runsignup",
                    "category": category,
                    "event_name": race.get("name"),
                    "event_date": (ev.get("start_time") or "")[:10] or None,
                    "venue_name": race.get("name"),
                    "venue_address": address.get("street"),
                    "venue_city": city,
                    "venue_state": state,
                    "venue_zip": zip_,
                    "venue_lat": float(lat) if lat else None,
                    "venue_lon": float(lon) if lon else None,
                    "county_fips": None,
                    "period_id": None,
                    "expected_attendance": expected,
                    "ticket_min_usd": None,
                    "ticket_max_usd": None,
                    "raw_payload": json.dumps({"race": race, "event": ev}),
                    "fetched_at": self.now_utc(),
                }
            )
        return out


register(RunSignUp.name, RunSignUp)
