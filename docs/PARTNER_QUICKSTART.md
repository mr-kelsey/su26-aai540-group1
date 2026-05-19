# Partner Quickstart — AWS Data Lake

> **Audience:** AAI-540 Group 1 partners. This is "you got AWS credentials, now what?"

By the end of this doc you'll have run your first SQL query against the data lake. Should take about 10 minutes.

---

## TL;DR

1. `aws configure` with the credentials Steve sent you (region: `us-east-2`)
2. Browser → [Athena Query Editor](https://us-east-2.console.aws.amazon.com/athena/home?region=us-east-2#/query-editor) → Database: `aai540_gold`
3. Run: `SELECT * FROM model_training_matrix LIMIT 10;`

If that works, skip to [Sample queries](#sample-queries). If not, read on.

---

## What you have

- A username + temporary console password (sent privately by Steve)
- An `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (sent privately by Steve)
- Full **read/write** on the project's S3 prefixes, the Glue Data Catalog, and Athena workgroup
- **No access** to anything else in the AWS account (IAM, billing, other buckets, EC2, etc.)

Account: `541974874359` · Region: `us-east-2` · Bucket: `jonno-lucas-steve-bucket`

---

## Step 1: Configure the AWS CLI

```bash
# Install if you don't have it
brew install awscli                                   # macOS
# or follow https://aws.amazon.com/cli/ for Linux/Windows

aws configure
# AWS Access Key ID:     <paste from the message Steve sent>
# AWS Secret Access Key: <paste from the message Steve sent>
# Default region name:   us-east-2
# Default output format: json
```

Verify:

```bash
aws sts get-caller-identity
# Expected:
# {
#   "UserId":  "AIDA...",
#   "Account": "541974874359",
#   "Arn":     "arn:aws:iam::541974874359:user/jonno"   # or /lucas
# }
```

If that returns your username, you're in.

---

## Step 2: First console login (recommended)

Browse to **https://541974874359.signin.aws.amazon.com/console** and sign in with your username + the temp password.

You'll be required to:

1. **Change the password.** Pick something strong and store it in 1Password / your password manager.
2. **Set up MFA.** Top-right username → *Security credentials* → *Multi-factor authentication*. Any TOTP app works (Authy, 1Password, Google Authenticator). Don't skip this — it's a near-zero-effort security upgrade.

You don't need to use the console day-to-day (everything works from the CLI), but Athena's web Query Editor is genuinely the fastest way to write exploratory SQL.

---

## Step 3: Run queries

Pick whichever interface fits your workflow.

### 3a. Athena Query Editor (browser, easiest)

1. Console → search "Athena" → **Query editor**
2. Top of left panel:
   - Data source: `AwsDataCatalog`
   - Database: `aai540_silver` (or `aai540_gold`)
3. Type SQL into the editor, hit Run.

First time only: Athena will ask you to set a query-results location. It's already configured for the project (`s3://jonno-lucas-steve-bucket/usd-aai540-group1/athena-results/`), so just confirm.

### 3b. Athena from the CLI

```bash
# Kick off a query
QID=$(aws athena start-query-execution \
  --query-string "SELECT COUNT(*) FROM aai540_gold.model_training_matrix" \
  --query-execution-context Database=aai540_gold \
  --output text --query QueryExecutionId)
echo "$QID"

# Check status (poll until SUCCEEDED)
aws athena get-query-execution --query-execution-id "$QID" \
  --output text --query 'QueryExecution.Status.State'

# Pull the results
aws athena get-query-results --query-execution-id "$QID" --output table
```

### 3c. Python (boto3 + awswrangler — best for SageMaker / Jupyter)

```bash
pip install awswrangler pyarrow polars
```

```python
import awswrangler as wr

df = wr.athena.read_sql_query(
    "SELECT * FROM model_training_matrix WHERE year >= 2020",
    database="aai540_gold",
)
print(df.shape)
df.head()
```

`awswrangler` orchestrates Athena (start query → poll → fetch results → return Pandas). It uses your `~/.aws/credentials` automatically. Returns Pandas; convert with `pl.from_pandas(df)` if you prefer Polars.

### 3d. Read parquet directly (no Athena, no per-query cost)

The parquet files are right there in S3 and your IAM lets you read them. If you don't need SQL on the server, just pull them locally:

```python
import polars as pl

# Credentials picked up from ~/.aws/credentials
df = pl.read_parquet(
    "s3://jonno-lucas-steve-bucket/usd-aai540-group1/gold/model_training_matrix/**/*.parquet",
    storage_options={"region": "us-east-2"},
)
```

Or full SQL via DuckDB (no AWS service involved beyond S3 reads):

```python
import duckdb

con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-east-2';")
# duckdb picks up AWS_ACCESS_KEY_ID/SECRET from env or aws config

df = con.execute("""
  SELECT county_name, year, quarter,
         ROUND(taxable_sales_usd / 1e9, 2) AS billions,
         n_events
  FROM read_parquet('s3://jonno-lucas-steve-bucket/usd-aai540-group1/gold/model_training_matrix/**/*.parquet')
  WHERE year = 2022 AND quarter = 3
  ORDER BY taxable_sales_usd DESC
  LIMIT 5
""").pl()
```

---

## Sample queries

All of these run as-is in the Athena Query Editor or `awswrangler.athena.read_sql_query`.

### Top 5 CA counties by 2022 Q3 taxable sales

```sql
SELECT county_name,
       ROUND(taxable_sales_usd / 1e9, 2) AS billions,
       n_events,
       avg_employment
FROM aai540_gold.model_training_matrix
WHERE year = 2022 AND quarter = 3
ORDER BY taxable_sales_usd DESC
LIMIT 5;
```

Expected first row: Los Angeles County, ~$53B, ~1,000 events.

### Event volume by source over time

```sql
SELECT year, source, COUNT(*) AS n_events
FROM aai540_silver.events
WHERE year IS NOT NULL
GROUP BY year, source
ORDER BY year, source;
```

### San Diego training-matrix panel

```sql
SELECT period_id, taxable_sales_usd, n_events,
       total_wages_usd, avg_employment, population
FROM aai540_gold.model_training_matrix
WHERE county_fips = '06073'
ORDER BY year, quarter;
```

### Cross-source sanity (the Phase 0 exit query)

```sql
SELECT d.county_name, a.population, q.total_wages_usd, q.avg_employment
FROM aai540_silver.dim_county d
JOIN aai540_silver.census_acs_county a USING (county_fips)
JOIN aai540_silver.bls_qcew q USING (county_fips)
WHERE d.county_fips = '06073'   -- San Diego
  AND q.year = 2022 AND q.quarter = 3
  AND q.naics_code = '721'      -- Accommodation
  AND q.ownership_code = 5;     -- Private
```

If this returns a row, every join in the warehouse is healthy.

---

## Data lake map

```
s3://jonno-lucas-steve-bucket/usd-aai540-group1/
├── bronze/         Raw API dumps (verbatim). Read-only for archaeology.
├── silver/         Cleaned, FIPS-keyed parquet. Hive-partitioned.
│                   → Glue catalog: aai540_silver.*  (12 tables)
├── gold/           Model-ready training matrices.
│   └── model_training_matrix/
│                   → Glue catalog: aai540_gold.model_training_matrix
└── athena-results/ Your query outputs land here; auto-managed by Athena.
```

### What's in each catalog

| Database | Tables | Use it for |
|---|---|---|
| `aai540_silver` | `dim_county`, `census_acs_county`, `bls_qcew`, `bea_io_use`, `bea_io_make`, `hud_zip_county`, `events`, `ticketmaster_events`, `setlistfm_setlists`, `cdtfa_taxable_sales`, `census_state_tax_collections`, `tx_comptroller_county_allocations` | Exploration, custom joins, building new gold tables |
| `aai540_gold` | `model_training_matrix` | Model training (already joined + denormalized) |

Full schema, row counts, and source descriptions live in **[`docs/DATA_SOURCES.md`](DATA_SOURCES.md)**.

---

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `Could not load credentials` | Region not set or wrong | `aws configure set region us-east-2` |
| `AccessDenied` on S3 | Trying to access a non-project bucket | Your IAM only allows `jonno-lucas-steve-bucket`; that's by design |
| `TYPE_MISMATCH: smallint = varchar(1)` | Comparing an `INT` partition column to a quoted string | `year`, `quarter`, `month`, `table_year` are `INT` — no quotes (`year = 2022`, not `year = '2022'`) |
| `HIVE_PARTITION_SCHEMA_MISMATCH` | Stale partition cache | Re-run the crawler: `aws glue start-crawler --name aai540-silver-crawler` |
| Athena says "no query results location" | First-time setup | Athena Query Editor → settings → set output to `s3://jonno-lucas-steve-bucket/usd-aai540-group1/athena-results/` |

---

## See also

- [`docs/DATA_SOURCES.md`](DATA_SOURCES.md) — what every table means, row counts, gotchas
- [`sql/gold/model_training_matrix.sql`](../sql/gold/model_training_matrix.sql) — the CTAS that builds the Gold layer (re-run if Silver data updates)
- [`pipelines/repartition_for_silver.py`](../pipelines/repartition_for_silver.py) — how raw → Silver was assembled
- [`notebooks/explore.ipynb`](../notebooks/explore.ipynb) — pre-executed walk-through against the local DuckDB warehouse; SQL applies identically in Athena
