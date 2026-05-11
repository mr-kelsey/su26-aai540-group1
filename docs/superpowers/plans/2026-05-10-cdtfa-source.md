# CDTFA California Taxable Sales Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the CDTFA source per [2026-05-10-cdtfa-source-design.md](../specs/2026-05-10-cdtfa-source-design.md) — single OData GET, ~30K rows of California county × quarter × business-type taxable sales since 2015 Q1. This becomes the model's Y target for California events.

**Architecture:** Source plugin following the existing ABC pattern (`fetch` → `to_cleaned` → `load`). Single HTTP GET; pure-Polars parsing; county name → FIPS lookup against `dim_county`.

**Tech Stack:** Python 3.11, Polars (DataFrames), httpx (via RateLimitedClient), pytest, DuckDB.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `migrations/007_cdtfa.sql` | CREATE | DDL for `cdtfa_taxable_sales` table |
| `configs/sources.yaml` | MODIFY | Add `cdtfa` section (endpoint, top) |
| `src/eia/sources/cdtfa.py` | CREATE | Source class — fetch, parse_row, FIPS lookup, to_cleaned |
| `src/eia/cli.py` | MODIFY (1 line) | Add `importlib.import_module("eia.sources.cdtfa")` in `_register_pull_commands` |
| `Makefile` | MODIFY | Add `pull-cdtfa` target |
| `tests/sources/test_cdtfa.py` | CREATE | Unit tests — parsing, FIPS lookup, to_cleaned shape |

Internal seams in `src/eia/sources/cdtfa.py` (tested independently):
- `_parse_row(payload: dict) -> dict | None` — pure JSON → schema row
- `_period_id(year: int, quarter_str: str) -> str | None` — `(2024, "Q3")` → `"2024Q3"`
- `_build_county_lookup(dim_county_df: pl.DataFrame) -> dict[str, str]` — `"ALAMEDA"` → `"06001"`

---

## Task 1: Migration + Config

**Files:**
- Create: `migrations/007_cdtfa.sql`
- Modify: `configs/sources.yaml`

- [ ] **Step 1: Create migration file**

Create `migrations/007_cdtfa.sql`:

```sql
CREATE TABLE IF NOT EXISTS cdtfa_taxable_sales (
    table_year          SMALLINT    NOT NULL,
    quarter             SMALLINT    NOT NULL,
    period_id           VARCHAR     NOT NULL,
    county_fips         CHAR(5),
    cdtfa_county_code   SMALLINT    NOT NULL,
    cdtfa_county_name   VARCHAR     NOT NULL,
    business_group_code VARCHAR,
    business_type       VARCHAR,
    permit_count        INTEGER,
    taxable_sales_usd   BIGINT,
    disclosure_flag     VARCHAR,
    fetched_at          TIMESTAMP   NOT NULL,
    PRIMARY KEY (period_id, cdtfa_county_code, business_group_code)
);

CREATE INDEX IF NOT EXISTS ix_cdtfa_county ON cdtfa_taxable_sales (county_fips);
CREATE INDEX IF NOT EXISTS ix_cdtfa_period ON cdtfa_taxable_sales (period_id);
```

- [ ] **Step 2: Add `cdtfa` to configs/sources.yaml**

Append to `configs/sources.yaml`:

```yaml
cdtfa:
  # California Department of Tax and Fee Administration — Taxable Table 3
  # (Counties by Type of Business), via the public OData endpoint.
  base_url: "https://cdtfa.ca.gov"
  dataset_path: "/dataportal/api/odata/Taxable_Sales_Counties"
  top: 100000  # Server returns all ~30K rows in one shot under this $top.
  requests_per_second: 1.0
```

- [ ] **Step 3: Apply migration; verify table**

```bash
make warehouse-init
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync python -c "
from eia.warehouse import get_warehouse
print(get_warehouse().query('DESCRIBE cdtfa_taxable_sales').to_pandas().to_string(index=False))
"
```

Expected: prints 12 columns matching the migration.

- [ ] **Step 4: Commit**

```bash
git add migrations/007_cdtfa.sql configs/sources.yaml
git commit -m "feat(cdtfa): add cdtfa_taxable_sales table + config"
```

---

## Task 2: `_parse_row` + `_period_id` helpers (TDD)

**Files:**
- Create: `tests/sources/test_cdtfa.py` (tests first)
- Create: `src/eia/sources/cdtfa.py` (helpers only; full source in later tasks)

