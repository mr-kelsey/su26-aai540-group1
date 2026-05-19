CREATE TABLE aai540_gold.model_training_matrix
WITH (
  format = 'PARQUET',
  parquet_compression = 'SNAPPY',
  external_location = 's3://jonno-lucas-steve-bucket/usd-aai540-group1/gold/model_training_matrix/',
  partitioned_by = ARRAY['year']
) AS
WITH events_agg AS (
  SELECT
    county_fips,
    year AS evt_year,
    CAST(((MONTH(CAST(event_date AS DATE)) - 1) / 3) + 1 AS SMALLINT) AS evt_quarter,
    COUNT(*) AS n_events,
    SUM(COALESCE(expected_attendance, 0)) AS total_expected_attendance,
    SUM(CASE WHEN source = 'setlistfm' THEN 1 ELSE 0 END) AS n_setlistfm,
    SUM(CASE WHEN source = 'ticketmaster' THEN 1 ELSE 0 END) AS n_ticketmaster
  FROM aai540_silver.events
  WHERE county_fips IS NOT NULL AND event_date IS NOT NULL
  GROUP BY county_fips, year, CAST(((MONTH(CAST(event_date AS DATE)) - 1) / 3) + 1 AS SMALLINT)
),
qcew_agg AS (
  SELECT
    county_fips,
    year AS qcew_year,
    quarter AS qcew_quarter,
    SUM(total_wages_usd) AS total_wages_usd,
    SUM(avg_employment) AS avg_employment,
    SUM(establishment_count) AS establishment_count
  FROM aai540_silver.bls_qcew
  WHERE naics_code = '10' AND ownership_code = 0
  GROUP BY county_fips, year, quarter
),
cdtfa_y AS (
  SELECT
    county_fips,
    table_year AS y_year,
    quarter AS y_quarter,
    SUM(taxable_sales_usd) AS taxable_sales_usd
  FROM aai540_silver.cdtfa_taxable_sales
  WHERE business_type = 'Total All Outlets'
  GROUP BY county_fips, table_year, quarter
)
SELECT
  c.county_fips,
  c.state_fips,
  c.county_name,
  c.latitude,
  c.longitude,
  c.land_area_sqmi,
  y.y_quarter AS quarter,
  CONCAT(CAST(y.y_year AS VARCHAR), 'Q', CAST(y.y_quarter AS VARCHAR)) AS period_id,
  y.taxable_sales_usd,
  COALESCE(e.n_events, 0) AS n_events,
  COALESCE(e.total_expected_attendance, 0) AS total_expected_attendance,
  COALESCE(e.n_setlistfm, 0) AS n_setlistfm,
  COALESCE(e.n_ticketmaster, 0) AS n_ticketmaster,
  q.total_wages_usd,
  q.avg_employment,
  q.establishment_count,
  a.population,
  a.median_household_income,
  a.median_age,
  a.bachelor_or_higher_pct,
  y.y_year AS year
FROM cdtfa_y y
JOIN aai540_silver.dim_county c ON c.county_fips = y.county_fips
LEFT JOIN events_agg e
  ON e.county_fips = y.county_fips AND e.evt_year = y.y_year AND e.evt_quarter = y.y_quarter
LEFT JOIN qcew_agg q
  ON q.county_fips = y.county_fips AND q.qcew_year = y.y_year AND q.qcew_quarter = y.y_quarter
LEFT JOIN aai540_silver.census_acs_county a
  ON a.county_fips = y.county_fips
WHERE c.state_fips = '06'
