-- Table 1: the sites we monitor
CREATE TABLE IF NOT EXISTS monitors (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  url         TEXT NOT NULL UNIQUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Table 2: one row per check (visit) of a site
CREATE TABLE IF NOT EXISTS checks (
  id           BIGSERIAL PRIMARY KEY,
  monitor_id   INT NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
  checked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_up        BOOLEAN NOT NULL,
  status_code  INT,
  latency_ms   INT,
  error        TEXT
);

-- Index to speed up latest check for this site lookups
CREATE INDEX IF NOT EXISTS checks_monitor_time
  ON checks (monitor_id, checked_at DESC);
