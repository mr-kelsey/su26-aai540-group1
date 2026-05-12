-- Census Bureau State Government Tax Collections (annual, state-level).
-- Universal Y-target floor: covers all 50 states + DC for 2016-2025
-- (data publication starts 2016 by Census's own coverage). Sales-tax-free
-- states (OR, AK, MT, NH, DE) appear with 0 in T09 General Sales Tax.

CREATE TABLE IF NOT EXISTS census_state_tax_collections (
    table_year             SMALLINT NOT NULL,    -- e.g. 2023
    period_id              VARCHAR  NOT NULL,    -- 'YYYY-annual', e.g. '2023-annual'
    state_fips             CHAR(2)  NOT NULL,    -- '06' for CA, '48' for TX, ...
    state_name             VARCHAR  NOT NULL,
    govtype                VARCHAR,              -- typically '002'
    agg_desc               VARCHAR,              -- 'STC001', 'STC002', ... aggregation level id
    item_code              VARCHAR  NOT NULL,    -- 'T09' = General Sales Tax; 'AGG' for aggregate roll-ups
    amount_thousands_usd   BIGINT,               -- AMOUNT field; multiply by 1000 for dollars
    fetched_at             TIMESTAMP NOT NULL,
    PRIMARY KEY (table_year, state_fips, agg_desc, item_code)
);

CREATE INDEX IF NOT EXISTS ix_cst_state ON census_state_tax_collections (state_fips);
CREATE INDEX IF NOT EXISTS ix_cst_year  ON census_state_tax_collections (table_year);
CREATE INDEX IF NOT EXISTS ix_cst_item  ON census_state_tax_collections (item_code);
