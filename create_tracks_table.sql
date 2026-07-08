-- Run this once in the Supabase SQL editor (Dashboard → SQL editor → New query)
-- Creates the tracks cache table used by RaceFusion for automatic track location lookup.

CREATE TABLE IF NOT EXISTS tracks (
  id           SERIAL PRIMARY KEY,
  name_key     TEXT UNIQUE NOT NULL,   -- normalised lookup key (lower-case, collapsed whitespace)
  display_name TEXT,                   -- human-readable name returned by geocoder
  lat          DOUBLE PRECISION NOT NULL,
  lon          DOUBLE PRECISION NOT NULL,
  elev_ft      DOUBLE PRECISION,       -- track elevation in feet (from Open-Meteo)
  city_state   TEXT,                   -- "City, State" hint used during geocoding
  source       TEXT,                   -- e.g. "nominatim", "manual"
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Optional: index for fast look-ups by name_key (already covered by UNIQUE, but explicit)
CREATE INDEX IF NOT EXISTS tracks_name_key_idx ON tracks (name_key);