- [ ] **Step 1: Write failing tests**

Create `tests/sources/test_cdtfa.py`:

```python
"""Tests for the CDTFA California taxable sales source."""

from __future__ import annotations

import json
from datetime import date

import polars as pl
import pytest

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
```

- [ ] **Step 2: Run tests; expect FAIL (import error / module missing)**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_cdtfa.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'eia.sources.cdtfa'`

- [ ] **Step 3: Implement minimal `CDTFA` class with the two static helpers**

Create `src/eia/sources/cdtfa.py`:

```python
"""CDTFA California Department of Tax and Fee Administration source.

Pulls Taxable Table 3 (Taxable Sales by County, by Type of Business) via
CDTFA's public OData JSON endpoint. Covers 2015 Q1 through the most recent
published quarter (typically ~5 months lagged). California-only; other
state DORs need their own source classes.

Endpoint: https://cdtfa.ca.gov/dataportal/api/odata/Taxable_Sales_Counties
No auth. No rate limit observed; we still go through RateLimitedClient.

See docs/superpowers/specs/2026-05-10-cdtfa-source-design.md for design.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from eia.clients import RateLimitedClient
from eia.sources.base import Source
from eia.sources.registry import register

logger = logging.getLogger(__name__)

_QUARTER_RE = re.compile(r"^Q([1-4])$")


class CDTFA(Source):
    name = "cdtfa-taxable-sales"
    target_table = "cdtfa_taxable_sales"
    raw_format = "json"

    def __init__(self) -> None:
        cfg = self._load_config()
        self.base_url = cfg["base_url"]
        self.dataset_path = cfg["dataset_path"]
        self.top = cfg["top"]
        self.requests_per_second = cfg["requests_per_second"]

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["cdtfa"]  # type: ignore[no-any-return]

    @staticmethod
    def _period_id(year: int, quarter_str: str) -> str | None:
        """Map (2024, 'Q3') -> '2024Q3'. Returns None for malformed quarter."""
        if not isinstance(quarter_str, str):
            return None
        m = _QUARTER_RE.match(quarter_str)
        if not m:
            return None
        return f"{year}Q{m.group(1)}"

    @staticmethod
    def _parse_row(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize one OData row into the cdtfa_taxable_sales-schema dict.

        Returns None if a required field (CalendarYear or Quarter) is missing
        or unparseable.
        """
        year_raw = payload.get("CalendarYear")
        quarter_str = payload.get("Quarter")
        if year_raw is None or quarter_str is None:
            return None
        try:
            year = int(year_raw)
        except (TypeError, ValueError):
            return None
        period_id = CDTFA._period_id(year, quarter_str)
        if period_id is None:
            return None
        quarter_num = int(period_id[-1])

        return {
            "table_year": year,
            "quarter": quarter_num,
            "period_id": period_id,
            "county_fips": None,  # filled by FIPS lookup later
            "cdtfa_county_code": int(payload.get("CountyCode") or 0),
            "cdtfa_county_name": payload.get("County") or "",
            "business_group_code": payload.get("BusinessGroupCode"),
            "business_type": payload.get("BusinessType"),
            "permit_count": payload.get("NumberOfPermits"),
            "taxable_sales_usd": payload.get("TaxableTransactions"),
            "disclosure_flag": payload.get("DisclosureFlag"),
            "fetched_at": datetime.utcnow(),
        }

    def fetch(self) -> Path:
        raise NotImplementedError("Implemented in Task 4")

    def to_cleaned(self, raw_path: Path) -> Path:
        raise NotImplementedError("Implemented in Task 4")


register(CDTFA.name, CDTFA)
```

- [ ] **Step 4: Run tests; expect all 8 to PASS**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_cdtfa.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/sources/test_cdtfa.py src/eia/sources/cdtfa.py
git commit -m "feat(cdtfa): _parse_row + _period_id helpers with tests"
```

---

## Task 3: County FIPS lookup helper (TDD)

**Files:**
- Modify: `src/eia/sources/cdtfa.py` (add `_build_county_lookup`)
- Modify: `tests/sources/test_cdtfa.py` (add lookup tests)

- [ ] **Step 1: Add tests for the lookup**

Append to `tests/sources/test_cdtfa.py`:

```python
# ---- _build_county_lookup ----


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
```

