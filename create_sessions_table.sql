-- Run this once in the Supabase SQL editor (Dashboard → SQL editor → New query)
-- Extends the sessions table to support persistent login tokens (24-hour expiry)
-- and creates the site_settings table for admin-controlled flags.

-- ── sessions table ────────────────────────────────────────────────────────────
-- Create if not already present (may already exist with username + last_seen)
CREATE TABLE IF NOT EXISTS sessions (
  username      TEXT PRIMARY KEY,
  last_seen     TIMESTAMPTZ DEFAULT NOW()
);

-- Add session token columns if they don't exist yet
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_token TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS expires_at    TIMESTAMPTZ;

-- Index for fast token look-ups
CREATE INDEX IF NOT EXISTS sessions_token_idx ON sessions (session_token);

-- ── site_settings table ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS site_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed the maintenance_mode flag (off by default)
INSERT INTO site_settings (key, value)
VALUES ('maintenance_mode', 'false')
ON CONFLICT (key) DO NOTHING;
