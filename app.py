"""
RaceFusion - RacePak Data Dashboard
Reads RacePak CSV exports and displays all channels in an interactive dashboard.
Includes timeslip scanning (Claude vision) and historical weather (Apple WeatherKit).
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import math
import os
import base64
import re
import requests
from pathlib import Path
import stripe
from dotenv import load_dotenv
from pathlib import Path as _Path
load_dotenv()                                  # picks up .env in cwd (Streamlit Cloud / local)
load_dotenv(_Path(__file__).parent / ".env")  # also try next to app.py for local dev

# ── Module imports (Phase 1 extraction) ──────────────────────────────────────
from styles import apply_login_styles, apply_maintenance_styles, apply_all_styles, PLOTLY_DARK
from database import (
    _sb, _sb_create_client, _SUPABASE_URL, _SUPABASE_KEY, _get_secret,
    _create_session_token, _restore_session_from_token, _delete_session_token,
    _read_maintenance_mode, _write_maintenance_mode,
    _hash_password, _verify_password, _check_user_exists,
    _get_user_subscription, _verify_login, _register_user,
    load_run, extract_youtube_id, get_run_videos, add_run_video, delete_run_video,
    get_user_cars, create_car, save_run, save_run_csv, load_run_csv_bytes,
    _get_slip_storage_key, _delete_slip_from_storage, _run_label,
    list_saved_runs, _delete_run_files, _rdp_load_run_history,
)
from config import load_config, save_config
from weather import (
    geocode, lookup_track, _track_key, _TRACK_OVERRIDES,
    fetch_weather, fetch_weather_rdp, fetch_metar,
    calc_density_altitude, sea_level_to_station_pressure, wind_dir_label,
    _get_weatherkit_token, _fetch_weatherkit_current, _haversine_km,
)
from charts import make_overlay_chart, TRACE_COLORS, RPM_CHANNEL_NAMES
from timeslip import correct_image_orientation, scan_timeslip, _normalize_slip_result

# ── Phase 2 page module imports ───────────────────────────────────────────────
from admin import show_admin_panel
from season_summary import show_season_summary
from race_day_predictor import show_race_day_predictor
from run_manager import show_run_manager
from instructions import show_instructions
from car_profile import show_car_profile
from run_comparison import show_run_comparison
from run_analysis import (
    show_run_analysis,
    load_racepak_csv, get_time_col, check_alerts, detect_shift_points, calc_rwhp,
)

import io as _io

def _has_feature(feature: str) -> bool:
    """Return True if the current user's tier includes this feature.

    Paid tier always takes priority over trial status — a Racer subscriber
    is gated on Pro features even if their trial window hasn't fully expired.
    """
    tier         = st.session_state.get("sub_tier", "trial")
    trial_active = st.session_state.get("trial_active", False)
    _pro_only    = {"csv_upload", "channel_charts", "ai_tuner"}

    # Paid tiers evaluated first — tier gates override trial flag.
    if tier == "crew_chief":
        return True
    if tier == "pro":
        return True
    if tier == "racer":
        return feature not in _pro_only  # racer blocked from pro-only features

    # No paid tier — fall back to trial status.
    if trial_active:
        return True  # active trial gets everything

    # Expired trial — block pro-only features.
    return feature not in _pro_only

# ── (styles extracted to styles.py) ──────────────────────────────────────────

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="RaceFusion",
    page_icon="🏁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Logo loader ───────────────────────────────────────────────────────────────
def _load_logo_b64(filename: str = "RaceFusion-Logo-V3.png") -> str | None:
    """Return base64 data-URI for the logo if the file exists next to app.py."""
    p = Path(__file__).parent / filename
    if p.exists():
        ext = p.suffix.lstrip(".").lower()
        mime = {"png": "image/png", "jpg": "image/jpeg",
                "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
    return None

_LOGO_SRC = _load_logo_b64("RaceFusion-Logo-V3.png")

_FOOTER_HTML = (
    "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
    "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
    "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
    "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
    "text-decoration:none;'>Contact Us</a></div>"
)

# ── Paths ─────────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent

# ── Supabase credential validation (client is initialized in database.py) ─────
if _sb_create_client and (not _SUPABASE_URL or not _SUPABASE_KEY):
    st.error(
        "❌ Supabase credentials missing. "
        "Check that SUPABASE_URL and SUPABASE_SERVICE_KEY are set in your secrets configuration."
    )
    st.stop()

# ── Stripe ────────────────────────────────────────────────────────────────────
_stripe_mod = stripe
stripe.api_key = _get_secret("STRIPE_SECRET_KEY")

_STRIPE_PRICE_RACER      = _get_secret("STRIPE_PRICE_RACER")
_STRIPE_PRICE_PRO        = _get_secret("STRIPE_PRICE_PRO")
_STRIPE_PRICE_CREW_CHIEF = _get_secret("STRIPE_PRICE_CREW_CHIEF")
_STRIPE_PUB_KEY          = _get_secret("STRIPE_PUBLISHABLE_KEY")
_STRIPE_TIER_MAP: dict[str, str] = {
    _STRIPE_PRICE_RACER:      "racer",
    _STRIPE_PRICE_PRO:        "pro",
    _STRIPE_PRICE_CREW_CHIEF: "crew_chief",
}
# Remove empty-string key so missing env vars don't match every price_id
_STRIPE_TIER_MAP = {k: v for k, v in _STRIPE_TIER_MAP.items() if k}

# ── App-wide constants ────────────────────────────────────────────────────────
_ADMIN_USER = "weeber70"

import sys as _sys_rf  # available throughout the module for debug prints
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

# ── Subscription helpers (Stripe — stays in app.py) ──────────────────────────

def _get_tier_from_price_id(price_id: str) -> str:
    return _STRIPE_TIER_MAP.get(price_id, "racer")

def _check_stripe_subscription(email: str, username: str) -> str | None:
    """
    Poll Stripe for an active subscription.
    Prefers the stored stripe_customer_id; falls back to email lookup.
    Returns tier name ('racer'|'pro'|'crew_chief') if active, else None.
    Side-effects: updates credentials table with stripe_customer_id and tier.
    """
    print(f"[RF-STRIPE] _check_stripe_subscription called: username={username!r} email={email!r}",
          file=_sys_rf.stderr, flush=True)
    if not _stripe_mod or not _stripe_mod.api_key:
        print("[RF-STRIPE] skipping — stripe not configured", file=_sys_rf.stderr, flush=True)
        return None

    try:
        # 1. Try to get the stored customer ID from Supabase first.
        _stored_cust_id: str | None = None
        if _sb and username:
            try:
                _cred = _sb.table("credentials").select("stripe_customer_id") \
                            .eq("username", username).execute().data
                if _cred:
                    _stored_cust_id = _cred[0].get("stripe_customer_id") or None
            except Exception as _ce:
                print(f"[RF-STRIPE] cred lookup error: {_ce}", file=_sys_rf.stderr, flush=True)

        print(f"[RF-STRIPE] stored_cust_id={_stored_cust_id!r}", file=_sys_rf.stderr, flush=True)

        # 2. Resolve the Stripe Customer object.
        cust = None
        if _stored_cust_id:
            try:
                cust = _stripe_mod.Customer.retrieve(_stored_cust_id)
            except Exception as _re:
                print(f"[RF-STRIPE] customer retrieve failed: {_re}", file=_sys_rf.stderr, flush=True)
        if cust is None and email:
            _clist = _stripe_mod.Customer.list(email=email, limit=1)
            if _clist.data:
                cust = _clist.data[0]
                print(f"[RF-STRIPE] found customer by email: {cust.id}", file=_sys_rf.stderr, flush=True)
            else:
                print(f"[RF-STRIPE] no customer found for email={email!r}", file=_sys_rf.stderr, flush=True)
        if cust is None:
            return None

        # 3. List active subscriptions.
        subs = _stripe_mod.Subscription.list(customer=cust.id, status="active", limit=5)
        print(f"[RF-STRIPE] active subs count={len(subs.data)} for customer={cust.id}",
              file=_sys_rf.stderr, flush=True)
        if not subs.data:
            return None

        price_id = subs.data[0].items.data[0].price.id
        tier = _get_tier_from_price_id(price_id)
        print(f"[RF-STRIPE] price_id={price_id!r} → tier={tier!r}", file=_sys_rf.stderr, flush=True)

        # 4. Persist customer ID and tier back to Supabase.
        if _sb:
            try:
                _sb.table("credentials").update({
                    "stripe_customer_id": cust.id,
                    "subscription_tier":  tier,
                }).eq("username", username).execute()
                print(f"[RF-STRIPE] updated credentials: tier={tier!r} cust={cust.id}",
                      file=_sys_rf.stderr, flush=True)
            except Exception as _ue:
                print(f"[RF-STRIPE] credentials update failed: {_ue}", file=_sys_rf.stderr, flush=True)

        return tier

    except Exception as _e:
        print(f"[RF-STRIPE] subscription check failed: {_e}", file=_sys_rf.stderr, flush=True)
        return None

def _is_trial_active(trial_start_date) -> bool:
    """Return True if the user is still within their 30-day trial."""
    if not trial_start_date:
        return True  # no date recorded → just registered, treat as active
    try:
        if isinstance(trial_start_date, str):
            ts = _dt.fromisoformat(trial_start_date.replace("Z", "+00:00"))
        else:
            ts = trial_start_date
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        return (_dt.now(_tz.utc) - ts).days <= 30
    except Exception:
        return True  # parse failure → be permissive

def _create_stripe_checkout(price_id: str, session_token: str,
                             username: str = "", email: str = "") -> str | None:
    """Create a Stripe Checkout Session and return the redirect URL.

    Reuses an existing Stripe customer when stripe_customer_id is stored in
    credentials; otherwise pre-fills the email for a new customer.
    """
    if not _stripe_mod or not price_id:
        return None
    try:
        # Look up existing Stripe customer ID so we don't create duplicates.
        _existing_cust_id: str | None = None
        if _sb and username:
            try:
                _cred_rows = _sb.table("credentials").select("stripe_customer_id") \
                               .eq("username", username).execute().data
                if _cred_rows:
                    _existing_cust_id = _cred_rows[0].get("stripe_customer_id") or None
            except Exception:
                pass

        base = _get_secret("APP_BASE_URL", "http://localhost:8501").rstrip("/")
        _sess_params: dict = {
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": f"{base}/?session={session_token}&p=upgrade&success=true&cs_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url":  f"{base}/?p=upgrade",
        }
        if _existing_cust_id:
            _sess_params["customer"] = _existing_cust_id
        elif email:
            _sess_params["customer_email"] = email

        checkout = _stripe_mod.checkout.Session.create(**_sess_params)
        return checkout.url
    except Exception as _e:
        print(f"[RF-STRIPE] checkout session creation failed: {_e}", file=_sys_rf.stderr, flush=True)
        st.session_state["_stripe_last_error"] = str(_e)
        return None

# ── Auth gate — must resolve before any other UI ──────────────────────────────
if "rf_user" not in st.session_state:
    st.session_state["rf_user"] = None

# Debug: show what's in URL params on every load
print(
    f"[RF-AUTH] page load  rf_user={st.session_state['rf_user']!r}  "
    f"query_params={dict(st.query_params)}",
    file=_sys_rf.stderr, flush=True,
)

# Try to restore login from session token stored in URL
if st.session_state["rf_user"] is None:
    _sess_param = st.query_params.get("session", "")
    print(f"[RF-AUTH] rf_user is None — session param={_sess_param!r}", file=_sys_rf.stderr, flush=True)
    if _sess_param:
        _restored_user = _restore_session_from_token(_sess_param)
        if _restored_user:
            st.session_state["rf_user"]       = _restored_user
            st.session_state["session_token"] = _sess_param
            # Restore current_page from URL if present
            _p_param = st.query_params.get("p", "")
            if _p_param in ("dashboard", "predictor", "season", "upgrade", "instructions"):
                st.session_state["current_page"] = _p_param
            print(f"[RF-AUTH] ✅ session restored as {_restored_user!r}  page={_p_param!r}", file=_sys_rf.stderr, flush=True)
        else:
            # Invalid / expired token — remove it from URL
            print("[RF-AUTH] token invalid/expired — clearing from URL", file=_sys_rf.stderr, flush=True)
            st.query_params.pop("session", None)

if st.session_state["rf_user"] is None:
    apply_login_styles()

    # ── Login / Register UI ───────────────────────────────────────────────────
    _LOGO_SRC_LOGIN = _load_logo_b64("RaceFusion-Logo-V3.png")
    if _LOGO_SRC_LOGIN:
        st.markdown(
            f'<img src="{_LOGO_SRC_LOGIN}" style="max-width:600px;width:80%;'
            f'margin:32px auto 8px auto;display:block;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🏁 RaceFusion")

    st.markdown("<div style='text-align:center;color:#888;margin-bottom:32px;'>Run Data Dashboard</div>",
                unsafe_allow_html=True)

    _auth_tab_login, _auth_tab_reg = st.tabs(["Log In", "Create Account"])

    def _do_login(username: str):
        """Set session state and write session token after successful auth.

        SECURITY: wipe ALL session state first so nothing from a previously
        logged-in account (selected run, cached AI analyses, compare
        selections, channel prefs, form state) leaks into this user's session.
        """
        for _k in list(st.session_state.keys()):
            st.session_state.pop(_k, None)
        # Drop any stale run/page pointers carried in the URL
        st.query_params.pop("run", None)
        st.query_params.pop("p", None)
        st.session_state["rf_user"] = username
        _tok = _create_session_token(username)
        if _tok:
            st.session_state["session_token"] = _tok
            st.query_params["session"] = _tok

    with _auth_tab_login:
        st.markdown("### Log In")
        _li_user = st.text_input("Username", key="li_user", placeholder="your username")
        _li_pass = st.text_input("Password", type="password", key="li_pass", placeholder="••••••••")
        if st.button("Log In", type="primary", key="li_btn"):
            _u = _li_user.strip().lower()
            if _verify_login(_u, _li_pass):
                _do_login(_u)
                st.rerun()
            elif _check_user_exists(_u):
                st.error("Incorrect password.")
            else:
                st.error("Username not found. Create an account on the right.")

        with st.expander("Forgot your username or password?"):
            _fgt_tab_user, _fgt_tab_pass = st.tabs(["Forgot Username", "Forgot Password"])

            with _fgt_tab_user:
                st.caption("Enter the email address you registered with.")
                _fgt_email = st.text_input("Email address", key="fgt_email",
                                           placeholder="you@example.com")
                if st.button("Look up username", key="fgt_email_btn"):
                    _fgt_em = _fgt_email.strip().lower()
                    if not _fgt_em:
                        st.warning("Please enter your email address.")
                    elif not _sb:
                        st.error("Database unavailable.")
                    else:
                        try:
                            # Email is stored in user_configs as part of the JSON config blob.
                            _cfg_rows = _sb.table("user_configs").select("username, config").execute().data
                            _matched_user = None
                            for _row in (_cfg_rows or []):
                                try:
                                    _cfg_blob = json.loads(_row.get("config") or "{}")
                                    if _cfg_blob.get("email", "").strip().lower() == _fgt_em:
                                        _matched_user = _row["username"]
                                        break
                                except Exception:
                                    pass
                            if _matched_user:
                                st.success(f"Your username is: **{_matched_user}**")
                            else:
                                st.error("No account found with that email address.")
                        except Exception as _fgt_e:
                            st.error(f"Lookup failed: {_fgt_e}")

            with _fgt_tab_pass:
                st.caption("Enter your username to receive a temporary password.")
                _fgt_uname = st.text_input("Username", key="fgt_uname",
                                           placeholder="your username")
                if st.button("Reset password", key="fgt_pass_btn"):
                    _fgt_u = _fgt_uname.strip().lower()
                    if not _fgt_u:
                        st.warning("Please enter your username.")
                    elif not _sb:
                        st.error("Database unavailable.")
                    elif not _check_user_exists(_fgt_u):
                        st.error("Username not found.")
                    else:
                        try:
                            _tmp_pass = _secrets.token_urlsafe(10)   # e.g. "Xk3mQ9vR2p_A"
                            _new_salt, _new_hash = _hash_password(_tmp_pass)
                            _sb.table("credentials").update({
                                "salt":          _new_salt,
                                "password_hash": _new_hash,
                            }).eq("username", _fgt_u).execute()
                            st.success(
                                f"Temporary password set. Log in with:\n\n"
                                f"**Username:** `{_fgt_u}`  \n"
                                f"**Temp password:** `{_tmp_pass}`\n\n"
                                f"Change your password in Account Settings after logging in."
                            )
                        except Exception as _rst_e:
                            st.error(f"Password reset failed: {_rst_e}")

    with _auth_tab_reg:
        st.markdown("### Create Account")
        st.caption("All fields are required.")
        _reg_user  = st.text_input("Username *",          key="reg_user",  placeholder="lowercase, no spaces")
        _reg_email = st.text_input("Email address *",     key="reg_email", placeholder="you@example.com",
                                   help="We'll notify you of maintenance windows and updates")
        _reg_pass  = st.text_input("Password *",          type="password", key="reg_pass",  placeholder="min 6 characters")
        _reg_pass2 = st.text_input("Confirm password *",  type="password", key="reg_pass2", placeholder="repeat password")
        if st.button("Create Account", type="primary", key="reg_btn"):
            _u  = _reg_user.strip().lower()
            _em = _reg_email.strip().lower()
            # Validate every field before touching the DB
            if not _u:
                st.error("⚠️ Username is required.")
            elif not re.match(r"^[a-z0-9_\-]{2,32}$", _u):
                st.error("Username must be 2–32 characters: letters, numbers, _ or - only.")
            elif not _em:
                st.error("⚠️ Email address is required.")
            elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", _em):
                st.error("⚠️ Please enter a valid email address (e.g. you@example.com).")
            elif not _reg_pass:
                st.error("⚠️ Password is required.")
            elif len(_reg_pass) < 6:
                st.error("Password must be at least 6 characters.")
            elif not _reg_pass2:
                st.error("⚠️ Please confirm your password.")
            elif _reg_pass != _reg_pass2:
                st.error("⚠️ Passwords do not match.")
            elif _check_user_exists(_u):
                st.error("Username already taken. Please choose a different username.")
            else:
                if _register_user(_u, _reg_pass, email=_em):
                    # Save initial config with email
                    if _sb:
                        try:
                            _sb.table("user_configs").upsert(
                                {"username": _u, "config": json.dumps({"email": _em})},
                                on_conflict="username",
                            ).execute()
                        except Exception:
                            pass
                    _do_login(_u)
                    st.rerun()
                else:
                    st.error("Registration failed — Supabase may not be configured. Check your .env.")

    st.stop()   # block rest of app until logged in

# ── Logged in ─────────────────────────────────────────────────────────────────
_current_user: str = st.session_state["rf_user"]

# ── Session heartbeat — update last_seen without touching session_token ───────
# Use UPDATE (not upsert) so we never accidentally NULL out session_token/expires_at
if _sb:
    try:
        _sb.table("sessions").update({"last_seen": "now()"}) \
           .eq("username", _current_user).execute()
    except Exception as _sess_err:
        print(f"[RF-DEBUG] sessions last_seen update FAILED: {_sess_err}", file=_sys_rf.stderr, flush=True)

# ── Upload session state — initialize once, never undefined ──────────────────
for _k, _v in {
    "active_run_id":           None,        # canonical active run filename
    "upload_gen":              0,           # incremented by Save & Close to reset form state
    "_create_run_instance_key": 0,          # incremented each time user enters Create New Run form
    "current_page":            "dashboard", # "dashboard" | "predictor" | "season" | "upgrade" | "run_manager" | "instructions" | "car_profile"
}.items():
    if _k not in st.session_state:
        # Restore current_page from URL on refresh
        if _k == "current_page":
            _pg_param = st.query_params.get("p", "")
            st.session_state[_k] = _pg_param if _pg_param in ("dashboard", "predictor", "season", "upgrade", "run_manager", "instructions") else _v
        else:
            st.session_state[_k] = _v

# ── Channel groups ────────────────────────────────────────────────────────────
CHANNEL_GROUPS = {
    "🔥 Engine": [
        "Engine RPM", "DS RPM", "MSD Engine RPM", "Conv % Slip",
        "Engine/DS Ratio", "MSD Engine Timing", "MSD RevLim RPM",
    ],
    "⚡ Performance": [
        "Accel G", "Lateral G", "G-Meter MPH", "G-Meter Distance", "Track Time",
    ],
    "🌡️ EGT (Exhaust Temps)": [
        "Cyl #1", "Cyl #2", "Cyl #3", "Cyl #4",
        "Cyl #5", "Cyl #6", "Cyl #7", "Cyl #8", "Avg. EGT",
    ],
    "🌡️ Temperatures": [
        "Trans Temp", "Man Temp", "L Head Temp", "Oil Temp",
    ],
    "💧 Pressures & Flow": [
        "Oil Press", "Pan Press", "Boost Press", "Fuel Press", "Fuel Flow",
        "Logger Volts",
    ],
    "📏 ET Clocks": [
        "Clock 60ft", "Clock 330ft", "Clock 660ft", "Clock 1000ft", "Clock 1320ft",
    ],
    "🔌 MSD / Digital": [
        "MSD Launch", "MSD Burn-Out", "MSD Output Sw 1", "Record Button",
        "Total DS Turns", "CC'S Per Turn", "Time_2",
    ],
}
ALL_GROUPED = [ch for chs in CHANNEL_GROUPS.values() for ch in chs]

# ── (RPM_CHANNEL_NAMES and TRACE_COLORS extracted to charts.py) ──────────────

# ── (check_alerts, load_racepak_csv, get_time_col, detect_shift_points, calc_rwhp
#      extracted to run_analysis.py) ─────────────────────────────────────────────
# ── (_rdp_percentile, _rdp_linear_regression, _rdp_r_squared
#      extracted to race_day_predictor.py) ──────────────────────────────────────

# ── Load config once, before any sidebar widgets that need it ─────────────────
cfg = load_config()

# ── One-time email backfill: copy email from user_configs → credentials ───────
# Runs once per session per user. Safe to repeat; skips rows that already have email.
if _sb and _current_user and not st.session_state.get("_email_backfill_done"):
    try:
        _bf_cred = _sb.table("credentials").select("email") \
                       .eq("username", _current_user).execute().data
        if _bf_cred and not _bf_cred[0].get("email"):
            _bf_cfg = _sb.table("user_configs").select("config") \
                          .eq("username", _current_user).execute().data
            if _bf_cfg:
                try:
                    _bf_email = json.loads(_bf_cfg[0].get("config") or "{}").get("email", "")
                except Exception:
                    _bf_email = ""
            else:
                _bf_email = cfg.get("email", "")
            if _bf_email:
                _sb.table("credentials").update({"email": _bf_email}) \
                   .eq("username", _current_user).execute()
                print(f"[RF-MIGRATION] backfilled email for {_current_user!r}: {_bf_email!r}",
                      file=_sys_rf.stderr, flush=True)
    except Exception as _bf_e:
        print(f"[RF-MIGRATION] email backfill failed: {_bf_e}", file=_sys_rf.stderr, flush=True)
    st.session_state["_email_backfill_done"] = True

# ── Subscription state — resolved once per session ────────────────────────────
# Clear cached state if returning from Stripe Checkout success
if "stripe_success" in st.query_params:
    st.session_state.pop("sub_tier", None)
    st.query_params.pop("stripe_success", None)

if "sub_tier" not in st.session_state:
    _sub_rec      = _get_user_subscription(_current_user)
    _trial_start  = _sub_rec.get("trial_start_date")
    _stored_tier  = _sub_rec.get("subscription_tier", "trial")
    # Poll Stripe for fresh status (only if not already on a paid tier)
    if _stored_tier not in ("racer", "pro", "crew_chief"):
        _user_email = cfg.get("email", "")
        if _sb:
            try:
                _cred_email_row = _sb.table("credentials").select("email") \
                                      .eq("username", _current_user).execute().data
                if _cred_email_row and _cred_email_row[0].get("email"):
                    _user_email = _cred_email_row[0]["email"]
            except Exception:
                pass
        _stripe_tier = _check_stripe_subscription(_user_email, _current_user)
        if _stripe_tier:
            _stored_tier = _stripe_tier
    st.session_state["sub_tier"]       = _stored_tier
    st.session_state["trial_active"]   = _is_trial_active(_trial_start)
    st.session_state["access_granted"] = (
        st.session_state["trial_active"]
        or _stored_tier in ("racer", "pro", "crew_chief")
    )
    st.session_state["charts_granted"] = (
        st.session_state["trial_active"]
        or _stored_tier in ("pro", "crew_chief")
    )

_sub_tier       = st.session_state.get("sub_tier", "trial")
_trial_active   = st.session_state.get("trial_active", True)
_access_granted = st.session_state.get("access_granted", True)
_charts_granted = st.session_state.get("charts_granted", True)

# ── Maintenance mode (Supabase-backed) ────────────────────────────────────────
_maintenance_on = _read_maintenance_mode()

if _maintenance_on:
    if st.session_state.get("rf_user") != "weeber70":
        # ── Full-screen block for non-admin users ─────────────────────────────
        apply_all_styles()
        apply_maintenance_styles()

        _maint_email = cfg.get("email", "")
        _maint_logo  = _load_logo_b64("RaceFusion-Logo-V3.png")

        st.markdown(
            f"""