- [ ] **Step 2: Run; expect 3 new FAIL**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_cdtfa.py -v
```

Expected: 8 pass, 3 fail (AttributeError: `_build_county_lookup`).

- [ ] **Step 3: Implement the helper**

In `src/eia/sources/cdtfa.py`, add this static method after `_parse_row`:

```python
    @staticmethod
    def _build_county_lookup(dim_county: pl.DataFrame) -> dict[str, str]:
        """Build {CDTFA_uppercase_name: county_fips} dict from dim_county.

        Restricts to CA counties (state_fips='06'). Normalizes by uppercasing
        and stripping a trailing ' COUNTY' so CDTFA's 'ALAMEDA' matches
        dim_county's 'Alameda County'.
        """
        ca = dim_county.filter(pl.col("state_fips") == "06").select(
            pl.col("county_fips"),
            pl.col("county_name")
            .str.to_uppercase()
            .str.replace(r" COUNTY$", "")
            .alias("_norm"),
        )
        return dict(zip(ca["_norm"].to_list(), ca["county_fips"].to_list(), strict=True))
```

- [ ] **Step 4: Run; expect 11 passed**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_cdtfa.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/sources/test_cdtfa.py src/eia/sources/cdtfa.py
git commit -m "feat(cdtfa): _build_county_lookup helper with tests"
```

---

## Task 4: `fetch` + `to_cleaned` (TDD)

**Files:**
- Modify: `src/eia/sources/cdtfa.py` (replace the two `NotImplementedError` stubs)
- Modify: `tests/sources/test_cdtfa.py` (add fetch + to_cleaned tests)

- [ ] **Step 1: Add tests**

Append to `tests/sources/test_cdtfa.py`:

```python
# ---- fetch + to_cleaned ----


def _odata_response(rows: list[dict]) -> dict:
    return {
        "@odata.context": "https://cdtfa.ca.gov/dataportal/api/odata/$metadata#Taxable_Sales_Counties",
        "value": rows,
    }


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

    # Stub dim_county as a registered table the source reads
    from eia.warehouse import get_warehouse
    wh = get_warehouse()
    wh.migrate()
    fake_dim = _fake_dim_county()
    fake_dim_path = tmp_path / "fake_dim.parquet"
    fake_dim.write_parquet(fake_dim_path)
    wh.register_table_from_parquet("dim_county", fake_dim_path, replace=True)

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
    # FIPS should be populated for both counties
    alameda = df.filter(pl.col("cdtfa_county_name") == "ALAMEDA")
    la = df.filter(pl.col("cdtfa_county_name") == "LOS ANGELES")
    assert alameda["county_fips"].unique().to_list() == ["06001"]
    assert la["county_fips"].unique().to_list() == ["06037"]


def test_to_cleaned_unknown_county_gets_null_fips(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    src = CDTFA()
    raw_dir = tmp_path / "raw" / "cdtfa-taxable-sales"
    raw_dir.mkdir(parents=True)

    rows = [{**_full_row(), "County": "ATLANTIS", "CountyCode": 99}]
    (raw_dir / "taxable_sales_counties.json").write_text(json.dumps(_odata_response(rows)))

    from eia.warehouse import get_warehouse
    wh = get_warehouse()
    wh.migrate()
    fake_dim_path = tmp_path / "fake_dim.parquet"
    _fake_dim_county().write_parquet(fake_dim_path)
    wh.register_table_from_parquet("dim_county", fake_dim_path, replace=True)

    out = src.to_cleaned(raw_dir)
    df = pl.read_parquet(out)

    assert df.height == 1
    assert df["county_fips"][0] is None
```

