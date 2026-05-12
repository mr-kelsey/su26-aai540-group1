"""Tests for the Texas Comptroller source."""

from __future__ import annotations

import json

import polars as pl

from eia.sources.tx_comptroller import TXComptroller, _as_float


def _socrata_row(name: str = "Anderson", year: str = "2023", month: str = "5") -> dict:
    return {
        "type": "COUNTY",
        "name": name,
        "current_rate": "0.5",
        "net_payment_this_period": "514323.7",
        "comparable_payment_prior_year": "388117.31",
        "percent_change_prior_year": "32.51",
        "payments_to_date": "2500569.64",
        "previous_payments_to_date": "2064901.59",
        "percent_change_to_date": "21.09",
        "report_month": month,
        "report_year": year,
        "report_period_type": "MONTHLY",
    }


def _fake_dim_county_tx() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "county_fips": ["48001", "48201", "48029", "48141"],
            "county_name": ["Anderson County", "Harris County", "Bexar County", "El Paso County"],
            "state_fips": ["48", "48", "48", "48"],
        }
    )


def _tmp_warehouse_with_fake_dim_county(tmp_path):  # type: ignore[no-untyped-def]
    from eia.warehouse.duckdb_impl import DuckDBWarehouse

    wh = DuckDBWarehouse(tmp_path / "test.duckdb")
    wh.migrate()
    fake_dim_path = tmp_path / "fake_dim.parquet"
    _fake_dim_county_tx().write_parquet(fake_dim_path)
    wh.register_table_from_parquet("dim_county", fake_dim_path, replace=True)
    return wh


def test_as_float_handles_strings_and_none() -> None:
    assert _as_float("123.45") == 123.45
    assert _as_float("") is None
    assert _as_float(None) is None
    assert _as_float("not a number") is None
    assert _as_float(0) == 0.0


def test_to_cleaned_normalizes_and_maps_fips(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = TXComptroller()
    raw_dir = tmp_path / "raw" / "tx-comptroller"
    raw_dir.mkdir(parents=True)
    payload = [
        _socrata_row(name="Anderson", year="2023", month="5"),
        _socrata_row(name="Harris", year="2023", month="5"),
        _socrata_row(name="Mars", year="2023", month="5"),  # unknown name → null FIPS
    ]
    (raw_dir / "page_0001.json").write_text(json.dumps(payload))

    fake_wh = _tmp_warehouse_with_fake_dim_county(tmp_path)
    monkeypatch.setattr("eia.warehouse.get_warehouse", lambda: fake_wh)

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    assert df.height == 3
    anderson = df.filter(pl.col("county_name") == "Anderson")
    assert anderson["county_fips"][0] == "48001"
    assert anderson["period_id"][0] == "2023-05"
    assert anderson["net_payment_usd"][0] == 514323.7
    assert anderson["current_rate_pct"][0] == 0.5

    harris = df.filter(pl.col("county_name") == "Harris")
    assert harris["county_fips"][0] == "48201"

    mars = df.filter(pl.col("county_name") == "Mars")
    assert mars["county_fips"][0] is None


def test_to_cleaned_dedups_pk(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = TXComptroller()
    raw_dir = tmp_path / "raw" / "tx-comptroller"
    raw_dir.mkdir(parents=True)
    # Two pages with the same (period_id, county_name) → dedup keeps one
    page = [_socrata_row(name="Anderson", year="2023", month="5")]
    (raw_dir / "page_0001.json").write_text(json.dumps(page))
    (raw_dir / "page_0002.json").write_text(json.dumps(page))

    fake_wh = _tmp_warehouse_with_fake_dim_county(tmp_path)
    monkeypatch.setattr("eia.warehouse.get_warehouse", lambda: fake_wh)

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    assert df.height == 1


def test_to_cleaned_skips_unparseable_year(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = TXComptroller()
    raw_dir = tmp_path / "raw" / "tx-comptroller"
    raw_dir.mkdir(parents=True)
    bad = _socrata_row()
    bad["report_year"] = "garbage"
    (raw_dir / "page_0001.json").write_text(json.dumps([bad]))

    fake_wh = _tmp_warehouse_with_fake_dim_county(tmp_path)
    monkeypatch.setattr("eia.warehouse.get_warehouse", lambda: fake_wh)

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    assert df.height == 0


def test_fetch_paginates(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = TXComptroller()
    src.limit_per_page = 2  # tiny page to force pagination

    pages_returned = [
        [_socrata_row(name="A"), _socrata_row(name="B")],  # full page
        [_socrata_row(name="C")],  # last page < limit
    ]
    page_index = [0]
    seen_offsets: list[int] = []

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get_json(
            self, path: str, params: dict | None = None, headers: dict | None = None
        ) -> list:
            seen_offsets.append(params["$offset"])  # type: ignore[index]
            idx = page_index[0]
            page_index[0] += 1
            return pages_returned[idx] if idx < len(pages_returned) else []

    monkeypatch.setattr("eia.sources.tx_comptroller.RateLimitedClient", FakeClient)
    out = src.fetch()

    assert (out / "page_0000.json").exists()
    assert (out / "page_0001.json").exists()
    assert seen_offsets == [0, 2]
