"""Tests for transforms.geo."""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest


def test_loader_caches_after_first_read(
    monkeypatch: pytest.MonkeyPatch, fake_counties_geo: Path
) -> None:
    """Second call must not re-read the GeoParquet from disk."""
    from eia.transforms import geo

    real_read = gpd.read_parquet
    calls = {"n": 0}

    def counting_read(*args: object, **kwargs: object) -> gpd.GeoDataFrame:
        calls["n"] += 1
        return real_read(*args, **kwargs)

    monkeypatch.setattr("geopandas.read_parquet", counting_read)

    first = geo._load_counties_gdf(2023)
    second = geo._load_counties_gdf(2023)

    assert first is second
    assert calls["n"] == 1


def test_loader_raises_clear_error_when_geo_parquet_missing(
    fake_counties_geo: Path,
) -> None:
    """If the file is missing, the error message must point the user at make pull-tiger."""
    from eia.transforms import geo

    fake_counties_geo.unlink()  # delete the planted fixture
    geo._COUNTIES_CACHE.clear()

    with pytest.raises(FileNotFoundError, match=r"make pull-tiger"):
        geo._load_counties_gdf(2023)


def test_attach_county_fips_full_matrix(fake_counties_geo: Path) -> None:
    """One test, six rows, one assertion per edge-case-matrix row."""
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        {
            "venue_name": ["inside-A", "inside-B", "off-county", "null-lat", "sentinel-zero", "out-of-range"],
            "venue_lat": [1.0, 11.0, 5.0, None, 0.0, 91.0],
            "venue_lon": [1.0, 11.0, 5.0, 1.0, 0.0, 1.0],
        }
    )

    out = attach_county_fips(df)

    # Same shape + new column.
    assert out.height == df.height
    assert out["venue_name"].to_list() == df["venue_name"].to_list()
    assert out.columns == [*df.columns, "county_fips"]

    fips = out["county_fips"].to_list()
    assert fips[0] == "00001"   # inside county A
    assert fips[1] == "00002"   # inside county B
    assert fips[2] is None      # off-county
    assert fips[3] is None      # null lat
    assert fips[4] is None      # (0, 0) sentinel
    assert fips[5] is None      # lat out of range


def test_attach_county_fips_rejects_reserved_column(fake_counties_geo: Path) -> None:
    """The internal `__pos__` column must not collide with caller-provided columns."""
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        {
            "venue_lat": [1.0],
            "venue_lon": [1.0],
            "__pos__": [42],
        }
    )

    with pytest.raises(ValueError, match=r"reserved column '__pos__'"):
        attach_county_fips(df)


def test_attach_county_fips_logs_miss_summary(
    fake_counties_geo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        {
            "venue_lat": [1.0, 11.0, 5.0, None, 91.0],
            "venue_lon": [1.0, 11.0, 5.0, 1.0, 1.0],
        }
    )

    with caplog.at_level(logging.INFO, logger="eia.transforms.geo"):
        attach_county_fips(df)

    matched = [r for r in caplog.records if "attach_county_fips" in r.getMessage()]
    assert len(matched) == 1
    msg = matched[0].getMessage()
    # 5 rows total, 2 hits, 1 off-county, 2 bad coords -> 3 unmapped
    assert "3/5 unmapped" in msg
    assert "2 bad coords" in msg
    assert "1 off-county" in msg
    assert "tiger_year=2023" in msg


def test_attach_county_fips_empty_dataframe(fake_counties_geo: Path) -> None:
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        schema={"venue_lat": pl.Float64, "venue_lon": pl.Float64},
    )
    out = attach_county_fips(df)
    assert out.height == 0
    assert out.columns == ["venue_lat", "venue_lon", "county_fips"]
    assert out.schema["county_fips"] == pl.Utf8


def test_attach_county_fips_passes_other_columns_through(
    fake_counties_geo: Path,
) -> None:
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        {
            "event_id": ["tm_1", "tm_2"],
            "event_name": ["Show A", "Show B"],
            "event_date": ["2023-08-01", "2023-08-02"],
            "venue_lat": [1.0, 11.0],
            "venue_lon": [1.0, 11.0],
            "expected_attendance": [500, 1200],
        }
    )
    out = attach_county_fips(df)

    # All input columns preserved, in original order, plus county_fips.
    assert out.columns == [*df.columns, "county_fips"]
    for col in df.columns:
        assert out[col].to_list() == df[col].to_list()
    assert out["county_fips"].to_list() == ["00001", "00002"]