- [ ] **Step 2: Run; expect 3 new FAIL (NotImplementedError)**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_cdtfa.py -v
```

Expected: 11 pass, 3 fail (NotImplementedError from `fetch`/`to_cleaned`).

- [ ] **Step 3: Implement `fetch` and `to_cleaned`**

In `src/eia/sources/cdtfa.py`, replace the two `raise NotImplementedError(...)` stubs:

```python
    def fetch(self) -> Path:
        """Single GET to CDTFA's OData endpoint; write JSON to raw_dir."""
        import json as _json

        out_dir = self.raw_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "taxable_sales_counties.json"
        with RateLimitedClient(
            self.base_url,
            requests_per_second=self.requests_per_second,
            timeout_s=60.0,
        ) as client:
            params = {"$top": self.top}
            headers = {"Accept": "application/json"}
            data = client.get_json(self.dataset_path, params=params, headers=headers)
        out_path.write_text(_json.dumps(data))
        n = len(data.get("value", []))
        logger.info("CDTFA fetch: %d rows written to %s", n, out_path)
        return out_dir

    def to_cleaned(self, raw_path: Path) -> Path:
        """Parse raw JSON, normalize rows, join FIPS lookup, write parquet."""
        import json as _json

        json_path = raw_path / "taxable_sales_counties.json"
        data = _json.loads(json_path.read_text())
        raw_rows = data.get("value", [])

        parsed: list[dict[str, Any]] = []
        for r in raw_rows:
            row = self._parse_row(r)
            if row is not None:
                parsed.append(row)

        from eia.warehouse import get_warehouse

        dim_county = get_warehouse().query(
            "SELECT county_fips, county_name, state_fips FROM dim_county"
        )
        lookup = self._build_county_lookup(dim_county)

        for row in parsed:
            row["county_fips"] = lookup.get(row["cdtfa_county_name"])

        n_unmapped = sum(1 for r in parsed if r["county_fips"] is None)
        if n_unmapped:
            unmapped_names = sorted(
                {r["cdtfa_county_name"] for r in parsed if r["county_fips"] is None}
            )
            logger.warning(
                "CDTFA: %d rows unmapped to county_fips; CDTFA names not in dim_county: %s",
                n_unmapped,
                unmapped_names,
            )

        df = pl.DataFrame(
            parsed,
            schema={
                "table_year": pl.Int16,
                "quarter": pl.Int16,
                "period_id": pl.Utf8,
                "county_fips": pl.Utf8,
                "cdtfa_county_code": pl.Int16,
                "cdtfa_county_name": pl.Utf8,
                "business_group_code": pl.Utf8,
                "business_type": pl.Utf8,
                "permit_count": pl.Int32,
                "taxable_sales_usd": pl.Int64,
                "disclosure_flag": pl.Utf8,
                "fetched_at": pl.Datetime,
            },
        )

        out = self.cleaned_dir / "taxable_sales_counties.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out)
        logger.info(
            "CDTFA to_cleaned: %d rows written to %s (%d unmapped to FIPS)",
            df.height,
            out,
            n_unmapped,
        )
        return out
```

- [ ] **Step 4: Run all 14 tests**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_cdtfa.py -v
```

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/sources/test_cdtfa.py src/eia/sources/cdtfa.py
git commit -m "feat(cdtfa): fetch + to_cleaned with FIPS lookup"
```

---

## Task 5: CLI + Makefile + smoke pull

**Files:**
- Modify: `src/eia/cli.py` (1-line import in `_register_pull_commands`)
- Modify: `Makefile` (add `pull-cdtfa` target)

- [ ] **Step 1: Register CDTFA in the CLI**

In `src/eia/cli.py`, in the `_register_pull_commands` function, find the block of `importlib.import_module(...)` lines and add this line after the others:

```python
    importlib.import_module("eia.sources.cdtfa")
```

It should sit alongside the other `importlib.import_module("eia.sources.<name>")` calls.

- [ ] **Step 2: Add the Makefile target**

In `Makefile`, find the `# ----- Federal data pulls (no auth required) -----` section, and add at the end of that block:

```makefile
pull-cdtfa:  ## Pull California CDTFA quarterly taxable sales by county/business type.
	$(UVRUN) eia pull cdtfa-taxable-sales
```

Also add `pull-cdtfa` to the `.PHONY:` list at the top of the file.

- [ ] **Step 3: Run the smoke pull**

```bash
make pull-cdtfa
```

Expected: takes ~5 seconds. Logs `CDTFA fetch: 30000+ rows written to ...` and `CDTFA to_cleaned: 30000+ rows written ...`.

