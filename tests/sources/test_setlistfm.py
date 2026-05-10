"""Tests for the Setlist.fm source."""

from __future__ import annotations

import json
import math
from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

from eia.sources.setlistfm import SetlistFM


def _full_payload() -> dict:
    return {
        "id": "73d6a40b",
        "eventDate": "15-03-2022",
        "artist": {
            "name": "Radiohead",
            "mbid": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
        },
        "venue": {
            "id": "73d6a380",
            "name": "The Greek Theatre",
            "city": {
                "name": "Berkeley",
                "stateCode": "CA",
                "country": {"code": "US"},
                "coords": {"lat": 37.8732, "long": -122.2547},
            },
        },
        "info": "Sold out show. Cover of 'Karma Police' was a surprise.",
        "tour": {"name": "OK Computer Anniversary Tour"},
        "sets": {
            "set": [
                {
                    "song": [
                        {"name": "Bones"},
                        {"name": "Airbag"},
                        {"name": "Lucky"},
                    ]
                },
                {
                    "song": [
                        {"name": "Karma Police"},
                        {"name": "Paranoid Android"},
                    ]
                },
            ]
        },
    }


def _fake_response(total: int, page: int, items_per_page: int = 20) -> dict:
    """Build a Setlist.fm-shaped page response with `total` overall and a
    page of synthetic setlists (or empty if past the data)."""
    start = (page - 1) * items_per_page
    n_on_page = max(0, min(items_per_page, total - start))
    setlists = [
        {
            "id": f"sl_{start + i}",
            "eventDate": "01-06-2022",
            "artist": {"name": f"Artist {start + i}"},
            "venue": {
                "name": "Venue",
                "city": {
                    "name": "Berkeley",
                    "stateCode": "CA",
                    "country": {"code": "US"},
                },
            },
            "sets": {"set": []},
        }
        for i in range(n_on_page)
    ]
    return {
        "total": total,
        "page": page,
        "itemsPerPage": items_per_page,
        "setlist": setlists,
    }


# ---- _parse_setlist tests ----


def test_parse_setlist_full_fields() -> None:
    row = SetlistFM._parse_setlist(_full_payload())
    assert row is not None
    assert row["setlist_id"] == "73d6a40b"
    assert row["artist_name"] == "Radiohead"
    assert row["artist_mbid"] == "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    assert row["event_date"] == date(2022, 3, 15)
    assert row["venue_name"] == "The Greek Theatre"
    assert row["venue_id"] == "73d6a380"
    assert row["city_name"] == "Berkeley"
    assert row["state_code"] == "CA"
    assert row["country_code"] == "US"
    assert row["venue_lat"] == pytest.approx(37.8732)
    assert row["venue_lon"] == pytest.approx(-122.2547)
    assert row["tour_name"] == "OK Computer Anniversary Tour"
    assert "Sold out" in (row["info_text"] or "")
    assert row["n_songs"] == 5


def test_parse_setlist_no_coords() -> None:
    payload = _full_payload()
    del payload["venue"]["city"]["coords"]
    row = SetlistFM._parse_setlist(payload)
    assert row is not None
    assert row["venue_lat"] is None
    assert row["venue_lon"] is None


def test_parse_setlist_no_venue() -> None:
    payload = _full_payload()
    del payload["venue"]
    row = SetlistFM._parse_setlist(payload)
    assert row is not None
    assert row["venue_name"] is None
    assert row["venue_id"] is None
    assert row["city_name"] is None


def test_parse_setlist_no_tour_no_info() -> None:
    payload = _full_payload()
    del payload["tour"]
    del payload["info"]
    row = SetlistFM._parse_setlist(payload)
    assert row is not None
    assert row["tour_name"] is None
    assert row["info_text"] is None


def test_parse_setlist_empty_sets() -> None:
    payload = _full_payload()
    payload["sets"] = {"set": []}
    row = SetlistFM._parse_setlist(payload)
    assert row is not None
    assert row["n_songs"] == 0


def test_parse_setlist_malformed_date_returns_none() -> None:
    payload = _full_payload()
    payload["eventDate"] = "not a date"
    assert SetlistFM._parse_setlist(payload) is None


def test_parse_setlist_missing_required_returns_none() -> None:
    payload = _full_payload()
    del payload["eventDate"]
    assert SetlistFM._parse_setlist(payload) is None


# ---- _fetch_partition tests ----


