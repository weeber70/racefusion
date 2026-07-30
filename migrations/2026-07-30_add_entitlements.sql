-- ═══════════════════════════════════════════════════════════════════════════
-- À la carte add-ons: car/crew entitlement columns + crew membership table.
-- Run once in the PRODUCTION Supabase SQL editor (and in RacFusion_Test to
-- keep the schemas aligned). Purely additive — no data modified.
-- Pre-launch confirmed: zero paying subscribers, no grandfather plan needed.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE credentials
  ADD COLUMN IF NOT EXISTS car_slots  INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS crew_slots INTEGER NOT NULL DEFAULT 0;

-- One row per crew member; PK on member = a user belongs to at most one garage.
CREATE TABLE IF NOT EXISTS account_members (
  member_username TEXT PRIMARY KEY REFERENCES credentials(username) ON DELETE CASCADE,
  owner_username  TEXT NOT NULL REFERENCES credentials(username) ON DELETE CASCADE,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS account_members_owner_idx
  ON account_members (owner_username);