- [ ] **Step 4: Verify the warehouse**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync python -c "
from eia.warehouse import get_warehouse
wh = get_warehouse()
print('Row count:', wh.query('SELECT COUNT(*) AS n FROM cdtfa_taxable_sales').item())
print()
print('Year coverage:')
print(wh.query('SELECT MIN(table_year) AS earliest, MAX(table_year) AS latest, COUNT(DISTINCT period_id) AS quarters FROM cdtfa_taxable_sales'))
print()
print('FIPS-mapped:')
print(wh.query('SELECT COUNT(*) AS total, COUNT(county_fips) AS mapped FROM cdtfa_taxable_sales'))
print()
print('San Diego 2023Q3 taxable sales by business type:')
print(wh.query(\"\"\"
    SELECT business_type, taxable_sales_usd / 1e6 AS sales_millions
    FROM cdtfa_taxable_sales
    WHERE county_fips = '06073' AND period_id = '2023Q3'
    ORDER BY taxable_sales_usd DESC
\"\"\"))
"
```

Expected:
- ~30,624 rows
- Year coverage 2015–2025, ~44 quarters
- All rows FIPS-mapped (CDTFA only publishes data for the 58 CA counties, all of which are in dim_county)
- San Diego 2023Q3 shows real dollar values by business type

- [ ] **Step 5: Demo cross-source join (events × Y target)**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync python -c "
from eia.warehouse import get_warehouse
wh = get_warehouse()
print('Top 5 CA counties: events count vs total taxable sales 2022')
print(wh.query(\"\"\"
    SELECT
      d.county_name,
      COUNT(e.event_id) AS events,
      SUM(c.taxable_sales_usd) / 1e9 AS total_taxable_sales_billions
    FROM dim_county d
    LEFT JOIN events e ON e.county_fips = d.county_fips
    LEFT JOIN cdtfa_taxable_sales c
      ON c.county_fips = d.county_fips
     AND c.table_year = 2022
    WHERE d.state_fips = '06'
    GROUP BY d.county_name
    ORDER BY events DESC NULLS LAST
    LIMIT 5
\"\"\"))
"
```

Expected: prints a table joining `dim_county` × `events` × `cdtfa_taxable_sales` for CA — proves the Y target is queryable alongside features.

- [ ] **Step 6: Commit**

```bash
git add src/eia/cli.py Makefile
git commit -m "feat(cdtfa): register in CLI + add make pull-cdtfa target"
```

---

## Task 6: Lint, mypy, full test suite, merge

- [ ] **Step 1: Lint**

```bash
chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
uv run --no-sync ruff check src tests pipelines
```

Expected: All checks passed!

- [ ] **Step 2: Format check; apply if needed**

```bash
uv run --no-sync ruff format src/eia/sources/cdtfa.py tests/sources/test_cdtfa.py
```

If anything reformatted, commit:

```bash
git add -u
git commit -m "chore: ruff format cdtfa source + tests"
```

- [ ] **Step 3: Mypy**

```bash
uv run --no-sync mypy src
```

Expected: Success: no issues found.

- [ ] **Step 4: Full test suite**

```bash
uv run --no-sync pytest --no-cov -q
```

Expected: 96 + 14 = 110 tests pass.

- [ ] **Step 5: Update STATUS.md**

In `docs/STATUS.md`, in the "Data layer" table, add a row for `cdtfa_taxable_sales`:

```markdown
| `cdtfa_taxable_sales` | 30,624 | California county × quarter × business-type taxable sales 2015 Q1 - 2025 Q4 — **the model's Y target for CA events** |
```

Commit:

```bash
git add docs/STATUS.md
git commit -m "docs(STATUS): note cdtfa_taxable_sales as CA Y target"
```

- [ ] **Step 6: Merge feature branch to main**

```bash
git checkout main
git merge --no-ff feature/cdtfa-source -m "Merge branch 'feature/cdtfa-source': CDTFA California Y-target source"
git branch -d feature/cdtfa-source
git log --oneline -5
```

---

## Self-Review

**Spec coverage:**
- Migration → Task 1 ✓
- Config → Task 1 ✓
- `_parse_row` helper + 5 tests → Task 2 ✓
- `_period_id` helper + 3 tests → Task 2 ✓
- `_build_county_lookup` helper + 3 tests → Task 3 ✓
- `fetch` (single GET) → Task 4 ✓
- `to_cleaned` (FIPS join + parquet write) + 3 tests → Task 4 ✓
- CLI registration → Task 5 ✓
- Makefile target → Task 5 ✓
- Smoke pull + warehouse verification → Task 5 ✓
- Cross-source demo (events × Y target) → Task 5 ✓

**Placeholder scan:** None. All code blocks are concrete.

**Type consistency:** `_parse_row` returns `dict[str, Any] | None`. `to_cleaned` filters out Nones, calls `_build_county_lookup`, fills `county_fips`. Output parquet schema matches the migration column types.

**Edge cases covered:**
- Disclosure flag preserved
- Null `TaxableTransactions` preserved
- Malformed quarter skipped
- Unknown county name → null FIPS + WARNING log
- Empty `value` array → empty parquet (no crash)
