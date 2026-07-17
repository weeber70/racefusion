"""
database.py — RaceFusion Supabase client and all database I/O functions.

Exports:
  _sb                 — Supabase client (or None if unconfigured)
  _get_secret()       — env-var / st.secrets helper
  ... plus every function that reads from or writes to Supabase.
"""

import os
import sys as _sys_rf
import json
import hashlib
import re
import secrets as _secrets
import uuid as _uuid
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

import streamlit as st

# ── Supabase library (optional import) ────────────────────────────────────────
try:
    from supabase import create_client as _sb_create_client
except ImportError:
    _sb_create_client = None  # type: ignore


# ── Secret helper ─────────────────────────────────────────────────────────────
def _get_secret(key: str, default: str = "") -> str:
    """Read a secret from env vars first, then st.secrets (if available)."""
    val = os.getenv(key, "")
    if val:
        return val
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


# ── Supabase client ───────────────────────────────────────────────────────────
_SUPABASE_URL = _get_secret("SUPABASE_URL")
_SUPABASE_KEY = _get_secret("SUPABASE_SERVICE_KEY")
_sb = (
    _sb_create_client(_SUPABASE_URL, _SUPABASE_KEY)
    if (_sb_create_client and _SUPABASE_URL and _SUPABASE_KEY)
    else None
)


# ── Session helpers (persist login across browser refresh) ────────────────────
_SESSION_TTL_HOURS = 24


def _create_session_token(username: str) -> str | None:
    """Upsert a session token into the sessions table; return the token."""
    if not _sb:
        print("[RF-SESSION] _sb is None — cannot create session token", file=_sys_rf.stderr, flush=True)
        return None
    tok = str(_uuid.uuid4())
    expires = (_dt.now(_tz.utc) + _td(hours=_SESSION_TTL_HOURS)).isoformat()
    print(f"[RF-SESSION] creating token for {username!r}  expires={expires}", file=_sys_rf.stderr, flush=True)
    try:
        _sb.table("sessions").upsert(
            {"username": username, "session_token": tok,
             "expires_at": expires, "last_seen": "now()"},
            on_conflict="username",
        ).execute()
        print(f"[RF-SESSION] token stored OK: {tok[:8]}…", file=_sys_rf.stderr, flush=True)
        return tok
    except Exception as _e:
        print(f"[RF-SESSION] ❌ token write FAILED: {_e}", file=_sys_rf.stderr, flush=True)
        return None


def _restore_session_from_token(token: str) -> str | None:
    """Validate a session token. Returns username if valid/not-expired, else None."""
    if not _sb:
        print("[RF-SESSION] _sb is None — cannot restore session", file=_sys_rf.stderr, flush=True)
        return None
    if not token:
        return None
    print(f"[RF-SESSION] validating token {token[:8]}…", file=_sys_rf.stderr, flush=True)
    try:
        rows = _sb.table("sessions").select("username,expires_at") \
                  .eq("session_token", token).execute().data
        print(f"[RF-SESSION] token lookup result: {rows}", file=_sys_rf.stderr, flush=True)
        if not rows:
            print("[RF-SESSION] token not found in sessions table", file=_sys_rf.stderr, flush=True)
            return None
        row = rows[0]
        exp_str = row.get("expires_at") or ""
        if exp_str:
            exp_dt = _dt.fromisoformat(exp_str.replace("Z", "+00:00"))
            if _dt.now(_tz.utc) > exp_dt:
                print(f"[RF-SESSION] token expired at {exp_str}", file=_sys_rf.stderr, flush=True)
                _sb.table("sessions").update({"session_token": None, "expires_at": None}) \
                   .eq("username", row["username"]).execute()
                return None
        _sb.table("sessions").update({"last_seen": "now()"}) \
           .eq("username", row["username"]).execute()
        print(f"[RF-SESSION] ✅ restored as {row['username']!r}", file=_sys_rf.stderr, flush=True)
        return row["username"]
    except Exception as _e:
        print(f"[RF-SESSION] ❌ token validation FAILED: {_e}", file=_sys_rf.stderr, flush=True)
        return None


def _delete_session_token(token: str):
    """Clear the session token on logout (keep the sessions row for admin last_seen)."""
    if not _sb or not token: return
    print(f"[RF-SESSION] deleting token {token[:8]}… on logout", file=_sys_rf.stderr, flush=True)
    try:
        _sb.table("sessions").update({"session_token": None, "expires_at": None}) \
           .eq("session_token", token).execute()
    except Exception as _e:
        print(f"[RF-SESSION] token delete failed: {_e}", file=_sys_rf.stderr, flush=True)


