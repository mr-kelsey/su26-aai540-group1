-- Ticketmaster staging table. Mirrors the events schema 1:1 so the source
-- can keep its normalize logic unchanged; build_events.py UNIONs this with
-- setlistfm_setlists (mapped via SQL) into the canonical `events` table.

CREATE TABLE IF NOT EXISTS ticketmaster_events (
    event_id            VARCHAR     PRIMARY KEY,
    source              VARCHAR     NOT NULL,
    category            VARCHAR     NOT NULL,
    event_name          VARCHAR     NOT NULL,
    event_date          VARCHAR,
    venue_name          VARCHAR,
    venue_address       VARCHAR,
    venue_city          VARCHAR,
    venue_state         CHAR(2),
    venue_zip           CHAR(5),
    venue_lat           DOUBLE,
    venue_lon           DOUBLE,
    county_fips         CHAR(5),
    period_id           VARCHAR,
    period_month        VARCHAR,
    expected_attendance INTEGER,
    ticket_min_usd      DOUBLE,
    ticket_max_usd      DOUBLE,
    raw_payload         VARCHAR,
    fetched_at          TIMESTAMP   NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tm_events_date     ON ticketmaster_events (event_date);
CREATE INDEX IF NOT EXISTS ix_tm_events_state    ON ticketmaster_events (venue_state);
