-- 003_events.sql
-- Event-side tables. Each event row carries both venue lat/lon AND a derived county_fips
-- (computed at clean-time from TIGER county polygons) so federal data joins cleanly.

CREATE TABLE IF NOT EXISTS events (
    event_id            VARCHAR     PRIMARY KEY,    -- source-prefixed id, e.g. 'tm_<id>'
    source              VARCHAR     NOT NULL,       -- ticketmaster|runsignup|setlistfm|wikidata|...
    category            VARCHAR     NOT NULL,       -- concert|marathon|sport|conference|festival|other
    event_name          VARCHAR     NOT NULL,
    event_date          DATE        NOT NULL,
    venue_name          VARCHAR,
    venue_address       VARCHAR,
    venue_city          VARCHAR,
    venue_state         CHAR(2),
    venue_zip           CHAR(5),
    venue_lat           DOUBLE,
    venue_lon           DOUBLE,
    county_fips         CHAR(5),                    -- derived
    period_id           VARCHAR,                    -- derived (year/quarter/month)
    expected_attendance INTEGER,
    ticket_min_usd      DOUBLE,
    ticket_max_usd      DOUBLE,
    raw_payload         JSON,                       -- source-specific fields preserved
    fetched_at          TIMESTAMP   NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_events_county      ON events (county_fips);
CREATE INDEX IF NOT EXISTS ix_events_period      ON events (period_id);
CREATE INDEX IF NOT EXISTS ix_events_category    ON events (category);
CREATE INDEX IF NOT EXISTS ix_events_date        ON events (event_date);