# ── Maintenance-mode helpers (Supabase-backed, admin-controlled) ──────────────
# No @st.cache_data — the read is lightweight and we need guaranteed freshness
# when the admin flips the toggle. Streamlit re-runs the full script on every
# interaction anyway, so each page action gets a fresh read.
def _read_maintenance_mode() -> bool:
    """Read maintenance_mode flag directly from site_settings (no cache)."""
    if not _sb:
        print("[RF-MAINT] _sb is None — defaulting to maintenance OFF", file=_sys_rf.stderr, flush=True)
        return False
    try:
        rows = _sb.table("site_settings").select("value") \
                  .eq("key", "maintenance_mode").execute().data
        result = bool(rows and rows[0].get("value") == "true")
        print(f"[RF-MAINT] read maintenance_mode={result!r}  raw={rows}", file=_sys_rf.stderr, flush=True)
        return result
    except Exception as _e:
        print(f"[RF-MAINT] ❌ read FAILED: {_e}  (site_settings table may not exist — run create_sessions_table.sql)", file=_sys_rf.stderr, flush=True)
        return False


def _write_maintenance_mode(enabled: bool):
    """Persist maintenance_mode to site_settings."""
    if not _sb:
        print("[RF-MAINT] _sb is None — cannot write", file=_sys_rf.stderr, flush=True)
        return
    print(f"[RF-MAINT] writing maintenance_mode={'true' if enabled else 'false'}", file=_sys_rf.stderr, flush=True)
    try:
        _sb.table("site_settings").upsert(
            {"key": "maintenance_mode", "value": "true" if enabled else "false"},
            on_conflict="key",
        ).execute()
        print("[RF-MAINT] ✅ write OK", file=_sys_rf.stderr, flush=True)
    except Exception as _e:
        print(f"[RF-MAINT] ❌ write FAILED: {_e}  (site_settings table may not exist — run create_sessions_table.sql)", file=_sys_rf.stderr, flush=True)


# ── Auth helpers ──────────────────────────────────────────────────────────────
def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = _secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return salt, dk.hex()


def _verify_password(password: str, salt: str, stored_hash: str) -> bool:
    _, computed = _hash_password(password, salt)
    return computed == stored_hash


def _check_user_exists(username: str) -> bool:
    if not _sb: return False
    try:
        rows = _sb.table("credentials").select("username").eq("username", username).execute().data
        return bool(rows)
    except Exception:
        return False


# ── Subscription helpers ──────────────────────────────────────────────────────
def _get_user_subscription(username: str) -> dict:
    """Read subscription_tier, trial_start_date, stripe_customer_id from credentials."""
    if not _sb: return {}
    try:
        rows = _sb.table("credentials") \
                  .select("subscription_tier,trial_start_date,stripe_customer_id") \
                  .eq("username", username).execute().data
        return rows[0] if rows else {}
    except Exception as _e:
        print(f"[RF-SUB] _get_user_subscription failed: {_e}", file=_sys_rf.stderr, flush=True)
        return {}


def _verify_login(username: str, password: str) -> bool:
    if not _sb: return False
    try:
        rows = _sb.table("credentials").select("salt,password_hash").eq("username", username).execute().data
        if not rows: return False
        return _verify_password(password, rows[0]["salt"], rows[0]["password_hash"])
    except Exception:
        return False


def _register_user(username: str, password: str, email: str = "") -> bool:
    if not _sb: return False
    salt, hsh = _hash_password(password)
    try:
        _sb.table("credentials").insert({
            "username":          username,
            "email":             email,
            "salt":              salt,
            "password_hash":     hsh,
            "subscription_tier": "trial",
            "trial_start_date":  _dt.now(_tz.utc).isoformat(),
        }).execute()
        return True
    except Exception:
        return False


# ── Run record persistence ────────────────────────────────────────────────────
def load_run(csv_name: str) -> dict:
    if not _sb: return {}
    username = st.session_state.get("rf_user", "")
    try:
        rows = _sb.table("runs").select("run_data").eq("username", username).eq("csv_filename", csv_name).execute().data
        return rows[0]["run_data"] if rows else {}
    except Exception:
        return {}


