"""Setlist.fm API source.

Setlist.fm doesn't list events by region directly; it lists setlists by
artist or venue. We use it to enrich Ticketmaster pulls (e.g., confirm an
artist actually played a venue, capture support acts) rather than as a
primary discovery source.

Free key: https://api.setlist.fm/docs/1.0/index.html
Polite rate: 1 req/sec.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from eia.config import settings
from eia.sources.base import Source
from eia.sources.registry import register


class SetlistFM(Source):
    name = "setlistfm"
    target_table = "setlistfm_setlists"  # populated in Phase 1
    raw_format = "json"

    BASE_URL = "https://api.setlist.fm"

    def __init__(self) -> None:
        self.api_key = settings.setlistfm_api_key

    def _check_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "SETLISTFM_API_KEY not set. Get a free key at "
                "https://api.setlist.fm/docs/1.0/index.html"
            )

    def fetch(self) -> Path:
        """Phase 0: stub. Phase 1 will iterate venues from Ticketmaster pulls
        and look up their Setlist.fm IDs."""
        self._check_key()
        out_dir = self.raw_dir / "stub"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / ".phase0_stub").touch()
        return out_dir

    def to_cleaned(self, raw_path: Path) -> Path:
        out = self.cleaned_dir / "stub.parquet"
        pl.DataFrame({"note": ["Phase 0 stub — implementation in Phase 1"]}).write_parquet(out)
        return out


register(SetlistFM.name, SetlistFM)
