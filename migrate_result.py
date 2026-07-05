#!/usr/bin/env python3
"""
One-time migration: backfill Win/Loss/Bye result (and round number) for runs
that have a stored timeslip image but no result saved yet.

For each qualifying run:
  1. Looks up the user's saved car number from user_configs
  2. Downloads the timeslip image from Supabase Storage
  3. Calls Claude vision with the full Compulink-aware prompt
  4. Writes result → run_data["run_details"]["result"]
     and round_number → run_data["timeslip"]["round_number"]

Safe to re-run: only processes runs where run_details.result is currently empty.
Pass --force to re-scan runs that already have a result (useful if prompt improved).

Reads SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY from .env.
"""

import base64
import json
import os
import re
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

SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

missing = [n for n, v in [("SUPABASE_URL", SUPABASE_URL),
                           ("SUPABASE_SERVICE_KEY", SUPABASE_KEY),
                           ("ANTHROPIC_API_KEY", ANTHROPIC_KEY)] if not v]
if missing:
    sys.exit(f"ERROR: missing env vars: {', '.join(missing)}")

try:
    from supabase import create_client
except ImportError:
    sys.exit("ERROR: run `pip install supabase` first")
try:
    import anthropic
except ImportError:
    sys.exit("ERROR: run `pip install anthropic` first")

FORCE = "--force" in sys.argv

sb     = create_client(SUPABASE_URL, SUPABASE_KEY)
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

SLIP_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
             "png": "image/png",  "webp": "image/webp",
             "gif": "image/gif",  "heic": "image/heic"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def normalize_result(raw) -> str:
    if not raw:
        return ""
    s = str(raw).strip().upper()
    if s in ("W", "WIN", "WINNER", "1"):
        return "Win"
    if s in ("L", "LOSS", "LOSER", "LOSE", "0"):
        return "Loss"
    if s in ("B", "BYE", "BYE RUN"):
        return "Bye"
    return ""


def build_prompt(car_num: str) -> str:
    car_section = (
        f'The user\'s car number is "{car_num}". '
        f"Identify which lane (Left or Right) that car number appears in, then use the "
        f"win indicator to decide if the result is Win or Loss for this user."
        if car_num
        else "If only one car is shown, <<WIN means that car won, >>WIN means it lost."
    )
    return f"""You are reading a drag racing timeslip. Extract every field you can see.
Return a JSON object with these keys (use null for anything not visible):
{{
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "track_name": "full track name as printed",
  "track_location": "City, State (or City, Country)",
  "lane": "left or right",
  "car_number": "...",
  "dial_in": float,
  "reaction_time": float,
  "ft_60": float,
  "ft_330": float,
  "ft_660": float,
  "mph_660": float,
  "ft_1000": float,
  "ft_1320": float,
  "mph_1320": float,
  "temp_f": float or null,
  "baro_inhg": float or null,
  "humidity_pct": float or null,
  "wind": "speed and direction as printed" or null,
  "density_alt_ft": float or null,
  "result": "Win", "Loss", "Bye", or null,
  "round_number": "round label as printed e.g. E1, R1, Q2" or null,
  "issues": "any notes or issues" or null
}}

HOW TO DETERMINE THE RESULT:

Compulink timing system (most common): The winner's ET on the 1/4-mile line is followed
immediately by "<<WIN" (left-lane winner) or ">>WIN" (right-lane winner).
Example: "6.676  <<WIN" means the left-lane car won.
Example: "8.341  >>WIN" means the right-lane car won.
{car_section}

Other timing systems: look for "W" / "L", "WINNER" / "LOSER", checked WIN/LOSS box,
or "BYE" label. Map winner → "Win", loser → "Loss", bye → "Bye". Use null if nothing found.

ROUND NUMBER: Look for "Rnd # E1 9/10", "Round 1", "R2", "Elim 1", etc. near the bottom.
Extract just the short label (e.g. "E1", "R1", "Q2") into "round_number", or null.

Many timeslips print weather — extract temp, baro, humidity, wind, density altitude too.
Return only the JSON object. No markdown, no explanation."""


def scan_image(image_bytes: bytes, media_type: str, car_num: str) -> dict:
    b64  = base64.standard_b64encode(image_bytes).decode()
    resp = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=600,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": build_prompt(car_num)},
            ],
        }],
    )
    text = resp.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$",          "", text)
    return json.loads(text)


# ── Load all user car numbers from user_configs ───────────────────────────────
print("Loading user car numbers…")
user_car: dict[str, str] = {}
try:
    cfg_rows = sb.table("user_configs").select("username,config").execute().data or []
    for row in cfg_rows:
        cn = (row.get("config") or {}).get("car_number", "").strip()
        if cn:
            user_car[row["username"]] = cn
    print(f"  Car numbers found for {len(user_car)} user(s): "
          + ", ".join(f"{u}={n}" for u, n in user_car.items()))
