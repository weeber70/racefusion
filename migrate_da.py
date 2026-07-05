#!/usr/bin/env python3
"""
One-time migration: recalculate density_altitude for all stored runs
using the corrected dry-air-only formula (motorsports / NHRA standard).

Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from .env in the same directory.
Safe to re-run: only updates records that have all three raw inputs
(temperature_f, humidity_pct, pressure_hpa) stored in weather data.
"""

import math
import os
import sys
from pathlib import Path

# ── Load .env ────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit("ERROR: SUPABASE_URL or SUPABASE_SERVICE_KEY not set.")

try:
    from supabase import create_client
except ImportError:
    sys.exit("ERROR: supabase-py not installed. Run: pip install supabase")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Corrected DA formula (dry-air-only, matches airdensityonline.com / NHRA) ─
def calc_density_altitude(temp_f, humidity_pct, pressure_hpa):
    """
    DA in feet using dry-air-only density (motorsports standard).
    Water vapor displaces oxygen-bearing dry air; excluding the vapor
    density term captures this effect and matches the drag racing standard.
    """
    if any(v is None for v in [temp_f, humidity_pct, pressure_hpa]):
        return None
    T_c   = (temp_f - 32) * 5 / 9
    T_k   = T_c + 273.15
    P_pa  = pressure_hpa * 100.0
    RH    = humidity_pct / 100.0
    e_s   = 610.78 * math.exp(17.27 * T_c / (T_c + 237.3))
    e_pa  = RH * e_s
    P_dry = P_pa - e_pa
    rho   = P_dry / (287.058 * T_k)
    rho_sl = 1.225
    return 145442.16 * (1 - (rho / rho_sl) ** 0.234969)


# ── Fetch all runs across all users ──────────────────────────────────────────
print("Fetching all runs from Supabase…")

PAGE = 1000
all_rows = []
offset = 0
while True:
    res = (
        sb.table("runs")
        .select("id,username,csv_filename,run_data")
        .range(offset, offset + PAGE - 1)
        .execute()
    )
    batch = res.data or []
    all_rows.extend(batch)
    if len(batch) < PAGE:
        break
    offset += PAGE

print(f"  Total run records fetched: {len(all_rows)}")


# ── Identify runs with stored weather.density_alt_ft ─────────────────────────
def get_weather(run_data):
    """Return the weather dict from a run_data blob, handling both layouts."""
    if not isinstance(run_data, dict):
        return None
    # run_data may be the record directly, or wrap slips under a key
    wx = run_data.get("weather")
    if isinstance(wx, dict):
        return wx
    # Some records nest under slips list — check first slip
    slips = run_data.get("slips") or run_data.get("runs") or []
    if slips and isinstance(slips, list):
        return slips[0].get("weather") if isinstance(slips[0], dict) else None
    return None


candidates = []
skipped_no_weather = 0
skipped_no_da = 0
skipped_no_inputs = 0

for row in all_rows:
    rd = row.get("run_data") or {}
    wx = get_weather(rd)
    if not wx:
        skipped_no_weather += 1
        continue
    old_da = wx.get("density_alt_ft")
    if old_da is None:
        skipped_no_da += 1
        continue
    temp    = wx.get("temperature_f")
    humidity = wx.get("humidity_pct")
    pressure = wx.get("pressure_hpa")
    if any(v is None for v in [temp, humidity, pressure]):
        skipped_no_inputs += 1
        continue
    candidates.append({
        "id":           row["id"],
        "username":     row.get("username", "?"),
        "csv_filename": row.get("csv_filename", "?"),
        "run_data":     rd,
        "wx":           wx,
        "old_da":       old_da,
        "temp":         temp,
        "humidity":     humidity,
        "pressure":     pressure,
    })

print(f"\n  Records with weather block:          {len(all_rows) - skipped_no_weather}")
print(f"  Records with stored density_alt_ft:  {len(candidates) + skipped_no_inputs}")
print(f"  Records with all 3 raw inputs:       {len(candidates)}")
print(f"  Skipped (no weather block):          {skipped_no_weather}")
print(f"  Skipped (no stored DA):              {skipped_no_da}")
print(f"  Skipped (missing raw inputs):        {skipped_no_inputs}")

if not candidates:
    print("\nNothing to migrate. Exiting.")
    sys.exit(0)


# ── Recalculate and update ────────────────────────────────────────────────────
print(f"\n{'─'*80}")
print(f"{'#':<4} {'User':<20} {'File':<30} {'Old DA':>10} {'New DA':>10} {'Δ':>8}")
print(f"{'─'*80}")

updated = 0
errors  = 0

for i, c in enumerate(candidates, 1):
    new_da = calc_density_altitude(c["temp"], c["humidity"], c["pressure"])
    if new_da is None:
        print(f"{i:<4} {c['username']:<20} {c['csv_filename']:<30} {'SKIP (calc returned None)':>28}")
        continue

    new_da_rounded = round(new_da)
    delta = new_da_rounded - round(c["old_da"])

    print(f"{i:<4} {c['username']:<20} {c['csv_filename'][:29]:<30} "
          f"{round(c['old_da']):>9,} ft {new_da_rounded:>9,} ft {delta:>+7,} ft")

    # Patch density_alt_ft in the weather dict inside run_data
    c["wx"]["density_alt_ft"] = new_da_rounded
    # run_data already contains the reference to wx (mutated in place above)

    try:
        sb.table("runs").update({"run_data": c["run_data"]}).eq("id", c["id"]).execute()
        updated += 1
    except Exception as e:
        print(f"     !! UPDATE FAILED for id={c['id']}: {e}")
        errors += 1

print(f"{'─'*80}")
print(f"\nDone. Updated: {updated}  Errors: {errors}  Skipped: {skipped_no_weather + skipped_no_da + skipped_no_inputs}")
