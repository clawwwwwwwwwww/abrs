-- Portable schema. Works unmodified on SQLite and Postgres.
-- Defaults that SQLite and Postgres both support; created_at/updated_at are
-- always set explicitly in Python so we don't depend on `datetime('now')`/`NOW()`.

CREATE TABLE IF NOT EXISTS appointments (
  id                  TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  phone               TEXT NOT NULL,
  service             TEXT NOT NULL,
  datetime            TEXT NOT NULL,
  duration_minutes    INTEGER NOT NULL,
  status              TEXT NOT NULL DEFAULT 'booked',
  calendar_event_id   TEXT,
  reminder_24h_sent   INTEGER NOT NULL DEFAULT 0,
  reminder_2h_sent    INTEGER NOT NULL DEFAULT 0,
  followup_sent       INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_appt_datetime    ON appointments(datetime);
CREATE INDEX IF NOT EXISTS idx_appt_phone       ON appointments(phone);
CREATE INDEX IF NOT EXISTS idx_appt_status_dt   ON appointments(status, datetime);

CREATE TABLE IF NOT EXISTS users (
  phone      TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  phone      TEXT PRIMARY KEY,
  state      TEXT NOT NULL,
  data       TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
