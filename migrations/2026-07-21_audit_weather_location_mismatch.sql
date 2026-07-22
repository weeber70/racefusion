-- AUDIT: runs whose stored weather was fetched for a different location
-- than the run's own timeslip track (the silent sidebar-fallback bug).
--
-- Flags any run with weather where the weather_location label does not
-- contain the first word of the timeslip's track name. First-word matching
-- is deliberately loose — review the list by eye; a run flagged here most
-- likely had its Temp/Humidity/Baro/DA fetched at the wrong coordinates.
--
-- Runs with a DA override still show the correct DA headline, but their
-- underlying weather fields are wrong-track data.

SELECT username,
       csv_filename,
       run_data->'timeslip'->>'date'  AS run_date,
       coalesce(run_data->'timeslip'->>'track_name',
                run_data->'timeslip'->>'track_location') AS slip_track,
       run_data->>'weather_location'  AS weather_fetched_for,
       (run_data ? 'da_override')     AS has_da_override
FROM runs
WHERE run_data ? 'weather'
  AND coalesce(run_data->'timeslip'->>'track_name',
               run_data->'timeslip'->>'track_location') IS NOT NULL
  AND (
        run_data->>'weather_location' IS NULL
        OR run_data->>'weather_location' NOT ILIKE
           '%' || split_part(
                    coalesce(run_data->'timeslip'->>'track_name',
                             run_data->'timeslip'->>'track_location'),
                    ' ', 1) || '%'
      )
ORDER BY username, run_date;

-- To repair a flagged run: open it in Run Analysis and use the new
-- "Retry weather lookup" flow (added 2026-07-21), or clear its weather so
-- it re-fetches from the run's own track:
--   UPDATE runs SET run_data = run_data - 'weather' - 'weather_location' - 'weather_date'
--   WHERE csv_filename = '<flagged run>' AND username = '<user>';