def extract_youtube_id(url: str) -> str | None:
    """Extract the 11-char video ID from common YouTube URL formats."""
    import re as _re
    url = url.strip()
    # youtu.be/ID
    _m = _re.search(r"youtu\.be/([A-Za-z0-9_\-]{11})", url)
    if _m: return _m.group(1)
    # youtube.com/shorts/ID
    _m = _re.search(r"/shorts/([A-Za-z0-9_\-]{11})", url)
    if _m: return _m.group(1)
    # youtube.com/watch?v=ID or /embed/ID
    _m = _re.search(r"(?:v=|/embed/)([A-Za-z0-9_\-]{11})", url)
    if _m: return _m.group(1)
    return None


def get_run_videos(run_id: str) -> list[dict]:
    """Return videos for a run ordered by display_order."""
    if not _sb or not run_id:
        return []
    try:
        return _sb.table("run_videos") \
                   .select("video_id, youtube_url, video_label, display_order") \
                   .eq("run_id", run_id) \
                   .order("display_order") \
                   .execute().data or []
    except Exception:
        return []


def add_run_video(run_id: str, username: str, youtube_url: str, video_label: str = "",
                  display_order: int | None = None) -> str | None:
    """Insert a video row; returns new video_id or None.

    Pass display_order explicitly when saving multiple videos in a batch to
    avoid read-after-write consistency issues with rapid successive calls.
    """
    if not _sb or not run_id or not youtube_url:
        return None
    try:
        if display_order is None:
            _existing = get_run_videos(run_id)
            display_order = max((v.get("display_order", 0) for v in _existing), default=0) + 1
        _row = _sb.table("run_videos").insert({
            "run_id":        run_id,
            "username":      username,
            "youtube_url":   youtube_url.strip(),
            "video_label":   video_label.strip(),
            "display_order": display_order,
        }).execute().data
        return _row[0]["video_id"] if _row else None
    except Exception as _e:
        st.warning(f"Could not save video: {_e}")
        return None


def delete_run_video(video_id: str) -> None:
    """Delete a video row by video_id."""
    if not _sb or not video_id:
        return
    try:
        _sb.table("run_videos").delete().eq("video_id", video_id).execute()
    except Exception:
        pass


def get_user_cars(username: str) -> list[dict]:
    """Return list of {car_id, car_name, default_car_number} for the user, oldest first."""
    if not _sb or not username:
        return []
    try:
        rows = _sb.table("cars") \
                   .select("car_id, car_name, default_car_number") \
                   .eq("username", username) \
                   .order("created_at") \
                   .execute().data
        return rows or []
    except Exception:
        return []


def create_car(username: str, car_name: str, default_car_number: str = "") -> str | None:
    """Insert a new car row and return the new car_id (UUID string), or None on failure."""
    if not _sb or not username or not car_name:
        return None
    try:
        row = _sb.table("cars").insert({
            "username":            username,
            "car_name":            car_name.strip(),
            "default_car_number":  default_car_number.strip(),
        }).execute().data
        return row[0]["car_id"] if row else None
    except Exception as _e:
        st.warning(f"Could not create car: {_e}")
        return None


def save_run(csv_name: str, record: dict, car_id: str | None = None):
    if not _sb: return
    username = st.session_state.get("rf_user", "")
    _extra = {"car_id": car_id} if car_id else {}
    try:
        existing = _sb.table("runs").select("id").eq("username", username).eq("csv_filename", csv_name).execute().data
        if existing:
            _sb.table("runs").update({"run_data": record, "updated_at": "now()", **_extra}).eq("username", username).eq("csv_filename", csv_name).execute()
        else:
            _sb.table("runs").insert({"username": username, "csv_filename": csv_name, "run_data": record, **_extra}).execute()
    except Exception as e:
        st.warning(f"Run save failed: {e}")


def save_run_csv(csv_name: str, data: bytes):
    """Persist RacePak CSV bytes to Supabase (stored as text in runs table)."""
    if not _sb: return
    username = st.session_state.get("rf_user", "")
    csv_text = data.decode("utf-8", errors="replace")
    try:
        existing = _sb.table("runs").select("id").eq("username", username).eq("csv_filename", csv_name).execute().data
        if existing:
            _sb.table("runs").update({"csv_content": csv_text, "updated_at": "now()"}).eq("username", username).eq("csv_filename", csv_name).execute()
        else:
            _sb.table("runs").insert({"username": username, "csv_filename": csv_name, "csv_content": csv_text, "run_data": {}}).execute()
    except Exception as e:
        st.warning(f"CSV save failed: {e}")


