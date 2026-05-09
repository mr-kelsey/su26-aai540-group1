"""Tests for transforms.panel."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest


def test_empty_counties_returns_empty_panel() -> None:
    """Empty input yields empty output with the five expected columns and dtypes."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": []}, schema={"county_fips": pl.Utf8})
    panel = build_county_month_panel(counties, start_year=2020, end_year=2020)

    assert panel.height == 0
    assert panel.columns == ["county_fips", "period_month", "period_id", "year", "month"]
    assert panel.schema["county_fips"] == pl.Utf8
    assert panel.schema["period_month"] == pl.Utf8
    assert panel.schema["period_id"] == pl.Utf8
    assert panel.schema["year"] == pl.Int32
    assert panel.schema["month"] == pl.Int32


def test_single_county_single_year_yields_12_rows() -> None:
    """One county × one year produces 12 rows in calendar order with correct period strings."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": ["06073"]})
    panel = build_county_month_panel(counties, start_year=2015, end_year=2015)

    assert panel.height == 12
    assert panel["county_fips"].unique().to_list() == ["06073"]
    assert panel["month"].to_list() == list(range(1, 13))
    assert panel["period_month"].to_list() == [
        "2015-01", "2015-02", "2015-03", "2015-04",
        "2015-05", "2015-06", "2015-07", "2015-08",
        "2015-09", "2015-10", "2015-11", "2015-12",
    ]
    assert panel["period_id"].to_list() == [
        "2015Q1", "2015Q1", "2015Q1",
        "2015Q2", "2015Q2", "2015Q2",
        "2015Q3", "2015Q3", "2015Q3",
        "2015Q4", "2015Q4", "2015Q4",
    ]


def test_two_counties_two_years_yields_48_rows_sorted() -> None:
    """Output is sorted (county_fips, year, month) deterministically."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": ["06073", "36061"]})
    panel = build_county_month_panel(counties, start_year=2015, end_year=2016)

    assert panel.height == 48  # 2 counties × 2 years × 12 months

    # First 24 rows are "06073" (alphabetically first), last 24 are "36061".
    first_24 = panel.head(24)
    last_24 = panel.tail(24)
    assert first_24["county_fips"].unique().to_list() == ["06073"]
    assert last_24["county_fips"].unique().to_list() == ["36061"]

    # Within each county, order is (year asc, month asc).
    assert first_24["year"].to_list()[:12] == [2015] * 12
    assert first_24["year"].to_list()[12:] == [2016] * 12
    assert first_24["month"].to_list() == list(range(1, 13)) * 2


def test_format_consistency_with_attach_period_id() -> None:
    """The panel's period strings must match attach_period_id byte-for-byte for the same dates."""
    from eia.transforms.panel import build_county_month_panel
    from eia.transforms.temporal import attach_period_id

    counties = pl.DataFrame({"county_fips": ["00001"]})
    panel = build_county_month_panel(counties, start_year=2023, end_year=2023)

    # Run attach_period_id on the first-of-month dates for the same year.
    dates_df = pl.DataFrame(
        {"event_date": [date(2023, m, 1) for m in range(1, 13)]}
    )
    via_attach = attach_period_id(dates_df)

    assert panel["period_month"].to_list() == via_attach["period_month"].to_list()
    assert panel["period_id"].to_list() == via_attach["period_id"].to_list()


def test_duplicate_fips_silently_deduped() -> None:
    """Repeated FIPS in input collapse to one set of months in output."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": ["06073", "06073", "06073"]})
    panel = build_county_month_panel(counties, start_year=2020, end_year=2020)

    assert panel.height == 12  # not 36
    assert panel["county_fips"].unique().to_list() == ["06073"]


def test_start_year_greater_than_end_year_raises() -> None:
    """Reversed year range is a programmer error, not a silent empty result."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": ["06073"]})
    with pytest.raises(ValueError, match=r"start_year .* > end_year"):
        build_county_month_panel(counties, start_year=2025, end_year=2020)


def test_null_fips_raises() -> None:
    """A null FIPS in the input is a data-quality bug — fail loud, not silent."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame(
        {"county_fips": ["06073", None, "36061"]},
        schema={"county_fips": pl.Utf8},
    )
    with pytest.raises(ValueError, match=r"county_fips contains null"):
        build_county_month_panel(counties)


def test_custom_fips_col() -> None:
    """Caller can pass an alternative column name for the input FIPS column."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"my_fips": ["06073"]})
    panel = build_county_month_panel(
        counties, fips_col="my_fips", start_year=2020, end_year=2020
    )

    assert panel.columns == ["my_fips", "period_month", "period_id", "year", "month"]
    assert panel["my_fips"].to_list() == ["06073"] * 12
