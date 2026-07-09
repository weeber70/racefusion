#!/usr/bin/env python3
"""
recalc_da.py — Recalculate density altitude for all runs using the current
dry-air formula and update run_data["weather"]["density_alt_ft"] in Supabase.

Only touches the weather-derived DA. The timeslip-scanned DA (run_data["timeslip"]
["density_alt_ft"]) is left alone — that's ground truth printed by the track.

Logs: run ID | username | csv_filename | old DA | new DA | delta
"""

import os
import math
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit("ERROR: SUPABASE_URL or SUPABASE_SERVICE_KEY not set in .env")

from supabase import create_client
sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def calc_density_altitude(temp_f, humidity_pct, pressure_hpa):
    """
    Dry-air motorsports formula — exact copy of current app.py implementation.
    DA = 145442.16 × (1 − (ρ_dry / 1.225)^0.234969)
    """
    if any(v is None for v in [temp_f, humidity_pct, pressure_hpa]):
        return None
    T_c   = (temp_f - 32) * 5 / 9
    T_k   = T_c + 273.15
    P_pa  = pressure_hpa * 100.0
    RH    = humidity_pct / 100.0
    e_s   = 610.78 * math.exp(17.625 * T_c / (243.04 + T_c))
    e_pa  = RH * e_s
    P_dry = P_pa - e_pa
    rho   = P_dry / (287.058 * T_k)
    return 145442.16 * (1 - (rho / 1.225) ** 0.234969)


def main():
    print("=" * 112)
    print("RaceFusion — Density Altitude Recalculation Migration")
    print("Formula: dry-air  |  Target field: run_data.weather.density_alt_ft")
    print("=" * 112)
    print()

    print("Fetching all runs from Supabase...")
    rows = sb.table("runs").select("id, username, csv_filename, run_data").execute().data
    print(f"Found {len(rows)} run(s).\n")

    updated            = 0
    skipped_no_weather = 0
    skipped_no_inputs  = 0
    skipped_unchanged  = 0
    errors             = 0

    col = f"{'RUN ID':<38}  {'USER':<14}  {'CSV FILE':<28}  {'OLD DA':>8}  {'NEW DA':>8}  {'DELTA':>8}"
    print(col)
    print("-" * 112)

    for row in rows:
        run_id       = row.get("id", "?")
        username     = row.get("username", "?")
        csv_filename = row.get("csv_filename", "?")
        run_data     = row.get("run_data") or {}

        wx = run_data.get("weather") or {}
        if not wx:
            skipped_no_weather += 1
            continue

        temp_f       = wx.get("temperature_f")
        humidity_pct = wx.get("humidity_pct")
        pressure_hpa = wx.get("pressure_hpa")
        old_da_raw   = wx.get("density_alt_ft")

        if None in (temp_f, humidity_pct, pressure_hpa):
            skipped_no_inputs += 1
            continue

        new_da = calc_density_altitude(temp_f, humidity_pct, pressure_hpa)
        if new_da is None:
            skipped_no_inputs += 1
            continue

        new_da_int = round(new_da)
        old_da_int = round(float(old_da_raw)) if old_da_raw is not None else None

        # Skip if already correct (tolerance: 1 ft covers float rounding noise)
        if old_da_int is not None and abs(new_da_int - old_da_int) <= 1:
            skipped_unchanged += 1
            continue

        # Write updated weather block back
        wx["density_alt_ft"] = new_da_int
        run_data["weather"]  = wx

        try:
            sb.table("runs").update({"run_data": run_data}).eq("id", run_id).execute()

            old_str   = f"{old_da_int:,}" if old_da_int is not None else "null"
            delta     = new_da_int - (old_da_int if old_da_int is not None else 0)
            delta_str = f"{delta:+,}" if old_da_int is not None else "n/a"

            print(
                f"{str(run_id):<38}  {str(username):<14}  {str(csv_filename):<28}  "
                f"{old_str:>8}  {new_da_int:>8,}  {delta_str:>8}"
            )
            updated += 1

        except Exception as e:
            print(f"  !! ERROR updating run {run_id}: {e}")
            errors += 1

    print("-" * 112)
    print()
    print(f"  Updated:                    {updated}")
    print(f"  Skipped — no weather data:  {skipped_no_weather}")
    print(f"  Skipped — missing inputs:   {skipped_no_inputs}")
    print(f"  Skipped — already correct:  {skipped_unchanged}")
    if errors:
        print(f"  ERRORS:                     {errors}")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
