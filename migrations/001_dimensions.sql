-- 001_dimensions.sql
-- Geographic and industry dimensions used as join keys across all source tables.
-- Standard ANSI SQL where possible so the same DDL works against both DuckDB and Postgres.

CREATE TABLE IF NOT EXISTS dim_county (
    county_fips     CHAR(5)     PRIMARY KEY,    -- 2-digit state + 3-digit county
    state_fips      CHAR(2)     NOT NULL,
    state_abbr      CHAR(2)     NOT NULL,
    county_name     VARCHAR     NOT NULL,
    cbsa_code       CHAR(5),                    -- nullable: not all counties are in a CBSA
    cbsa_name       VARCHAR,
    latitude        DOUBLE,
    longitude       DOUBLE,
    population      BIGINT,
    land_area_sqmi  DOUBLE,
    source          VARCHAR     NOT NULL,       -- e.g. 'tiger_2023'
    fetched_at      TIMESTAMP   NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_naics (
    naics_code      VARCHAR     PRIMARY KEY,    -- variable length 2..6
    naics_level     SMALLINT    NOT NULL,
    title           VARCHAR     NOT NULL,
    parent_code     VARCHAR,
    source          VARCHAR     NOT NULL,
    fetched_at      TIMESTAMP   NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_time (
    period_id       VARCHAR     PRIMARY KEY,    -- e.g. '2023Q3', '2023-08'
    year            SMALLINT    NOT NULL,
    quarter         SMALLINT,
    month           SMALLINT,
    is_quarter      BOOLEAN     NOT NULL,
    is_month        BOOLEAN     NOT NULL
);
