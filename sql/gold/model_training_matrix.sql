CREATE TABLE aai540_gold.model_training_matrix
WITH (
  format = 'PARQUET',
  parquet_compression = 'SNAPPY',
  external_location = 's3://jonno-lucas-steve-bucket/usd-aai540-group1/gold/model_training_matrix/',
  partitioned_by = ARRAY['year']
) AS
-- Sell-through assumption: % of venue capacity that actually shows up at
-- a typical concert. 0.80 is a conservative industry rule-of-thumb for
-- mid-to-major venues. The Bayesian model can later learn this as a latent
-- parameter, but a fixed prior is fine for v1.
WITH events_agg AS (
  SELECT
    e.county_fips,
    e.year AS evt_year,
    CAST(((MONTH(CAST(e.event_date AS DATE)) - 1) / 3) + 1 AS SMALLINT) AS evt_quarter,
    COUNT(*) AS n_events,
    SUM(COALESCE(e.expected_attendance, 0)) AS total_expected_attendance,
    -- Derived attendance: ticketmaster -> use actual; setlistfm -> capacity * sell_through
    SUM(
      CASE
        WHEN e.source = 'ticketmaster' AND e.expected_attendance > 0
          THEN CAST(e.expected_attendance AS BIGINT)
        WHEN e.source = 'setlistfm' AND vc.capacity IS NOT NULL
          THEN CAST(vc.capacity * 0.80 AS BIGINT)
        ELSE 0
      END
    ) AS total_est_attendance,
    SUM(CASE WHEN e.source = 'setlistfm' THEN 1 ELSE 0 END) AS n_setlistfm,
    SUM(CASE WHEN e.source = 'ticketmaster' THEN 1 ELSE 0 END) AS n_ticketmaster
  FROM aai540_silver.events e
  -- Bridge to venue_capacities via setlistfm_setlists.setlist_id.
  -- events.event_id for setlistfm rows is 'sfm_' || setlist_id.
  LEFT JOIN aai540_silver.setlistfm_setlists s
    ON e.source = 'setlistfm' AND e.event_id = 'sfm_' || s.setlist_id
  LEFT JOIN aai540_silver.venue_capacities vc
    ON vc.venue_id = s.venue_id
  WHERE e.county_fips IS NOT NULL AND e.event_date IS NOT NULL
  GROUP BY e.county_fips, e.year,
    CAST(((MONTH(CAST(e.event_date AS DATE)) - 1) / 3) + 1 AS SMALLINT)
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
  COALESCE(e.total_est_attendance, 0) AS total_est_attendance,
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