def test_public_import(fake_counties_geo: Path) -> None:
    """The canonical caller form — `from eia.transforms import attach_county_fips`."""
    from eia.transforms import attach_county_fips as imported

    df = pl.DataFrame({"venue_lat": [1.0], "venue_lon": [1.0]})
    out = imported(df)
    assert out["county_fips"].to_list() == ["00001"]


# ---- attach_county_fips_via_zip tests ----


def test_zip_already_set_fips_preserved(fake_counties_geo: Path) -> None:
    """If county_fips is already non-null, the function does not overwrite it."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {
            "venue_zip": ["92101", "92103"],
            "county_fips": ["99999", None],
        },
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {
            "zip": ["92101", "92103"],
            "county_fips": ["06073", "06073"],
            "res_ratio": [1.0, 1.0],
        }
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == ["99999", "06073"]


def test_zip_null_fips_filled_from_single_county_zip(
    fake_counties_geo: Path,
) -> None:
    """A null county_fips + ZIP mapping to one county gets filled."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": ["92101"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == ["06073"]


def test_zip_max_res_ratio_wins_multi_county(fake_counties_geo: Path) -> None:
    """A ZIP spanning multiple counties picks the max res_ratio one."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": ["12345"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {
            "zip": ["12345", "12345", "12345"],
            "county_fips": ["00001", "00002", "00003"],
            "res_ratio": [0.2, 0.7, 0.1],
        }
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == ["00002"]


def test_zip_tie_on_res_ratio_breaks_alphabetically(
    fake_counties_geo: Path,
) -> None:
    """Ties on res_ratio resolve to the alphabetically-lowest county_fips."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": ["12345"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {
            "zip": ["12345", "12345"],
            "county_fips": ["00002", "00001"],
            "res_ratio": [0.5, 0.5],
        }
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == ["00001"]


def test_zip_null_zip_stays_null(fake_counties_geo: Path) -> None:
    """A null venue_zip with null county_fips stays null."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": [None], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == [None]


def test_zip_not_in_hud_stays_null(fake_counties_geo: Path) -> None:
    """A ZIP that isn't in HUD leaves county_fips null."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": ["99999"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == [None]


def test_zip_empty_input(fake_counties_geo: Path) -> None:
    """Empty input frame returns empty frame, columns preserved."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": [], "county_fips": []},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out.height == 0
    assert out.columns == ["venue_zip", "county_fips"]


def test_zip_missing_fips_col_raises(fake_counties_geo: Path) -> None:
    """If fips_col is absent, raise ValueError pointing the caller at attach_county_fips."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame({"venue_zip": ["92101"]})
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    with pytest.raises(ValueError, match=r"attach_county_fips"):
        attach_county_fips_via_zip(df, hud)


def test_zip_custom_column_names(fake_counties_geo: Path) -> None:
    """Caller can override zip_col and fips_col."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"the_zip": ["92101"], "the_fips": [None]},
        schema={"the_zip": pl.Utf8, "the_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(
        df, hud, zip_col="the_zip", fips_col="the_fips"
    )

    assert out["the_fips"].to_list() == ["06073"]


def test_zip_logs_summary(
    fake_counties_geo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One INFO line per call with fill counts."""
    import logging

    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {
            "venue_zip": ["92101", None, "99999", "92103"],
            "county_fips": [None, None, None, "99999"],
        },
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {
            "zip": ["92101"],
            "county_fips": ["06073"],
            "res_ratio": [1.0],
            "quarter": ["2024Q1"],
        }
    )

    with caplog.at_level(logging.INFO, logger="eia.transforms.geo"):
        attach_county_fips_via_zip(df, hud)

    matched = [
        r for r in caplog.records if "attach_county_fips_via_zip" in r.getMessage()
    ]
    assert len(matched) == 1
    msg = matched[0].getMessage()
    assert "filled 1/3" in msg
    assert "1 no ZIP" in msg
    assert "1 ZIP not in crosswalk" in msg
    assert "2024Q1" in msg
