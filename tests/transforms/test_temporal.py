"""Tests for transforms.temporal."""

from __future__ import annotations

from datetime import date

import polars as pl


def test_quarter_boundaries() -> None:
    """First day of each calendar quarter maps to the right Q."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame(
        {
            "event_date": [
                date(2023, 1, 1),
                date(2023, 4, 1),
                date(2023, 7, 1),
                date(2023, 10, 1),
            ],
        }
    )
    out = attach_period_id(df)
    assert out["period_id"].to_list() == ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    assert out["period_month"].to_list() == ["2023-01", "2023-04", "2023-07", "2023-10"]


def test_mid_quarter_dates() -> None:
    """Dates inside a quarter still map to that quarter."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame(
        {"event_date": [date(2023, 8, 15), date(2023, 12, 31)]}
    )
    out = attach_period_id(df)
    assert out["period_id"].to_list() == ["2023Q3", "2023Q4"]
    assert out["period_month"].to_list() == ["2023-08", "2023-12"]


def test_year_boundary() -> None:
    """Jan 1 of new year yields the new year, not the old one."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame({"event_date": [date(2024, 1, 1)]})
    out = attach_period_id(df)
    assert out["period_id"].to_list() == ["2024Q1"]
    assert out["period_month"].to_list() == ["2024-01"]


def test_null_date_propagates_to_both_columns() -> None:
    """A null event_date produces null in both output columns."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame(
        {"event_date": [date(2023, 7, 1), None, date(2023, 8, 15)]},
        schema={"event_date": pl.Date},
    )
    out = attach_period_id(df)
    assert out["period_id"].to_list() == ["2023Q3", None, "2023Q3"]
    assert out["period_month"].to_list() == ["2023-07", None, "2023-08"]


def test_custom_column_names() -> None:
    """Caller can override the input and output column names."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame({"the_date": [date(2023, 7, 15)]})
    out = attach_period_id(df, date_col="the_date", quarter_col="Q", month_col="M")
    assert out["Q"].to_list() == ["2023Q3"]
    assert out["M"].to_list() == ["2023-07"]
    # Defaults must NOT appear when custom names are used.
    assert "period_id" not in out.columns
    assert "period_month" not in out.columns


def test_passes_other_columns_through() -> None:
    """Realistic events-shape input — all original columns intact."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame(
        {
            "event_id": ["tm_1", "tm_2"],
            "event_name": ["Show A", "Show B"],
            "venue_lat": [32.7, 40.7],
            "venue_lon": [-117.2, -74.0],
            "event_date": [date(2023, 7, 15), date(2023, 8, 20)],
        }
    )
    out = attach_period_id(df)
    assert out.columns == [*df.columns, "period_id", "period_month"]
    for col in df.columns:
        assert out[col].to_list() == df[col].to_list()
    assert out["period_id"].to_list() == ["2023Q3", "2023Q3"]
    assert out["period_month"].to_list() == ["2023-07", "2023-08"]
