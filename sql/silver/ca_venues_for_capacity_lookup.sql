-- Input query for pipelines/build_venue_capacities.py.
-- Lists every distinct CA venue seen in the setlistfm setlists, with
-- event counts so the curation pass can prioritize by impact.
--
-- Save the result to data/curated/_ca_venues_raw.csv before running the
-- builder. Recommended: run via the Athena Query Editor and download
-- the CSV directly, OR via:
--   aws athena start-query-execution \
--     --query-string file://sql/silver/ca_venues_for_capacity_lookup.sql \
--     --query-execution-context Database=aai540_silver
SELECT venue_name,
       venue_id,
       city_name,
       COUNT(*)                       AS n_events,
       COUNT(DISTINCT artist_name)    AS n_artists,
       MIN(event_date)                AS first_event,
       MAX(event_date)                AS last_event
FROM aai540_silver.setlistfm_setlists
WHERE state_code = 'CA'
GROUP BY venue_name, venue_id, city_name
ORDER BY n_events DESC
