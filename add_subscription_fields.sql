-- RaceFusion: Add subscription fields to credentials table
-- Run this once in your Supabase SQL editor.

ALTER TABLE credentials
  ADD COLUMN IF NOT EXISTS subscription_tier    TEXT        NOT NULL DEFAULT 'trial',
  ADD COLUMN IF NOT EXISTS trial_start_date     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS stripe_customer_id   TEXT;

-- Backfill existing users: give everyone a fresh 30-day trial from today.
-- Remove this UPDATE if you want existing users to be immediately expired.
UPDATE credentials
SET trial_start_date = NOW()
WHERE trial_start_date IS NULL OR trial_start_date = '1970-01-01T00:00:00+00:00';

-- Verify
SELECT username, subscription_tier, trial_start_date, stripe_customer_id
FROM credentials
ORDER BY username;
