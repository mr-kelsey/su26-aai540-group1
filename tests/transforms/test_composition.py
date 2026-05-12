"""Integration test for the full Phase 1 transform composition.

Documents the canonical call-site pattern that event sources will use:

    df = attach_county_fips(df)        # lat/lon -> county_fips
    df = attach_period_id(df)          # event_date -> period_id + period_month
    # ...later, downstream feature ETL:
    panel = build_county_month_panel(counties)
    training_input = panel.join(event_aggs, on=["county_fips", "period_month"], how="left")

If this test breaks while individual transform tests still pass, a composition
contract has drifted (e.g. column names, format strings, or the join keys no
longer line up across the three transforms). Fix that first.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def test_full_transform_composition(fake_counties_geo: Path) -> None:
    """End-to-end: raw events -> panel-joined training input shape.

    The fake_counties_geo fixture (from conftest.py) plants two test counties:
        GEOID 00001 covering bbox lon=[0,2], lat=[0,2]
        GEOID 00002 covering bbox lon=[10,12], lat=[10,12]
    """
    from eia.transforms import (
        attach_county_fips,
        attach_period_id,
        build_county_month_panel,
    )

    # Three synthetic events: two in county 00001 (different months) and one in 00002.
    events = pl.DataFrame(
        {
            "event_id": ["e1", "e2", "e3"],
            "event_name": ["Concert A", "Marathon B", "Concert C"],
            "venue_lat": [1.0, 11.0, 1.0],
            "venue_lon": [1.0, 11.0, 1.0],
            "event_date": [date(2023, 7, 15), date(2023, 8, 1), date(2023, 9, 5)],
        }
    )

    # Transform composition at the call site.
    events = attach_county_fips(events)
    events = attach_period_id(events)

    # Each transform is independently tested elsewhere; here we just confirm
    # the composed output has all the expected derived columns set.
    assert events["county_fips"].to_list() == ["00001", "00002", "00001"]
    assert events["period_id"].to_list() == ["2023Q3", "2023Q3", "2023Q3"]
    assert events["period_month"].to_list() == ["2023-07", "2023-08", "2023-09"]

    # Downstream feature ETL: build the panel, aggregate events, left-join.
    counties = pl.DataFrame({"county_fips": ["00001", "00002"]})
    panel = build_county_month_panel(counties, start_year=2023, end_year=2023)
    assert panel.height == 24  # 2 counties x 12 months

    event_aggs = events.group_by("county_fips", "period_month").agg(
        pl.len().alias("n_events"),
    )

    training_input = panel.join(
        event_aggs,
        on=["county_fips", "period_month"],
        how="left",
    ).with_columns(
        pl.col("n_events").fill_null(0),
    )

    # 24 county-months total; 3 have events (one per distinct event-month);
    # 21 are zero-event baseline cells the regression treats as untreated.
    assert training_input.height == 24
    assert training_input.filter(pl.col("n_events") > 0).height == 3
    assert training_input.filter(pl.col("n_events") == 0).height == 21
    assert training_input["n_events"].sum() == 3

    # The three event-bearing rows are at the expected (county, month) cells.
    treated = (
        training_input.filter(pl.col("n_events") > 0)
        .select("county_fips", "period_month")
        .sort("county_fips", "period_month")
    )
    assert treated.to_dicts() == [
        {"county_fips": "00001", "period_month": "2023-07"},
        {"county_fips": "00001", "period_month": "2023-09"},
        {"county_fips": "00002", "period_month": "2023-08"},
    ]