<div style="min-height:100vh;background:#08080d;display:flex;flex-direction:column;
     align-items:center;justify-content:center;padding:40px 24px;text-align:center;">
  {f'<img src="{_maint_logo}" style="max-width:320px;width:60%;margin-bottom:32px;">'
   if _maint_logo else '<h1 style="color:#e8e8e8;">🏁 RaceFusion</h1>'}
  <div style="font-size:2.6rem;margin-bottom:16px;">🏁</div>
  <h2 style="color:#ffffff;font-size:1.8rem;margin:0 0 12px;">RaceFusion is getting an upgrade</h2>
  <p style="color:#aaa;font-size:1.1rem;max-width:480px;line-height:1.6;margin:0 0 24px;">
    We're tuning things up behind the scenes to make your experience faster and better.
  </p>
  {"<p style='color:#cc1111;font-size:1rem;'>We'll email you at <strong style='color:#e8e8e8;'>"
   + _maint_email
   + "</strong> when we're back on track. 🏎️</p>"
   if _maint_email else ""}
</div>""",
            unsafe_allow_html=True,
        )
        st.stop()
    else:
        # ── Admin exemption: show banner, continue rendering ───────────────────
        st.error("⚠️ Maintenance mode is ON — other users see the under construction screen.")

# ── Theme — always dark ───────────────────────────────────────────────────────
apply_all_styles()

# ── weeber70 maintenance banner rendered at top of main content (see line ~3200) ──

# ── Feedback button — fixed top-right, always visible ────────────────────────
import urllib.parse as _urlparse
_fb_user    = st.session_state.get("rf_user", "unknown")
_fb_subject = _urlparse.quote(f"RaceFusion Feedback – {_fb_user}")
_fb_body    = _urlparse.quote(
    f"Username: {_fb_user}\n\n"
    f"What were you doing when the issue occurred?\n\n\n"
    f"What happened?\n\n\n"
    f"What did you expect to happen?\n"
)
_fb_href = f"mailto:chris@weebenterprises.com?subject={_fb_subject}&body={_fb_body}"
st.markdown(f"""
<div style="position: fixed; top: 60px; right: 20px; z-index: 9999;">
  <a href="{_fb_href}"
     style="background:#333; color:#fff; padding:8px 14px; border-radius:6px;
            text-decoration:none; font-size:13px; font-family:sans-serif;">
    📧 Send Feedback
  </a>
