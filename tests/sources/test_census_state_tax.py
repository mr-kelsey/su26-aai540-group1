"""Tests for the Census State Tax Collections source."""

from __future__ import annotations

import json

import polars as pl

from eia.sources.census_state_tax import CensusStateTax


def _odata_response_for_year(year: int) -> list[list]:
    """Build a Census-API-shaped 2D-array response for one year."""
    header = ["AGG_DESC", "GOVTYPE", "ITEM_CODE", "NAME", "YEAR", "AMOUNT", "time", "state"]
    return [
        header,
        ["STC001", "002", "AGG", "California", str(year), "150000000", str(year), "06"],
        ["STC004", "002", "T09", "California", str(year), "50000000", str(year), "06"],
        ["STC001", "002", "AGG", "Texas", str(year), "100000000", str(year), "48"],
        ["STC004", "002", "T09", "Texas", str(year), "45000000", str(year), "48"],
        # Sales-tax-free state with None amount
        ["STC004", "002", "T09", "Oregon", str(year), None, str(year), "41"],
    ]


def test_to_cleaned_parses_multi_year(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = CensusStateTax()
    raw_dir = tmp_path / "raw" / "census-state-tax"
    raw_dir.mkdir(parents=True)
    for year in (2022, 2023):
        (raw_dir / f"year_{year}.json").write_text(json.dumps(_odata_response_for_year(year)))

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    assert df.height == 10  # 5 rows x 2 years
    expected_cols = {
        "table_year",
        "period_id",
        "state_fips",
        "state_name",
        "govtype",
        "agg_desc",
        "item_code",
        "amount_thousands_usd",
        "fetched_at",
    }
    assert expected_cols.issubset(set(df.columns))
    # State FIPS is zero-padded.
    assert set(df["state_fips"].unique().to_list()) == {"06", "48", "41"}
    # period_id format
    assert set(df["period_id"].unique().to_list()) == {"2022-annual", "2023-annual"}


def test_to_cleaned_handles_null_amount(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = CensusStateTax()
    raw_dir = tmp_path / "raw" / "census-state-tax"
    raw_dir.mkdir(parents=True)
    (raw_dir / "year_2023.json").write_text(json.dumps(_odata_response_for_year(2023)))

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    # Oregon's T09 row should have null amount (sales-tax-free state)
    oregon = df.filter((pl.col("state_fips") == "41") & (pl.col("item_code") == "T09"))
    assert oregon.height == 1
    assert oregon["amount_thousands_usd"][0] is None


def test_to_cleaned_dedups_pk(tmp_path, monkeypatch) -> None:
    """If two raw rows somehow share (year, state, agg_desc, item_code), keep first."""
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = CensusStateTax()
    raw_dir = tmp_path / "raw" / "census-state-tax"
    raw_dir.mkdir(parents=True)
    # Two files with overlapping content (e.g. accidental re-fetch)
    payload = _odata_response_for_year(2023)
    (raw_dir / "year_2023a.json").write_text(json.dumps(payload))
    (raw_dir / "year_2023b.json").write_text(json.dumps(payload))

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    # Each PK combination should appear once (5 rows after dedup, not 10)
    assert df.height == 5


def test_fetch_writes_one_file_per_year(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = CensusStateTax()
    # Narrow year window so the test is fast
    src.start_year = 2022
    src.end_year = 2023

    calls: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get_json(self, path: str, params: dict | None = None, headers: dict | None = None) -> list:
            calls.append({"path": path, "params": params})
            year = int(params["time"])  # type: ignore[index]
            return _odata_response_for_year(year)

    monkeypatch.setattr("eia.sources.census_state_tax.RateLimitedClient", FakeClient)
    out = src.fetch()

    assert (out / "year_2022.json").exists()
    assert (out / "year_2023.json").exists()
    assert len(calls) == 2
    assert calls[0]["params"]["for"] == "state:*"
