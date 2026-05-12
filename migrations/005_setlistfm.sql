CREATE TABLE IF NOT EXISTS setlistfm_setlists (
    setlist_id      VARCHAR PRIMARY KEY,
    artist_name     VARCHAR NOT NULL,
    artist_mbid     VARCHAR,
    event_date      DATE NOT NULL,
    venue_name      VARCHAR,
    venue_id        VARCHAR,
    city_name       VARCHAR,
    state_code      CHAR(2),
    country_code    CHAR(2),
    venue_lat       DOUBLE,
    venue_lon       DOUBLE,
    tour_name       VARCHAR,
    info_text       VARCHAR,
    n_songs         INTEGER,
    raw_payload     VARCHAR,
    fetched_at      TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_setlistfm_date     ON setlistfm_setlists (event_date);
CREATE INDEX IF NOT EXISTS ix_setlistfm_state    ON setlistfm_setlists (state_code);
CREATE INDEX IF NOT EXISTS ix_setlistfm_country  ON setlistfm_setlists (country_code);
CREATE INDEX IF NOT EXISTS ix_setlistfm_venue    ON setlistfm_setlists (venue_id);
