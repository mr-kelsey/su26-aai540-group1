-- 002_economic_backbone.sql
-- Tables landing federal economic data sources (BLS QCEW, BEA I-O, Census ACS).

-- BLS Quarterly Census of Employment & Wages
CREATE TABLE IF NOT EXISTS bls_qcew (
    county_fips         CHAR(5)     NOT NULL,
    naics_code          VARCHAR     NOT NULL,
    period_id           VARCHAR     NOT NULL,
    year                SMALLINT    NOT NULL,
    quarter             SMALLINT    NOT NULL,
    ownership_code      SMALLINT    NOT NULL,    -- 0=total,5=private,1-3=govt
    establishment_count INTEGER,
    avg_employment      INTEGER,
    total_wages_usd     BIGINT,
    avg_weekly_wage_usd INTEGER,
    fetched_at          TIMESTAMP   NOT NULL,
    PRIMARY KEY (county_fips, naics_code, period_id, ownership_code)
);

-- BEA Input-Output Use table (industry x commodity)
CREATE TABLE IF NOT EXISTS bea_io_use (
    table_year      SMALLINT    NOT NULL,
    industry_code   VARCHAR     NOT NULL,
    commodity_code  VARCHAR     NOT NULL,
    value_millions  DOUBLE,
    fetched_at      TIMESTAMP   NOT NULL,
    PRIMARY KEY (table_year, industry_code, commodity_code)
);

-- BEA Input-Output Make table (industry x commodity output)
CREATE TABLE IF NOT EXISTS bea_io_make (
    table_year      SMALLINT    NOT NULL,
    industry_code   VARCHAR     NOT NULL,
    commodity_code  VARCHAR     NOT NULL,
    value_millions  DOUBLE,
    fetched_at      TIMESTAMP   NOT NULL,
    PRIMARY KEY (table_year, industry_code, commodity_code)
);

-- Census ACS 5-year detailed county tables (subset)
CREATE TABLE IF NOT EXISTS census_acs_county (
    county_fips             CHAR(5)     NOT NULL,
    acs_5yr_end_year        SMALLINT    NOT NULL,
    population              INTEGER,
    median_household_income INTEGER,
    median_age              DOUBLE,
    bachelor_or_higher_pct  DOUBLE,
    fetched_at              TIMESTAMP   NOT NULL,
    PRIMARY KEY (county_fips, acs_5yr_end_year)
);

-- HUD ZIP-to-County crosswalk (residential weights)
CREATE TABLE IF NOT EXISTS hud_zip_county (
    zip                 CHAR(5)     NOT NULL,
    county_fips         CHAR(5)     NOT NULL,
    quarter             VARCHAR     NOT NULL,    -- e.g. '2024Q1'
    res_ratio           DOUBLE,
    bus_ratio           DOUBLE,
    oth_ratio           DOUBLE,
    tot_ratio           DOUBLE,
    fetched_at          TIMESTAMP   NOT NULL,
    PRIMARY KEY (zip, county_fips, quarter)
);
