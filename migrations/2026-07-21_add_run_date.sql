-- Migration: add run_date TIMESTAMPTZ to runs table
-- Run this in the Supabase SQL editor.
--
-- run_date = the run's actual chronological timestamp, parsed from
-- run_data->'timeslip'->>'date' + ->>'time'. Replaces client-side sorting
-- on JSONB fields. NULL when the run has no parseable timeslip date.

-- 1. Add the column
ALTER TABLE runs ADD COLUMN IF NOT EXISTS run_date TIMESTAMPTZ;

-- 2. Backfill from timeslip date + time
UPDATE runs
SET run_date = sub.ts
FROM (
    SELECT id,
        CASE
            -- ISO date (YYYY-MM-DD)
            WHEN d ~ '^\d{4}-\d{2}-\d{2}$' THEN
                CASE
                    WHEN t ~* '^\d{1,2}:\d{2}:\d{2}\s*(AM|PM)$' THEN to_timestamp(d || ' ' || upper(t), 'YYYY-MM-DD HH12:MI:SS AM')
                    WHEN t ~* '^\d{1,2}:\d{2}\s*(AM|PM)$'        THEN to_timestamp(d || ' ' || upper(t), 'YYYY-MM-DD HH12:MI AM')
                    WHEN t ~  '^\d{1,2}:\d{2}:\d{2}$'            THEN to_timestamp(d || ' ' || t,        'YYYY-MM-DD HH24:MI:SS')
                    WHEN t ~  '^\d{1,2}:\d{2}$'                  THEN to_timestamp(d || ' ' || t,        'YYYY-MM-DD HH24:MI')
                    ELSE d::timestamptz
                END
            -- US date (M-D-YYYY or M/D/YYYY)
            WHEN d ~ '^\d{1,2}[-/]\d{1,2}[-/]\d{4}$' THEN
                CASE
                    WHEN t ~* '^\d{1,2}:\d{2}:\d{2}\s*(AM|PM)$' THEN to_timestamp(d || ' ' || upper(t), 'MM-DD-YYYY HH12:MI:SS AM')
                    WHEN t ~* '^\d{1,2}:\d{2}\s*(AM|PM)$'        THEN to_timestamp(d || ' ' || upper(t), 'MM-DD-YYYY HH12:MI AM')
                    WHEN t ~  '^\d{1,2}:\d{2}:\d{2}$'            THEN to_timestamp(d || ' ' || t,        'MM-DD-YYYY HH24:MI:SS')
                    WHEN t ~  '^\d{1,2}:\d{2}$'                  THEN to_timestamp(d || ' ' || t,        'MM-DD-YYYY HH24:MI')
                    ELSE to_timestamp(d, 'MM-DD-YYYY')
                END
            ELSE NULL
        END AS ts
    FROM (
        SELECT id,
               trim(coalesce(run_data->'timeslip'->>'date', '')) AS d,
               trim(coalesce(run_data->'timeslip'->>'time', '')) AS t
        FROM runs
    ) x
) sub
WHERE runs.id = sub.id
  AND runs.run_date IS NULL;

-- 3. Index for the per-user chronological queries
CREATE INDEX IF NOT EXISTS idx_runs_username_run_date
    ON runs (username, run_date DESC);

-- Verify: rows that could not be parsed (will sort last / be skipped in diffs)
-- SELECT csv_filename, run_data->'timeslip'->>'date' AS date,
--        run_data->'timeslip'->>'time' AS time
-- FROM runs WHERE run_date IS NULL;