except Exception as e:
    print(f"  Warning: could not load car numbers ({e})")


# ── Fetch all runs ────────────────────────────────────────────────────────────
print("\nFetching all runs from Supabase…")
PAGE, all_rows, offset = 1000, [], 0
while True:
    res = (sb.table("runs")
             .select("id,username,csv_filename,run_data")
             .range(offset, offset + PAGE - 1)
             .execute())
    batch = res.data or []
    all_rows.extend(batch)
    if len(batch) < PAGE:
        break
    offset += PAGE
print(f"  Total records fetched: {len(all_rows)}")

# ── Filter candidates ─────────────────────────────────────────────────────────
candidates, skip_no_img, skip_has_result, skip_no_data = [], 0, 0, 0
for row in all_rows:
    rd = row.get("run_data") or {}
    if not isinstance(rd, dict):
        skip_no_data += 1
        continue
    storage_key = rd.get("timeslip_storage_key")
    if not storage_key:
        skip_no_img += 1
        continue
    existing = (rd.get("run_details") or {}).get("result", "")
    if existing and not FORCE:
        skip_has_result += 1
        continue
    candidates.append({
        "id":          row["id"],
        "username":    row.get("username", "?"),
        "csv":         row.get("csv_filename", "?"),
        "storage_key": storage_key,
        "run_data":    rd,
        "car_num":     user_car.get(row.get("username", ""), ""),
        "had_result":  existing,
    })

print(f"\n  Has stored timeslip image: {len(candidates) + skip_has_result}")
print(f"  Already have a result:     {skip_has_result}"
      + (" (use --force to re-scan)" if skip_has_result and not FORCE else ""))
print(f"  Will scan:                 {len(candidates)}")
print(f"  No image stored:           {skip_no_img}")

if not candidates:
    print("\nNothing to do. Exiting.")
    sys.exit(0)

# ── Process ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*80}")
print(f"{'#':<4} {'User':<16} {'CSV':<26} {'Car':<8} {'Result':<8} {'Round':<6}  Status")
print(f"{'─'*80}")

updated = errors = no_result = 0

for i, c in enumerate(candidates, 1):
    # Download image
    try:
        raw = bytes(sb.storage.from_("timeslips").download(c["storage_key"]))
    except Exception as e:
        print(f"{i:<4} {c['username']:<16} {c['csv'][:25]:<26} {c['car_num']:<8} {'—':<8} {'—':<6}  ⚠️  download: {e}")
        errors += 1
        continue

    ext      = c["storage_key"].rsplit(".", 1)[-1].lower()
    mime     = SLIP_MIME.get(ext, "image/jpeg")

    # Vision scan
    try:
        parsed = scan_image(raw, mime, c["car_num"])
    except Exception as e:
        print(f"{i:<4} {c['username']:<16} {c['csv'][:25]:<26} {c['car_num']:<8} {'—':<8} {'—':<6}  ⚠️  scan: {e}")
        errors += 1
        continue

    result       = normalize_result(parsed.get("result"))
    round_number = (parsed.get("round_number") or "").strip() or None

    if not result:
        print(f"{i:<4} {c['username']:<16} {c['csv'][:25]:<26} {c['car_num']:<8} {'—':<8} {round_number or '—':<6}  (no result on slip)")
        no_result += 1
        # Still save round_number if found
        if not round_number:
            continue

    rd_updated = c["run_data"]

    # Write result → run_details.result
    if result:
        if not isinstance(rd_updated.get("run_details"), dict):
            rd_updated["run_details"] = {}
        rd_updated["run_details"]["result"] = result

    # Write round_number → timeslip.round_number
    if round_number:
        if isinstance(rd_updated.get("timeslip"), dict):
            rd_updated["timeslip"]["round_number"] = round_number

    try:
        sb.table("runs").update({"run_data": rd_updated}).eq("id", c["id"]).execute()
        changed = []
        if result:      changed.append(f"result={result}")
        if round_number: changed.append(f"round={round_number}")
        print(f"{i:<4} {c['username']:<16} {c['csv'][:25]:<26} {c['car_num']:<8} "
              f"{result or '—':<8} {round_number or '—':<6}  ✅ {', '.join(changed)}")
        updated += 1
    except Exception as e:
        print(f"{i:<4} {c['username']:<16} {c['csv'][:25]:<26} {'—':<8} {'—':<6}  ❌ update: {e}")
        errors += 1

print(f"{'─'*80}")
print(f"\nDone.  Updated: {updated}  |  No result on slip: {no_result}  |  Errors: {errors}")
if FORCE:
    print("(--force was used: existing results were re-scanned)")
