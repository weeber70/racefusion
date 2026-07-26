#!/usr/bin/env python3
"""
backfill_weatherkit_hour.py — one-time correction for runs whose saved weather
came from the broken WeatherKit local-hour-as-UTC path (2026-07 hotfix).

Scope (hard rules):
  • Touches ONLY rows where run_data->'weather'->>'_source' == 'weatherkit'.
  • Rewrites ONLY run_data['weather'] (re-fetched at the correct hour, with
    density_alt_ft recomputed). Every other key — da_override, run_details,
    timeslip, car_snapshot, weather_date, weather_location — is left byte-
    for-byte as-is.
  • Dry-run by default: prints old → new side by side, writes NOTHING.
  • Pass --execute to actually write.

Usage (from the repo root, where .env lives):
    python backfill_weatherkit_hour.py             # dry-run, no writes
    python backfill_weatherkit_hour.py --execute   # write corrected weather
"""

import os
import sys
import json

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv() -> None:
    """Minimal .env reader (read-only; env vars already set take precedence)."""
    path = os.path.join(_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# ── Streamlit shim ────────────────────────────────────────────────────────────
# weather.py caches (track geocodes, tz offsets) in st.session_state, which
# doesn't exist outside `streamlit run`. A plain dict quacks well enough
# (.get / .setdefault / item assignment are all that's used).
import streamlit as st  # noqa: E402

st.session_state = {}  # type: ignore[assignment]

from database import _sb  # noqa: E402
from weather import fetch_weather, lookup_track, calc_density_altitude, \
    track_utc_offset_seconds  # noqa: E402


def _hpa_to_inhg(hpa):
    try:
        return float(hpa) * 0.02953
    except (TypeError, ValueError):
        return None


def _fmt(v, spec="{:.1f}", suffix=""):
    if v is None:
        return "—"
    try:
        return spec.format(float(v)) + suffix
    except (TypeError, ValueError):
        return str(v)


def main() -> int:
    execute = "--execute" in sys.argv[1:]
    mode = "EXECUTE (writing)" if execute else "DRY-RUN (no writes)"
    print(f"=== backfill_weatherkit_hour — {mode} ===\n")

    if _sb is None:
        print("ERROR: Supabase client not configured (SUPABASE_URL / "
              "SUPABASE_SERVICE_KEY missing). Run from the repo root with .env present.")
        return 1

    rows = (_sb.table("runs")
            .select("id,username,csv_filename,run_data")
            .execute().data) or []

    affected = [r for r in rows
                if ((r.get("run_data") or {}).get("weather") or {})
                .get("_source") == "weatherkit"]

    print(f"Scanned {len(rows)} runs; {len(affected)} with "
          f"weather._source == 'weatherkit' (expected 9).\n")

    fixed = skipped = 0
    for r in affected:
        rd   = r["run_data"] or {}
        slip = rd.get("timeslip") or {}
        name = f"{r.get('username','?')}/{r.get('csv_filename','?')}"

        date_str = slip.get("date")
        if not date_str:
            print(f"⏭️  {name}: no timeslip date — SKIPPED (needs manual review)")
            skipped += 1
            continue

        # Same hour-parse as the app (all stored times verified 24-hour HH:MM)
        hour = 12
        if slip.get("time"):
            try:
                hour = int(str(slip["time"]).split(":")[0])
            except Exception:
                hour = 12

        tk = lookup_track(slip.get("track_name", ""), slip.get("track_location", ""))
        if not tk:
            print(f"⏭️  {name}: couldn't resolve track "
                  f"'{slip.get('track_name','')}' — SKIPPED")
            skipped += 1
            continue

        old = rd.get("weather") or {}
        try:
            new = fetch_weather(tk["lat"], tk["lon"], date_str, hour)
        except Exception as e:
            print(f"⏭️  {name}: re-fetch failed ({e}) — SKIPPED, old data untouched")
            skipped += 1
            continue

        da_new = calc_density_altitude(new.get("temperature_f"), new.get("pressure_hpa"))
        if da_new is not None:
            new["density_alt_ft"] = round(da_new)

        off_h = track_utc_offset_seconds(tk["lat"], tk["lon"]) / 3600
        print(f"── {name}")
        print(f"   {date_str} {slip.get('time') or '(no time, using 12:00)'} local "
              f"(UTC{off_h:+.0f}) @ {tk.get('display_name','?')}")
        if rd.get("da_override") not in (None, ""):
            print(f"   da_override = {rd['da_override']} (PRESERVED — not touched)")
        print(f"   {'':14}{'OLD (wrong hour)':>18}{'NEW (correct hour)':>20}")
        print(f"   {'Temp °F':14}{_fmt(old.get('temperature_f')):>18}"
              f"{_fmt(new.get('temperature_f')):>20}")
        print(f"   {'Humidity %':14}{_fmt(old.get('humidity_pct'), '{:.0f}'):>18}"
              f"{_fmt(new.get('humidity_pct'), '{:.0f}'):>20}")
        print(f"   {'Baro inHg':14}{_fmt(_hpa_to_inhg(old.get('pressure_hpa')), '{:.2f}'):>18}"
              f"{_fmt(_hpa_to_inhg(new.get('pressure_hpa')), '{:.2f}'):>20}")
        print(f"   {'DA ft':14}{_fmt(old.get('density_alt_ft'), '{:.0f}'):>18}"
              f"{_fmt(new.get('density_alt_ft'), '{:.0f}'):>20}")
        print(f"   {'Source':14}{str(old.get('_source')):>18}{str(new.get('_source')):>20}")

        if execute:
            rd_out = dict(rd)          # shallow copy; ONLY 'weather' replaced
            rd_out["weather"] = new
            _sb.table("runs").update({"run_data": rd_out}).eq("id", r["id"]).execute()
            print("   ✅ WRITTEN")
            fixed += 1
        else:
            print("   (dry-run — nothing written)")
        print()

    print(f"=== Done: {len(affected)} affected, "
          f"{fixed if execute else 0} written, {skipped} skipped, "
          f"{'DRY-RUN — re-run with --execute to apply' if not execute else 'EXECUTED'} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
