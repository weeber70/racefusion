-- Drop dead legacy columns on cars — never written by any code path;
-- all car data lives in the build_sheet JSONB column instead.
-- Verified 2026-07-31: no code reads/writes these, no index/constraint/RLS
-- policy references them (cars has only cars_pkey on car_id).
-- Run in BOTH environments: production RaceFusion and RacFusion_Test.

ALTER TABLE cars
  DROP COLUMN IF EXISTS car_weight_lbs,
  DROP COLUMN IF EXISTS engine_desc,
  DROP COLUMN IF EXISTS fuel_type,
  DROP COLUMN IF EXISTS gear_ratios,
  DROP COLUMN IF EXISTS rear_gear_ratio,
  DROP COLUMN IF EXISTS tire_size;