def load_run_csv_bytes(csv_name: str) -> bytes | None:
    """Load the raw CSV bytes for a saved run from Supabase."""
    if not _sb: return None
    username = st.session_state.get("rf_user", "")
    try:
        rows = _sb.table("runs").select("csv_content").eq("username", username).eq("csv_filename", csv_name).execute().data
        if rows and rows[0]["csv_content"]:
            return rows[0]["csv_content"].encode("utf-8")
    except Exception:
        pass
    return None


def _get_slip_storage_key(csv_name: str) -> str | None:
    """Return the Supabase Storage key for this run's timeslip, or None."""
    run_rec = load_run(csv_name)
    return run_rec.get("timeslip_storage_key")


def _delete_slip_from_storage(storage_key: str):
    if not _sb or not storage_key: return
    try:
        _sb.storage.from_("timeslips").remove([storage_key])
    except Exception:
        pass


def _run_label(filename: str, rec: dict) -> str:
    slip = rec.get("timeslip", {})
    date  = slip.get("date", "")
    track = slip.get("track_name", "") or slip.get("track_location", "")
    et    = slip.get("ft_1320", "")
    mph   = slip.get("mph_1320", "")
    parts = []
    if date:  parts.append(date)
    if track: parts.append(track)
    if et:    parts.append(f"{float(et):.3f}s")
    if mph:   parts.append(f"{float(mph):.2f} mph")
    if parts:
        return " · ".join(parts)
    # Friendly label for auto-named timeslip-only runs
    if filename.startswith("slip_") and filename.endswith(".run"):
        return "🎫 New run — upload timeslip"
    return filename


def list_saved_runs() -> list[dict]:
    """Return saved runs newest-first, each with label + filename + has_csv + record."""
    if not _sb: return []
    username = st.session_state.get("rf_user", "")
    try:
        rows = _sb.table("runs").select("csv_filename,run_data,created_at").eq("username", username).order("created_at", desc=True).execute().data
        try:
            _has_csv_set = {r["csv_filename"] for r in
                _sb.table("runs").select("csv_filename").eq("username", username).not_.is_("csv_content", "null").execute().data}
        except Exception:
            _has_csv_set = set()
    except Exception:
        return []
    out = []
    for r in rows:
        csv_name = r["csv_filename"]
        rec = r["run_data"] or {}
        has_csv = csv_name in _has_csv_set
        try:
            label = _run_label(csv_name, rec)
        except Exception:
            label = csv_name
        out.append({"filename": csv_name, "label": label, "record": rec, "has_csv": has_csv})
    return out


def _delete_run_files(csv_filename: str):
    """Delete all data associated with a run from Supabase."""
    if not _sb: return
    username = st.session_state.get("rf_user", "")
    _key = _get_slip_storage_key(csv_filename)
    if _key:
        _delete_slip_from_storage(_key)
    try:
        # Delete video rows first so they're never left orphaned if the run delete succeeds.
        _sb.table("run_videos").delete().eq("run_id", csv_filename).eq("username", username).execute()
    except Exception as e:
        st.warning(f"Video cleanup failed: {e}")
    try:
        _sb.table("runs").delete().eq("username", username).eq("csv_filename", csv_filename).execute()
    except Exception as e:
        st.warning(f"Delete failed: {e}")


def _rdp_load_run_history(username: str) -> list[dict]:
    """Return all runs for username that have both a valid ET and a DA."""
    # Lazy import to avoid circular dependency (weather.py imports _sb from database.py)
    from weather import calc_density_altitude
    if not _sb:
        return []
    try:
        rows = _sb.table("runs").select("id,csv_filename,run_data,created_at").eq("username", username).execute().data
    except Exception:
        return []
    results = []
    for row in rows:
        rec  = row.get("run_data") or {}
        slip = rec.get("timeslip", {}) or {}
        wx   = rec.get("weather",  {}) or {}
        try:
            et = float(slip.get("ft_1320") or 0)
        except (TypeError, ValueError):
            continue
        if et <= 0:
            continue
        # Always recalculate DA from raw weather values so the formula matches run view.
        # Never read stored density_alt_ft — it may have been computed by an older formula.
        da = calc_density_altitude(
            wx.get("temperature_f"),
            wx.get("pressure_hpa"),
            wx.get("humidity_pct"),
        )
        if da is None:
            continue
        results.append({
            "run_id":            str(row.get("id", "")),
            "csv_filename":      row.get("csv_filename", ""),
            "date":              slip.get("date") or row.get("created_at", "")[:10],
            "track":             slip.get("track_name") or slip.get("track_location") or "—",
            "et":                 et,
            "da":                 float(da),
            "predictor_exclude":  rec.get("predictor_exclude"),  # None / True / False
        })
    return results