def test_fetch_partition_exhaustive_under_cap(tmp_path, monkeypatch) -> None:
    """When total < 10000, fetch every page up to ceil(total/20)."""
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    src = SetlistFM(country_code="US", years=[2022], state_codes=["CA"])

    total = 437  # arbitrary under-cap number
    calls: list[int] = []

    def fake_fetch_page(self, client, country, state, year, page):
        calls.append(page)
        return _fake_response(total, page)

    out_dir = tmp_path / "raw" / "setlistfm" / "US_CA_2022"
    with patch.object(SetlistFM, "_fetch_page", new=fake_fetch_page):
        n_pages = src._fetch_partition(None, "US", "CA", 2022, out_dir)

    expected_pages = math.ceil(total / 20)
    assert n_pages == expected_pages
    assert calls == list(range(1, expected_pages + 1))
    written = sorted(out_dir.glob("page_*.json"))
    assert len(written) == expected_pages


def test_fetch_partition_caps_at_max_pages(tmp_path, monkeypatch) -> None:
    """When total exceeds cap, stop at max_pages_per_partition."""
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    src = SetlistFM(
        country_code="US", years=[2022], state_codes=["CA"], max_pages=3
    )  # max_pages=3 for a fast test

    calls: list[int] = []

    def fake_fetch_page(self, client, country, state, year, page):
        calls.append(page)
        return _fake_response(50_000, page)  # 50K total → would be 2500 pages

    out_dir = tmp_path / "raw" / "setlistfm" / "US_CA_2022"
    with patch.object(SetlistFM, "_fetch_page", new=fake_fetch_page):
        n_pages = src._fetch_partition(None, "US", "CA", 2022, out_dir)

    assert n_pages == 3
    assert calls == [1, 2, 3]


def test_fetch_partition_empty_partition(tmp_path, monkeypatch) -> None:
    """When total == 0, write zero files and return 0."""
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    src = SetlistFM(country_code="US", years=[2022], state_codes=["WY"])

    def fake_fetch_page(self, client, country, state, year, page):
        return _fake_response(0, page)

    out_dir = tmp_path / "raw" / "setlistfm" / "US_WY_2022"
    out_dir.mkdir(parents=True)
    with patch.object(SetlistFM, "_fetch_page", new=fake_fetch_page):
        n_pages = src._fetch_partition(None, "US", "WY", 2022, out_dir)

    assert n_pages == 0
    assert list(out_dir.glob("page_*.json")) == []


# ---- to_cleaned tests ----


def test_to_cleaned_writes_parquet_with_expected_schema(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = SetlistFM()
    raw_root = tmp_path / "raw" / "setlistfm"
    partition = raw_root / "US_CA_2022"
    partition.mkdir(parents=True)
    page1 = _fake_response(40, 1)
    page2 = _fake_response(40, 2)
    (partition / "page_0001.json").write_text(json.dumps(page1))
    (partition / "page_0002.json").write_text(json.dumps(page2))

    out_path = src.to_cleaned(raw_root)

    assert out_path.exists() and out_path.suffix == ".parquet"
    df = pl.read_parquet(out_path)
    assert df.height == 40
    expected = {
        "setlist_id",
        "artist_name",
        "artist_mbid",
        "event_date",
        "venue_name",
        "venue_id",
        "city_name",
        "state_code",
        "country_code",
        "venue_lat",
        "venue_lon",
        "tour_name",
        "info_text",
        "n_songs",
        "raw_payload",
        "fetched_at",
    }
    assert expected.issubset(set(df.columns))


def test_to_cleaned_deduplicates_setlist_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = SetlistFM()
    raw_root = tmp_path / "raw" / "setlistfm"
    partition = raw_root / "US_CA_2022"
    partition.mkdir(parents=True)

    page = _fake_response(40, 1)
    # Force a duplicate setlist_id between two pages
    page2 = _fake_response(40, 2)
    page2["setlist"][0]["id"] = page["setlist"][0]["id"]
    (partition / "page_0001.json").write_text(json.dumps(page))
    (partition / "page_0002.json").write_text(json.dumps(page2))

    out_path = src.to_cleaned(raw_root)
    df = pl.read_parquet(out_path)
    # Duplicate id should be deduped; 40 total - 1 dup = 39
    assert df["setlist_id"].n_unique() == df.height
    assert df.height == 39


def test_to_cleaned_skips_unparseable_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = SetlistFM()
    raw_root = tmp_path / "raw" / "setlistfm"
    partition = raw_root / "US_CA_2022"
    partition.mkdir(parents=True)

    page = _fake_response(2, 1)
    page["setlist"][1]["eventDate"] = "garbage"  # unparseable
    (partition / "page_0001.json").write_text(json.dumps(page))

    out_path = src.to_cleaned(raw_root)
    df = pl.read_parquet(out_path)
    assert df.height == 1  # only the valid row survives