</div>
""", unsafe_allow_html=True)

# ── Dropdown hover fix (JS MutationObserver via components.html) ──────────────
# st.markdown strips <script> tags; components.html actually executes JS.
# Targets window.parent.document so it can reach the Streamlit app DOM.
import streamlit.components.v1 as _components
_components.html("""
<script>
(function() {
  var doc = window.parent.document;
  var BG       = '#111111', BG_H = '#8b0000', BG_S = '#5a0000';
  var FG       = '#e8e8e8', FG_H = '#ffffff', FG_S = '#ff8888';

  function paint(el, bg, fg) {
    el.style.setProperty('background',       bg, 'important');
    el.style.setProperty('background-color', bg, 'important');
    el.style.setProperty('color',            fg, 'important');
    for (var i = 0; i < el.children.length; i++) paint(el.children[i], bg, fg);
  }

  function wireMenu(root) {
    var opts = root.querySelectorAll('li[role="option"], [role="option"]');
    if (!opts.length) return;
    root.style.setProperty('background', BG, 'important');
    root.style.setProperty('background-color', BG, 'important');
    opts.forEach(function(opt) {
      var sel = opt.getAttribute('aria-selected') === 'true';
      paint(opt, sel ? BG_S : BG, sel ? FG_S : FG);
      opt.onmouseenter = function() { paint(opt, BG_H, FG_H); };
      opt.onmouseleave = function() {
        var s = opt.getAttribute('aria-selected') === 'true';
        paint(opt, s ? BG_S : BG, s ? FG_S : FG);
      };
    });
  }

  // ── File uploader icon fix ──────────────────────────────────────────────────
  function forceWhiteSvg(svg) {
    svg.setAttribute('fill', 'white');
    svg.style.setProperty('fill', 'white', 'important');
    svg.style.setProperty('color', 'white', 'important');
    svg.querySelectorAll('*').forEach(function(c) {
      c.setAttribute('fill', 'white');
      c.style.setProperty('fill', 'white', 'important');
      if (c.getAttribute('stroke') && c.getAttribute('stroke') !== 'none') {
        c.setAttribute('stroke', 'white');
        c.style.setProperty('stroke', 'white', 'important');
      }
    });
  }

  function fixUploaderIcons() {
    // Approach A: wildcard data-testid selector
    doc.querySelectorAll('[data-testid*="FileUpload"] svg, [data-testid*="fileUpload"] svg').forEach(forceWhiteSvg);
    // Approach B: walk up from input[type="file"] — always present inside Streamlit uploaders
    doc.querySelectorAll('input[type="file"]').forEach(function(inp) {
      var el = inp.parentElement;
      for (var i = 0; i < 8 && el; i++, el = el.parentElement) {
        el.querySelectorAll('button svg, svg').forEach(forceWhiteSvg);
      }
    });
  }

  // Keep retrying for 15s to survive async sidebar renders
  var _iconFixRuns = 0;
  var _iconFixTimer = setInterval(function() {
    fixUploaderIcons();
    if (++_iconFixRuns >= 30) clearInterval(_iconFixTimer);
  }, 500);

  new MutationObserver(function(mutations) {
    mutations.forEach(function(m) {
      m.addedNodes.forEach(function(n) {
        if (!n.querySelectorAll) return;
        n.querySelectorAll('[data-baseweb="menu"], [role="listbox"]').forEach(wireMenu);
        if (n.getAttribute && n.getAttribute('role') === 'listbox') wireMenu(n);
      });
    });
    fixUploaderIcons();
  }).observe(doc.body, { childList: true, subtree: true });
})();
</script>
""", height=0)

# ── Sidebar ───────────────────────────────────────────────────────────────────
if _LOGO_SRC:
    st.sidebar.markdown(
        f'<img src="{_LOGO_SRC}" style="width:100%;max-width:220px;'
        f'display:block;margin:0 auto 8px auto;">',
        unsafe_allow_html=True,
    )
else:
    st.sidebar.title("🏁 RaceFusion")

st.sidebar.caption("Run Data Dashboard")

# ── User badge + logout ───────────────────────────────────────────────────────
_ub_col1, _ub_col2 = st.sidebar.columns([3, 2])
_ub_col1.markdown(f"👤 **{_current_user}**")
if _ub_col2.button("Log Out", key="logout_btn"):
    _delete_session_token(st.session_state.pop("session_token", None) or "")
    st.query_params.pop("session", None)
    st.query_params.pop("p", None)
    st.query_params.pop("run", None)
    # SECURITY: wipe ALL session state (selected run, cached AI analyses,
    # compare selections, subscription flags, form state) so nothing leaks
    # into the next account that logs in on this browser session.
    for _lo_k in list(st.session_state.keys()):
        st.session_state.pop(_lo_k, None)
    st.session_state["rf_user"] = None
    st.rerun()

_cur_page = st.session_state.get("current_page", "dashboard")
# Keep page in URL so it survives browser refresh
if st.query_params.get("p") != _cur_page:
    st.query_params["p"] = _cur_page
if _cur_page != "instructions":
    if st.sidebar.button("📖 Instructions", use_container_width=True, key="nav_to_instructions"):
        st.session_state["current_page"] = "instructions"
        st.query_params["p"] = "instructions"
        st.rerun()
if _cur_page != "car_profile":
    if st.sidebar.button("🔧 Car Profile", use_container_width=True, key="nav_to_car_profile"):
        st.session_state["current_page"] = "car_profile"
        st.query_params["p"] = "car_profile"
        st.rerun()
if _cur_page != "run_manager":
    if st.sidebar.button("🗂️ Run Manager", use_container_width=True, key="nav_to_run_manager"):
        st.session_state["current_page"] = "run_manager"
        st.query_params["p"] = "run_manager"
        st.rerun()
if _cur_page != "dashboard":
    if st.sidebar.button("🏎️ Run Analysis", use_container_width=True, key="nav_to_dashboard"):
        st.session_state["current_page"] = "dashboard"
        st.query_params["p"] = "dashboard"
        st.rerun()
if _cur_page != "predictor":
    if st.sidebar.button("🏁 Race Day Predictor", use_container_width=True, key="nav_to_predictor"):
        st.session_state["current_page"] = "predictor"
        st.query_params["p"] = "predictor"
        st.rerun()
if _cur_page != "season":
    if st.sidebar.button("📅 Season Summary", use_container_width=True, key="nav_to_season"):
        st.session_state["current_page"] = "season"
        st.query_params["p"] = "season"
        st.rerun()
if _cur_page != "upgrade":
    _upg_label = "💳 Upgrade Plan" if not _access_granted else "💳 Manage Subscription"
    if st.sidebar.button(_upg_label, use_container_width=True, key="nav_to_upgrade"):
        st.session_state["current_page"] = "upgrade"
        st.query_params["p"] = "upgrade"
        st.rerun()

st.sidebar.markdown("---")

# ── Car number & weight (used downstream for timeslip scanning and RWHP) ──────
car_number_input = cfg.get("car_number", "")
# Legacy key only — run_analysis re-resolves weight per-run from the car
# snapshot / build sheet. 0 = not set (never assume a placeholder weight).
weight_input = int(cfg.get("car_weight_lbs", 0) or 0)

# ── Run selector ───────────────────────────────────────────────────────────────
_saved_runs = list_saved_runs()
# Inject newly created run into selector if Supabase hasn't returned it yet
_newly_created = st.session_state.get("_newly_created_run")
if _newly_created:
    _nc_id = _newly_created.get("id")
    _nc_in_list = any(r["filename"] == _nc_id for r in _saved_runs)
    if _nc_in_list:
        # Run now appears naturally — clear the cache
        st.session_state.pop("_newly_created_run", None)
    else:
        # Prepend so it's always selectable
        _saved_runs = [{"filename": _nc_id, "label": _newly_created.get("label", _nc_id), "record": _newly_created.get("record", {}), "has_csv": _newly_created.get("has_csv", False)}] + _saved_runs
_qp_run = st.query_params.get("run")
if _qp_run:
    # Always restore from query param — it is the authoritative recovery source.
    # Individual button handlers set query_params["run"] before every st.rerun(),
    # so this fires on every button-triggered rerun and keeps active_run_id stable.
    st.session_state["active_run_id"] = _qp_run
# ── (_delete_run_files extracted to database.py) ─────────────────────────────

# Reset selector after delete
if st.session_state.get("_reset_selector"):
    st.session_state.pop("_run_selector_idx", None)
    st.session_state.pop("run_selector", None)
    st.session_state["active_run_id"] = None
    st.query_params.pop("run", None)
    st.session_state["_reset_selector"] = False
    # Bump instance key so the Create New Run form shows fresh file uploaders.
    st.session_state["_create_run_instance_key"] = (
        st.session_state.get("_create_run_instance_key", 0) + 1
    )

# (sidebar delete buttons removed — success feedback now handled in Run Manager page)
st.session_state.pop("_delete_success", None)
st.session_state.pop("_delete_all_success", None)

# ── active_run_id: single source of truth for which run is active ─────────────
# Navigation is via the Run Manager page — no sidebar selectbox.
# Derive _sel_idx_raw from active_run_id for backward compat with run-view code.
_active_run_id = st.session_state.get("active_run_id")
_sel_idx_raw = 0
if _active_run_id:
    for _i, _r in enumerate(_saved_runs):
        if _r["filename"] == _active_run_id:
            _sel_idx_raw = _i + 1
            break

if _sel_idx_raw == 0:
    _active_csv_name = None
    _active_has_csv  = False
    st.session_state["_was_on_new_run"] = True
else:
    _sel_run_meta    = _saved_runs[_sel_idx_raw - 1]
    _active_csv_name = _sel_run_meta["filename"]
    _active_has_csv  = _sel_run_meta["has_csv"]
    st.session_state["active_run_id"] = _active_csv_name
    st.query_params["run"] = _active_csv_name
    # Flush Create New Run form state when navigating to a specific run
    _was_on_new_run_nav = st.session_state.pop("_was_on_new_run", False)
    if _was_on_new_run_nav:
        st.session_state["upload_gen"] = st.session_state.get("upload_gen", 0) + 1
        _nav_vid_count = st.session_state.get("_create_video_row_count", 3)
        st.session_state["_create_video_row_count"] = 3
        for _nvi in range(_nav_vid_count):
            st.session_state.pop(f"video_url_{_nvi}", None)
            st.session_state.pop(f"video_label_{_nvi}", None)
        st.session_state.pop("_pending_csv", None)
        st.session_state.pop("_pending_timeslip", None)

# ── Persistent "Run Open" indicator ─────────────────────────────────────────
if _active_csv_name:
    # Look up car name (one small DB hit; user typically has 1–2 cars)
    _open_rec     = next((r["record"] for r in _saved_runs if r["filename"] == _active_csv_name), {})
    _open_car_id  = _open_rec.get("car_id")
    _open_car_name = ""
    if _open_car_id:
        _oc_cars  = get_user_cars(_current_user)
        _oc_match = next((c for c in _oc_cars if c["car_id"] == _open_car_id), None)
        if _oc_match:
            _open_car_name = _oc_match.get("car_name", "")

    st.sidebar.markdown("---")
    _ro_label = f"🟢 **Run Open:** {_open_car_name}" if _open_car_name else "🟢 **Run Open**"
    st.sidebar.markdown(_ro_label)
    st.sidebar.caption(_active_csv_name)
    _ro_c1, _ro_c2 = st.sidebar.columns(2)
    if _ro_c1.button("✅ Save & Close", use_container_width=True, key="sidebar_save_close_btn"):
        _old_gen  = st.session_state["upload_gen"]
        _old_inst = st.session_state.get("_create_run_instance_key", 0)
        st.session_state["upload_gen"] = _old_gen + 1
        for _stale_key in [
            f"csv_uploader_{_old_inst}",
            f"slip_uploader_{_old_inst}",
            f"create_car_num_{_old_gen}",
            f"create_run_type_{_old_gen}",
            f"create_note_{_old_gen}",
            f"create_run_btn_{_old_gen}",
            "_last_uploaded_csv",
        ]:
            st.session_state.pop(_stale_key, None)
        st.session_state["_reset_selector"] = True
        st.rerun()
    if _ro_c2.button("🗑 Discard", use_container_width=True, key="sidebar_discard_btn"):
        _delete_run_files(_active_csv_name)
        st.session_state["active_run_id"] = None
        st.session_state["current_page"] = "run_manager"
        st.query_params["p"] = "run_manager"
        st.query_params.pop("run", None)
        st.session_state["_reset_selector"] = True
        st.rerun()

st.sidebar.markdown("---")

# Reserve sidebar slot here — RacePak Controls will be rendered into this
# container later (after CSV data is loaded), so it appears between Run Manager
# and RacePak Data without needing the data to be available at this point.
_racepak_controls_slot = st.sidebar.container()

# ── RacePak Data ──────────────────────────────────────────────────────────────
st.sidebar.markdown("### 📂 Run Data")

if _sel_idx_raw == 0:   # "New run…"
    st.sidebar.caption("📋 Use the **Create New Run** form in the main area →")
elif _active_has_csv:
    st.sidebar.caption(f"✅ Loaded: {_active_csv_name}")
elif _active_csv_name and _active_csv_name.endswith(".run"):
    # Timeslip-only run — auto-process CSV as soon as one is selected
    _csv_up_key    = f"_add_csv_up_{_active_csv_name}"
    _csv_saved_key = f"_csv_saved_{_active_csv_name}"
    if not _has_feature("csv_upload"):
        st.sidebar.caption("🔒 CSV upload available on Pro.")
        _add_csv_file = None
    else:
        _add_csv_file  = st.sidebar.file_uploader(
            "Add Run Data CSV to this run",
            type=["csv"],
            key=_csv_up_key,
            help="Attach a Run Data CSV to combine with your timeslip data",
        )
    if _add_csv_file is not None and not st.session_state.get(_csv_saved_key):
        if st.sidebar.button("💾 Save CSV to this run", key=f"save_csv_btn_{_active_csv_name}",
                             use_container_width=True, type="primary"):
            with st.sidebar.spinner("💾 Saving CSV…"):
                save_run_csv(_active_csv_name, _add_csv_file.read())
                st.session_state["active_run_id"] = _active_csv_name
                st.query_params["run"] = _active_csv_name
                st.session_state[_csv_saved_key] = True
            st.rerun()
    elif _add_csv_file is None:
        st.sidebar.caption("🎫 Timeslip-only — add channel data above")
else:
    st.sidebar.caption(f"⚠️ CSV not found: {_active_csv_name}")

st.sidebar.markdown("---")

# ── Timeslip
st.sidebar.markdown("### 🎫 Timeslip Scanner")
api_key = _get_secret("ANTHROPIC_API_KEY")
if not api_key:
    st.sidebar.warning("⚠️ ANTHROPIC_API_KEY not set in .env — timeslip scanning and AI tuner unavailable.")

# Reserve a container so scan status appears in Timeslip Scanner sidebar position
_scan_status_area = st.sidebar.container()

if _sel_idx_raw == 0:
    # New run — slip is uploaded via the Create New Run form in the main area
    st.sidebar.caption("📋 Add a timeslip in the **Create New Run** form →")
elif _active_csv_name:
    _existing_run     = load_run(_active_csv_name)
    _existing_slip_key = _existing_run.get("timeslip_storage_key")
    if not _existing_slip_key:
        # No timeslip yet — auto-process as soon as a photo is selected
        _slip_up_key    = f"_add_slip_up_{_active_csv_name}"
        _slip_saved_key = f"_slip_saved_{_active_csv_name}"
        _add_slip_file  = st.sidebar.file_uploader(
            "Upload timeslip photo",
            type=["jpg", "jpeg", "png", "webp"],
            key=_slip_up_key,
            help="Clear photo of your printed timeslip",
        )
        if _add_slip_file is not None and not st.session_state.get(_slip_saved_key):
            _sl_bytes = _add_slip_file.read()
            _sl_ext   = _add_slip_file.name.rsplit(".", 1)[-1].lower()
            _sl_stem  = re.sub(r"[^\w\-]", "_", Path(_active_csv_name).stem)
            _sl_key   = f"{_current_user}/{_sl_stem}.{_sl_ext}"
            _sl_mime  = {"jpg":"image/jpeg","jpeg":"image/jpeg",
                         "png":"image/png","webp":"image/webp"}.get(_sl_ext, "image/jpeg")
            _slip_upload_ok = True
            with _scan_status_area.status("📤 Uploading timeslip…", expanded=True) as _add_slip_status:
                # 1. Upload to storage
                if _sb:
                    try:
                        _sb.storage.from_("timeslips").upload(
                            path=_sl_key, file=_sl_bytes,
                            file_options={"upsert": "true", "content-type": _sl_mime},
                        )
                    except Exception as _sl_se:
                        _add_slip_status.update(label=f"❌ Upload failed: {_sl_se}", state="error")
                        _slip_upload_ok = False

                if _slip_upload_ok:
                    _existing_run["timeslip_storage_key"] = _sl_key
                    _existing_run.pop("timeslip", None)
                    _existing_run.pop("weather", None)
                    save_run(_active_csv_name, _existing_run)

                    # 2. Scan the timeslip — always attempt when api_key is available,
                    #    regardless of whether a car number is configured.
                    _scan_went_to_review = False
                    if api_key:
                        _add_slip_status.write("🎫 Scanning timeslip…")
                        try:
                            _scan_result = scan_timeslip(_sl_bytes, _sl_mime, api_key, car_number_input)
                            _scan_result["_scanned_with"] = car_number_input.strip()
                            # Only proceed to review if the scan returned at least some readable data.
                            _has_scan_data = any(
                                _scan_result.get(k) is not None
                                for k in ("ft_1320", "ft_60", "date", "track_name",
                                          "mph_1320", "reaction_time", "ft_330",
                                          "ft_660", "ft_1000")
                            )
                            if _has_scan_data:
                                st.session_state["pending_timeslip"] = {
                                    "scan_result":              _scan_result,
                                    "run_id":                   _active_csv_name,
                                    "run_rec":                  dict(_existing_run),
                                    "existing_run":             True,
                                    "storage_freshly_uploaded": True,
                                    "sl_bytes":                 _sl_bytes,
                                    "sl_mime":                  _sl_mime,
                                    "form_car_number":          car_number_input.strip(),
                                    "csv_hsave":                None,
                                    "slp_hsave":                None,
                                    "form_videos":              [],
                                    "submit_car_id":            None,
                                }
                                st.session_state[_slip_saved_key] = True
                                _add_slip_status.update(
                                    label="✅ Scan complete — review results",
                                    state="complete", expanded=False,
                                )
                                _scan_went_to_review = True
                            else:
                                _add_slip_status.update(
                                    label="❌ Could not read timeslip",
                                    state="error", expanded=True,
                                )
                                _add_slip_status.error(
                                    "Could not read timeslip — please try again or "
                                    "check the image quality."
                                )
                        except Exception as _scan_e:
                            _add_slip_status.update(label="❌ Scan failed", state="error", expanded=True)
                            _add_slip_status.error(f"Scan failed: {_scan_e}")

                    if not _scan_went_to_review:
                        st.session_state["active_run_id"] = _active_csv_name
                        st.query_params["run"] = _active_csv_name
                        st.session_state[_slip_saved_key] = True
                        if not api_key:
                            _add_slip_status.update(label="✅ Timeslip added!", state="complete", expanded=False)

            if _slip_upload_ok:
                st.rerun()
    else:
        # Timeslip already on file — show re-scan / delete controls
        _rescan_col, _del_slip_col = st.sidebar.columns(2)
        if _rescan_col.button("🔄 Re-scan", key="rescan_btn", use_container_width=True):
            _existing_run.pop("timeslip", None)
            _existing_run.pop("weather", None)
            save_run(_active_csv_name, _existing_run)
            st.session_state["active_run_id"] = _active_csv_name
            st.query_params["run"] = _active_csv_name
            st.rerun()
        if _del_slip_col.button("🗑️ Delete", key="del_slip_btn", use_container_width=True):
            _delete_slip_from_storage(_existing_slip_key)
            _existing_run.pop("timeslip", None)
            _existing_run.pop("weather", None)
            _existing_run.pop("timeslip_storage_key", None)
            save_run(_active_csv_name, _existing_run)
            st.session_state["active_run_id"] = _active_csv_name
            st.query_params["run"] = _active_csv_name
            # Clear the upload-guard so the file uploader accepts a fresh file.
            st.session_state.pop(f"_slip_saved_{_active_csv_name}", None)
            st.rerun()
        st.sidebar.caption(f"✅ Timeslip on file: {_existing_slip_key.split('/')[-1]}")

st.sidebar.markdown("---")

# ── Track Location sidebar removed (2026-07-21) ───────────────────────────────
# Per-run weather is resolved from each run's own timeslip track, with an
# explicit per-run prompt when geocoding fails (see run_analysis.py). The
# Race Day Predictor has its own page-local track field. No global location
# setting feeds the run/weather pipeline anymore.

# ── Trial-expired banner (shown on every page when access is locked) ──────────
if not _access_granted:
    st.markdown(
        """<div style="background:#7a0000;color:#fff;padding:12px 20px;border-radius:6px;
        margin-bottom:16px;font-weight:600;font-size:1rem;border:1px solid #cc0000;">
        🔒 Your 30-day trial has expired — upgrade to continue adding runs.
        </div>""",
        unsafe_allow_html=True,
    )

# ── Admin Panel (weeber70 only) ─────────────────────────────────────────────────
show_admin_panel(maintenance_on=_maintenance_on, current_user=_current_user)

# ── Race Day Predictor page ─────────────────────────────────────────────────────
if st.session_state.get("current_page") == "predictor":
    show_race_day_predictor(cfg=cfg, current_user=_current_user, access_granted=_access_granted, logo_src=_LOGO_SRC)
    st.stop()

# ── Season Summary page ─────────────────────────────────────────────────────────
if st.session_state.get("current_page") == "season":
    show_season_summary(saved_runs=_saved_runs, cfg=cfg, logo_src=_LOGO_SRC)
    st.markdown(_FOOTER_HTML, unsafe_allow_html=True)
    st.stop()

# ── Upgrade page ──────────────────────────────────────────────────────────────
if st.session_state.get("current_page") == "upgrade":
    _sess_tok = st.session_state.get("session_token", "")

    if _LOGO_SRC:
        st.markdown(
            f'<img src="{_LOGO_SRC}" style="max-width:520px;width:60%;'
            f'margin:0 auto 4px auto;display:block;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🏁 RaceFusion")
    st.markdown("# 💳 Upgrade RaceFusion")

    # ── Proactive Stripe check on every upgrade page load for trial users ─────
    # Catches missed redirects (browser closed, Streamlit restart, etc.).
    if _sub_tier not in ("racer", "pro", "crew_chief") and _sb:
        try:
            _pro_cred = _sb.table("credentials").select("email, stripe_customer_id") \
                            .eq("username", _current_user).execute().data
            _pro_email = (_pro_cred[0].get("email") or cfg.get("email", "")) if _pro_cred else cfg.get("email", "")
        except Exception:
            _pro_email = cfg.get("email", "")
        _live_tier = _check_stripe_subscription(_pro_email, _current_user)
        if _live_tier and _live_tier not in ("trial", None):
            st.session_state["sub_tier"]        = _live_tier
            st.session_state["trial_active"]    = False
            st.session_state["access_granted"]  = True
            st.session_state["charts_granted"]  = _live_tier in ("pro", "crew_chief")
            _sub_tier = _live_tier
            st.rerun()

    # ── Post-payment success handler ──────────────────────────────────────────
    if st.query_params.get("success") == "true":
        _cs_id       = st.query_params.get("cs_id", "")
        _saved_sess  = st.query_params.get("session", "")
        st.query_params.clear()                    # wipe all params including success/cs_id
        if _saved_sess:
            st.query_params["session"] = _saved_sess   # restore session token so user stays logged in
        if not _cs_id or not _cs_id.startswith("cs_"):
            # Stale bookmark or manually crafted URL — ignore silently.
            pass
        else:
            # Valid Stripe checkout session ID — poll Stripe to confirm.
            # Fetch email from credentials so it's accurate even if cfg is stale.
            _pay_email = cfg.get("email", "")
            if _sb:
                try:
                    _cred_row = _sb.table("credentials").select("email, stripe_customer_id") \
                                    .eq("username", _current_user).execute().data
                    if _cred_row and _cred_row[0].get("email"):
                        _pay_email = _cred_row[0]["email"]
                except Exception:
                    pass
            _new_tier  = _check_stripe_subscription(_pay_email, _current_user)
            if _new_tier and _new_tier != "trial":
                st.session_state["sub_tier"]        = _new_tier
                st.session_state["trial_active"]    = False
                st.session_state["access_granted"]  = True
                st.session_state["charts_granted"]  = _new_tier in ("pro", "crew_chief")
                _sub_tier = _new_tier
                st.session_state["_stripe_flash"] = "success"
            else:
                st.session_state["_stripe_flash"] = "pending"

    # Show the flash exactly once (session state survives the query_params.clear() rerun).
    _stripe_flash = st.session_state.pop("_stripe_flash", None)
    if _stripe_flash == "success":
        st.success("🎉 Subscription activated! Welcome to RaceFusion.")
        st.balloons()
    elif _stripe_flash == "pending":
        st.warning(
            "Payment received but subscription not yet confirmed by Stripe. "
            "Please refresh in a moment."
        )

    if _trial_active:
        _upg_sub_rec   = _get_user_subscription(_current_user)
        _upg_ts_str    = _upg_sub_rec.get("trial_start_date") or ""
        _upg_ts        = (
            _dt.fromisoformat(_upg_ts_str.replace("Z", "+00:00")).replace(tzinfo=_tz.utc)
            if _upg_ts_str else _dt.now(_tz.utc)
        )
        _days_left = max(0, 30 - (_dt.now(_tz.utc) - _upg_ts).days)
        st.markdown(
            f"<p style='color:#22aa55;'>✅ Your trial is active — <strong>{_days_left} day(s)</strong> remaining.</p>",
            unsafe_allow_html=True,
        )
    elif _sub_tier in ("racer", "pro", "crew_chief"):
        _tier_prices = {"racer": "9.99", "pro": "19.99", "crew_chief": "34.99"}
        _tier_label  = _sub_tier.replace("_", " ").title()
        _tier_price  = _tier_prices.get(_sub_tier, "?")
        st.success(f"✅ You're subscribed to **{_tier_label}** — ${_tier_price}/month")
    else:
        st.markdown(
            "<p style='color:#cc1111;font-weight:600;'>⚠️ Your trial has expired. Choose a plan to continue.</p>",
            unsafe_allow_html=True,
        )

    if stripe.api_key and stripe.api_key.startswith("sk_test_"):
        st.info(
            "🧪 **Beta Testing Mode** — No real charges will be made. "
            "Use test card **4242 4242 4242 4242**, any future expiry, any CVC, any ZIP."
        )

    st.markdown("---")

    _tier_data = [
        {
            "key":      "racer",
            "label":    "🏎️ Racer",
            "price":    "$9.99/month",
            "price_id": _STRIPE_PRICE_RACER,
            "features": [
                ("✅", "Unlimited timeslips"),
                ("✅", "ET Predictor"),
                ("✅", "Weather / DA tracking"),
                ("✅", "Season Summary"),
                ("✅", "Race Day Predictor"),
            ],
        },
        {
            "key":      "pro",
            "label":    "🏆 Pro",
            "price":    "$19.99/month",
            "price_id": _STRIPE_PRICE_PRO,
            "features": [
                ("✅", "Everything in Racer"),
                ("✅", "1 car"),
                ("✅", "Interactive Channel Charts"),
                ("✅", "Channel Peaks & alerts"),
                ("✅", "Custom channel overlays"),
                ("✅", "AI Virtual Tuner"),
            ],
        },
        {
            "key":      "crew_chief",
            "label":    "🧠 Crew Chief",
            "price":    "$34.99/month",
            "price_id": _STRIPE_PRICE_CREW_CHIEF,
            "features": [
                ("✅", "Everything in Pro"),
                ("✅", "AI Virtual Tuner"),
                ("🔜", "Unlimited cars (coming soon)"),
                ("🔜", "Team logins — up to 3 users (coming soon)"),
            ],
        },
    ]

    _upg_cols = st.columns(3)
    for _tier, _col in zip(_tier_data, _upg_cols):
        with _col:
            _is_current   = _sub_tier == _tier["key"]
            _border_color = "#cc1111" if _is_current else "#2a2a3a"
            _bg           = "#140000" if _is_current else "#0a0a0a"

            _li_parts = []
            for _icon, _text in _tier["features"]:
                _color = "#e8e8e8" if _icon == "✅" else "#888"
                _li_parts.append(
                    f'<li style="margin:5px 0;color:{_color};">{_icon} {_text}</li>'
                )
            _features_html = "".join(_li_parts)

            _current_badge = (
                '<div style="color:#22aa55;font-weight:600;font-size:0.85rem;">← Current plan</div>'
                if _is_current else ""
            )

            # min-height + flexbox keeps all cards the same height regardless of feature count.
            # flex:1 on the <ul> pushes the badge/empty space to fill, aligning buttons below.
            _card_html = (
                f'<div style="border:2px solid {_border_color};border-radius:10px;'
                f'padding:20px 18px;background:{_bg};font-family:monospace;'
                f'min-height:320px;display:flex;flex-direction:column;">'
                f'<div style="font-size:1.3rem;font-weight:700;color:#fff;margin-bottom:4px;">{_tier["label"]}</div>'
                f'<div style="font-size:1.1rem;color:#cc1111;font-weight:700;margin-bottom:12px;">{_tier["price"]}</div>'
                f'<ul style="list-style:none;padding:0;margin:0;flex:1;">{_features_html}</ul>'
                f'{_current_badge}'
                f'</div>'
            )
            st.markdown(_card_html, unsafe_allow_html=True)

            _is_paid_subscriber = _sub_tier in ("racer", "pro", "crew_chief")
            if _is_paid_subscriber:
                pass  # "← Current plan" badge already shown inside the card HTML above
                # No subscribe buttons for paid subscribers — manage via Stripe portal
            elif not _tier["price_id"]:
                st.caption("Configure STRIPE_PRICE env var to enable.")
            else:
                if st.button(
                    f"Subscribe — {_tier['price']}",
                    key=f"sub_btn_{_tier['key']}",
                    type="primary",
                    use_container_width=True,
                ):
                    _checkout_url = _create_stripe_checkout(
                        _tier["price_id"], _sess_tok,
                        username=_current_user, email=cfg.get("email", ""),
                    )
                    if _checkout_url:
                        st.markdown(
                            f'<meta http-equiv="refresh" content="0;url={_checkout_url}">',
                            unsafe_allow_html=True,
                        )
                        st.info("Redirecting to Stripe checkout…")
                    else:
                        _err = st.session_state.get("_stripe_last_error", "No exception captured — check server logs.")
                        st.error(f"Stripe error: {_err}")

    st.markdown("---")
    if _sub_tier in ("racer", "pro", "crew_chief"):
        if st.button("⚙️ Manage Subscription", key="manage_sub_btn"):
            _portal_cust_id = None
            if _sb:
                try:
                    _portal_cred = _sb.table("credentials").select("stripe_customer_id") \
                                       .eq("username", _current_user).execute().data
                    if _portal_cred:
                        _portal_cust_id = _portal_cred[0].get("stripe_customer_id") or None
                except Exception:
                    pass
            if not _portal_cust_id:
                st.error("No Stripe customer record found for your account. Contact support.")
            else:
                try:
                    _portal_return = _get_secret("APP_BASE_URL", "http://127.0.0.1:8501").rstrip("/") + "/?p=upgrade"
                    _portal_session = stripe.billing_portal.Session.create(
                        customer=_portal_cust_id,
                        return_url=_portal_return,
                    )
                    st.markdown(
                        f'<meta http-equiv="refresh" content="0; url={_portal_session.url}">',
                        unsafe_allow_html=True,
                    )
                    st.info("Redirecting to Stripe customer portal…")
                except Exception as _portal_e:
                    st.error(f"Could not open portal: {_portal_e}")
    st.caption("All plans billed monthly. Cancel anytime. Secure payments via Stripe.")

    if st.button("← Back to Run Analysis", key="upgrade_back_btn"):
        st.session_state["current_page"] = "dashboard"
        st.query_params["p"] = "dashboard"
        st.rerun()

    st.markdown(_FOOTER_HTML, unsafe_allow_html=True)
    st.stop()  # Don't render the dashboard on the upgrade page

# ── Run Manager page ────────────────────────────────────────────────────────────
if st.session_state.get("current_page") == "run_manager":
    show_run_manager(saved_runs=_saved_runs, current_user=_current_user, access_granted=_access_granted, logo_src=_LOGO_SRC)
    st.stop()

# ── Instructions page ────────────────────────────────────────────────────────────
if st.session_state.get("current_page") == "instructions":
    show_instructions(logo_src=_LOGO_SRC)
    st.markdown(_FOOTER_HTML, unsafe_allow_html=True)
    st.stop()

# ── Car Profile page ──────────────────────────────────────────────────────────
if st.session_state.get("current_page") == "car_profile":
    show_car_profile(current_user=_current_user, logo_src=_LOGO_SRC)
    st.markdown(_FOOTER_HTML, unsafe_allow_html=True)
    st.stop()

# ── Run Comparison page ───────────────────────────────────────────────────────
if st.session_state.get("current_page") == "run_comparison":
    show_run_comparison(username=_current_user, logo_src=_LOGO_SRC)
    st.markdown(_FOOTER_HTML, unsafe_allow_html=True)
    st.stop()

# ── Main area (Run Analysis / Create New Run) ───────────────────────────────────
show_run_analysis(
    saved_runs=_saved_runs,
    cfg=cfg,
    sel_idx_raw=_sel_idx_raw,
    logo_src=_LOGO_SRC,
    access_granted=_access_granted,
    current_user=_current_user,
    has_feature=_has_feature,
    channel_groups=CHANNEL_GROUPS,
    all_grouped=ALL_GROUPED,
    _scan_status_area=_scan_status_area,
    _racepak_controls_slot=_racepak_controls_slot,
)
st.markdown(_FOOTER_HTML, unsafe_allow_html=True)
