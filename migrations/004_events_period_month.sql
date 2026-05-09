-- 004_events_period_month.sql
-- Adds the period_month column to events for monthly joins (state DOR, county-month panel).
-- Quarterly joins continue to use the existing period_id column.

ALTER TABLE events ADD COLUMN period_month VARCHAR;

CREATE INDEX IF NOT EXISTS ix_events_period_month ON events (period_month);
