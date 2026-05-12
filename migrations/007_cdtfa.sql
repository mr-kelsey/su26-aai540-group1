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
