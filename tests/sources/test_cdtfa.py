"""Tests for the CDTFA California taxable sales source."""

from __future__ import annotations

import json

import polars as pl

from eia.sources.cdtfa import CDTFA


def _full_row() -> dict:
    return {
        "CalendarYear": 2023,
        "Quarter": "Q3",
        "QuarterMonthfrom": 7,
        "QuarterMonthto": 9,
        "County": "ALAMEDA",
        "CountyCode": 1,
        "BusinessGroupCode": "C01",
        "BusinessType": "Motor Vehicle and Parts Dealers",
        "PermitDate": "2023-09-30",
        "NumberOfPermits": 1350,
        "TaxableTransactions": 1234567890,
        "DisclosureFlag": None,
    }


def _odata_response(rows: list[dict]) -> dict:
    return {
        "@odata.context": "https://cdtfa.ca.gov/dataportal/api/odata/$metadata#Taxable_Sales_Counties",
        "value": rows,
    }


def _fake_dim_county() -> pl.DataFrame:
    """Minimal dim_county fixture: name -> fips for a few CA counties."""
    return pl.DataFrame(
        {
            "county_fips": ["06001", "06037", "06075", "06079", "36061"],
            "county_name": [
                "Alameda County",
                "Los Angeles County",
                "San Francisco County",
                "San Luis Obispo County",
                "New York County",
            ],
            "state_fips": ["06", "06", "06", "06", "36"],
        }
    )


# ---- _period_id ----


def test_period_id_basic() -> None:
    assert CDTFA._period_id(2024, "Q3") == "2024Q3"


def test_period_id_q4() -> None:
    assert CDTFA._period_id(2025, "Q4") == "2025Q4"


def test_period_id_malformed_returns_none() -> None:
    assert CDTFA._period_id(2024, "Quarter 3") is None
    assert CDTFA._period_id(2024, "") is None
    assert CDTFA._period_id(2024, "X1") is None


# ---- _parse_row ----


def test_parse_row_full_fields() -> None:
    row = CDTFA._parse_row(_full_row())
    assert row is not None
    assert row["table_year"] == 2023
    assert row["quarter"] == 3
    assert row["period_id"] == "2023Q3"
    assert row["cdtfa_county_code"] == 1
    assert row["cdtfa_county_name"] == "ALAMEDA"
    assert row["business_group_code"] == "C01"
    assert row["business_type"] == "Motor Vehicle and Parts Dealers"
    assert row["permit_count"] == 1350
    assert row["taxable_sales_usd"] == 1234567890
    assert row["disclosure_flag"] is None


def test_parse_row_with_disclosure_flag() -> None:
    payload = _full_row()
    payload["DisclosureFlag"] = "D"
    row = CDTFA._parse_row(payload)
    assert row is not None
    assert row["disclosure_flag"] == "D"


def test_parse_row_null_taxable_transactions() -> None:
    payload = _full_row()
    payload["TaxableTransactions"] = None
    row = CDTFA._parse_row(payload)
    assert row is not None
    assert row["taxable_sales_usd"] is None


def test_parse_row_skips_malformed_quarter() -> None:
    payload = _full_row()
    payload["Quarter"] = "Quarter Three"
    assert CDTFA._parse_row(payload) is None


def test_parse_row_missing_year_returns_none() -> None:
    payload = _full_row()
    del payload["CalendarYear"]
    assert CDTFA._parse_row(payload) is None


# ---- _build_county_lookup ----


def test_county_lookup_simple_name() -> None:
    lookup = CDTFA._build_county_lookup(_fake_dim_county())
    assert lookup["ALAMEDA"] == "06001"


def test_county_lookup_multi_word() -> None:
    lookup = CDTFA._build_county_lookup(_fake_dim_county())
    assert lookup["LOS ANGELES"] == "06037"
    assert lookup["SAN FRANCISCO"] == "06075"
    assert lookup["SAN LUIS OBISPO"] == "06079"


def test_county_lookup_excludes_other_states() -> None:
    """New York County (in NY, state_fips=36) shouldn't appear."""
    lookup = CDTFA._build_county_lookup(_fake_dim_county())
    assert "NEW YORK" not in lookup


# ---- fetch + to_cleaned ----


def test_fetch_writes_single_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = CDTFA()

    calls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get_json(self, path: str, params: dict | None = None, headers: dict | None = None) -> dict:
            calls.append(path)
            return _odata_response([_full_row()])

    monkeypatch.setattr("eia.sources.cdtfa.RateLimitedClient", FakeClient)
    out = src.fetch()

    assert out.exists()
    assert (out / "taxable_sales_counties.json").exists()
    assert calls == ["/dataportal/api/odata/Taxable_Sales_Counties"]


def _tmp_warehouse_with_fake_dim_county(tmp_path) -> "DuckDBWarehouse":
    """Build an ISOLATED tmp DuckDB warehouse pre-populated with a fake
    dim_county. Don't touch the real warehouse — that's a foot-gun (we
    discovered the hard way in feature/cdtfa-source)."""
    from eia.warehouse.duckdb_impl import DuckDBWarehouse

    wh = DuckDBWarehouse(tmp_path / "test.duckdb")
    wh.migrate()
    fake_dim_path = tmp_path / "fake_dim.parquet"
    _fake_dim_county().write_parquet(fake_dim_path)
    wh.register_table_from_parquet("dim_county", fake_dim_path, replace=True)
    return wh


def test_to_cleaned_writes_parquet_with_fips(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = CDTFA()
    raw_dir = tmp_path / "raw" / "cdtfa-taxable-sales"
    raw_dir.mkdir(parents=True)

    rows = [
        {**_full_row(), "County": "ALAMEDA", "CountyCode": 1, "BusinessGroupCode": "C01"},
        {**_full_row(), "County": "LOS ANGELES", "CountyCode": 19, "BusinessGroupCode": "C01"},
        {**_full_row(), "County": "ALAMEDA", "CountyCode": 1, "BusinessGroupCode": "C02"},
    ]
    (raw_dir / "taxable_sales_counties.json").write_text(json.dumps(_odata_response(rows)))

    fake_wh = _tmp_warehouse_with_fake_dim_county(tmp_path)
    monkeypatch.setattr("eia.warehouse.get_warehouse", lambda: fake_wh)

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    assert df.height == 3
    expected_cols = {
        "table_year", "quarter", "period_id", "county_fips",
        "cdtfa_county_code", "cdtfa_county_name", "business_group_code",
        "business_type", "permit_count", "taxable_sales_usd",
        "disclosure_flag", "fetched_at",
    }
    assert expected_cols.issubset(set(df.columns))
    alameda = df.filter(pl.col("cdtfa_county_name") == "ALAMEDA")
    la = df.filter(pl.col("cdtfa_county_name") == "LOS ANGELES")
    assert alameda["county_fips"].unique().to_list() == ["06001"]
    assert la["county_fips"].unique().to_list() == ["06037"]


def test_to_cleaned_unknown_county_gets_null_fips(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = CDTFA()
    raw_dir = tmp_path / "raw" / "cdtfa-taxable-sales"
    raw_dir.mkdir(parents=True)

    rows = [{**_full_row(), "County": "ATLANTIS", "CountyCode": 99}]
    (raw_dir / "taxable_sales_counties.json").write_text(json.dumps(_odata_response(rows)))

    fake_wh = _tmp_warehouse_with_fake_dim_county(tmp_path)
    monkeypatch.setattr("eia.warehouse.get_warehouse", lambda: fake_wh)

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    assert df.height == 1
    assert df["county_fips"][0] is None
