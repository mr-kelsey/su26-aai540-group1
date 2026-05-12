-- Texas Comptroller — local sales tax allocations to counties (monthly).
-- Not raw taxable sales but the closest publicly available proxy:
-- the dollars each county receives back from Texas's 2% local sales tax,
-- which is proportional to the taxable sales activity in that county.
-- ~77K rows 2013-2026 via the data.texas.gov Socrata endpoint.

CREATE TABLE IF NOT EXISTS tx_comptroller_county_allocations (
    state_fips                     CHAR(2)   NOT NULL,            -- always '48'
    table_year                     SMALLINT  NOT NULL,
    month                          SMALLINT  NOT NULL,
    period_id                      VARCHAR   NOT NULL,            -- 'YYYY-MM'
    county_fips                    CHAR(5),                       -- derived from name lookup
    county_name                    VARCHAR   NOT NULL,            -- raw from API, e.g. 'Anderson'
    net_payment_usd                DOUBLE,                        -- the local-sales-tax allocation
    current_rate_pct               DOUBLE,                        -- county's allocation rate
    comparable_prior_year_usd      DOUBLE,
    pct_change_prior_year          DOUBLE,
    payments_to_date_usd           DOUBLE,
    previous_payments_to_date_usd  DOUBLE,
    pct_change_to_date             DOUBLE,
    fetched_at                     TIMESTAMP NOT NULL,
    PRIMARY KEY (period_id, county_name)
);

CREATE INDEX IF NOT EXISTS ix_tx_county ON tx_comptroller_county_allocations (county_fips);
CREATE INDEX IF NOT EXISTS ix_tx_period ON tx_comptroller_county_allocations (period_id);
