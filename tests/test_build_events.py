"""Tests for the build_events pipeline.

These tests verify the schema mapping logic — `_read_setlistfm` and
`_normalize_fetched_at`. The actual fetch logic (which requires a running
warehouse and TIGER data) is exercised in a separate integration run, not
in these unit tests.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import polars as pl
import pytest
from pipelines.build_events import (
    EVENTS_COLUMNS,
    _normalize_fetched_at,
    _read_setlistfm,
    _read_ticketmaster,
)


def _make_setlistfm_row() -> dict:
    return {
        "setlist_id": "abc123",
        "artist_name": "Radiohead",
        "artist_mbid": "mbid-x",
        "event_date": pl.Series([datetime(2022, 6, 1).date()]).to_list()[0],
        "venue_name": "The Greek Theatre",
        "venue_id": "v1",
        "city_name": "Berkeley",
        "state_code": "CA",
        "country_code": "US",
        "venue_lat": 37.8732,
        "venue_lon": -122.2547,
        "tour_name": "OK Computer Tour",
        "info_text": None,
        "n_songs": 12,
        "raw_payload": '{"id":"abc123"}',
        "fetched_at": datetime(2026, 5, 10, 12, 0, 0),
    }


def _make_ticketmaster_row() -> dict:
    return {
        "event_id": "tm_K8vZ...",
        "source": "ticketmaster",
        "category": "concert",
        "event_name": "Some Concert",
        "event_date": "2026-05-15",
        "venue_name": "The Forum",
        "venue_address": "123 Main St",
        "venue_city": "Inglewood",
        "venue_state": "CA",
        "venue_zip": "90305",
        "venue_lat": 33.9583,
        "venue_lon": -118.3417,
        "county_fips": None,
        "period_id": None,
        "period_month": None,
        "expected_attendance": None,
        "ticket_min_usd": 50.0,
        "ticket_max_usd": 250.0,
        "raw_payload": '{"id":"K8vZ"}',
        "fetched_at": datetime(2026, 5, 10, 12, 0, 0),
    }


def test_normalize_fetched_at_strips_tz() -> None:
    df = pl.DataFrame(
        {"fetched_at": [datetime(2026, 5, 10, 12, 0, 0)]},
        schema={"fetched_at": pl.Datetime(time_unit="us", time_zone="America/Phoenix")},
    )
    out = _normalize_fetched_at(df)
    assert out.schema["fetched_at"].time_zone is None


def test_normalize_fetched_at_passthrough_for_naive() -> None:
    df = pl.DataFrame({"fetched_at": [datetime(2026, 5, 10)]})
    out = _normalize_fetched_at(df)
    assert out.schema["fetched_at"].time_zone is None


def test_normalize_fetched_at_missing_column_no_op() -> None:
    df = pl.DataFrame({"x": [1, 2, 3]})
    out = _normalize_fetched_at(df)
    assert out.equals(df)


def test_read_setlistfm_maps_to_events_schema() -> None:
    row = _make_setlistfm_row()
    fake_wh = MagicMock()
    # First call: _table_exists (returns 1-row frame)
    # Second call: SELECT * (returns a setlistfm row)
    fake_wh.query.side_effect = [
        pl.DataFrame({"x": [1]}),
        pl.DataFrame([row]),
    ]

    out = _read_setlistfm(fake_wh)

    # All event-schema columns present, in order
    assert list(out.columns) == EVENTS_COLUMNS
    # Mapping correctness
    assert out["event_id"][0] == "sfm_abc123"
    assert out["source"][0] == "setlistfm"
    assert out["category"][0] == "concert"
    assert out["event_name"][0] == "Radiohead"
    assert out["event_date"][0] == "2022-06-01"
    assert out["venue_name"][0] == "The Greek Theatre"
    assert out["venue_city"][0] == "Berkeley"
    assert out["venue_state"][0] == "CA"
    assert out["venue_lat"][0] == pytest.approx(37.8732)
    assert out["venue_lon"][0] == pytest.approx(-122.2547)
    # Setlist.fm has no zip / address / tickets / attendance
    assert out["venue_zip"][0] is None
    assert out["venue_address"][0] is None
    assert out["ticket_min_usd"][0] is None
    assert out["ticket_max_usd"][0] is None
    assert out["expected_attendance"][0] is None


def test_read_setlistfm_empty_when_no_table() -> None:
    fake_wh = MagicMock()
    fake_wh.query.return_value = pl.DataFrame(schema={})  # _table_exists -> empty

    out = _read_setlistfm(fake_wh)
    assert out.is_empty()
    assert list(out.columns) == EVENTS_COLUMNS


def test_read_setlistfm_empty_when_table_has_no_rows() -> None:
    fake_wh = MagicMock()
    fake_wh.query.side_effect = [
        pl.DataFrame({"x": [1]}),  # _table_exists -> present
        pl.DataFrame(schema={"setlist_id": pl.Utf8}),  # empty SELECT
    ]
    out = _read_setlistfm(fake_wh)
    assert out.is_empty()


def test_read_ticketmaster_passthrough() -> None:
    row = _make_ticketmaster_row()
    fake_wh = MagicMock()
    fake_wh.query.side_effect = [
        pl.DataFrame({"x": [1]}),  # _table_exists
        pl.DataFrame([row]),
    ]

    out = _read_ticketmaster(fake_wh)
    assert list(out.columns) == EVENTS_COLUMNS
    assert out["event_id"][0] == "tm_K8vZ..."
    assert out["source"][0] == "ticketmaster"
    assert out["ticket_min_usd"][0] == 50.0


def test_read_ticketmaster_fills_missing_columns() -> None:
    """If staging predates the period_month column, it gets backfilled as null."""
    row = _make_ticketmaster_row()
    del row["period_month"]
    fake_wh = MagicMock()
    fake_wh.query.side_effect = [
        pl.DataFrame({"x": [1]}),
        pl.DataFrame([row]),
    ]
    out = _read_ticketmaster(fake_wh)
    assert "period_month" in out.columns
    assert out["period_month"][0] is None
