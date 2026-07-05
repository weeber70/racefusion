"""
RaceFusion - RacePak Data Dashboard
Reads RacePak CSV exports and displays all channels in an interactive dashboard.
Includes timeslip scanning (Claude vision) and historical weather (Open-Meteo).
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import os
import base64
import re
import requests
from pathlib import Path
from dotenv import load_dotenv
from pathlib import Path as _Path
load_dotenv(_Path(__file__).parent / ".env")  # loads ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY from .env
try:
    from supabase import create_client as _sb_create_client
except ImportError:
    _sb_create_client = None  # type: ignore

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="RaceFusion",
    page_icon="🏁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Logo loader ───────────────────────────────────────────────────────────────
def _load_logo_b64(filename: str = "racefusion_logo.png") -> str | None:
    """Return base64 data-URI for the logo if the file exists next to app.py."""
    p = Path(__file__).parent / filename
    if p.exists():
        ext = p.suffix.lstrip(".").lower()
        mime = {"png": "image/png", "jpg": "image/jpeg",
                "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
    return None

_LOGO_SRC = _load_logo_b64("RaceFusion.jpg")

# ── Theme CSS ─────────────────────────────────────────────────────────────────
def _inject_theme(dark: bool):
    if dark:
        css = """
<style>
/* ── Core backgrounds ── */
.stApp, [data-testid="stAppViewContainer"] {
    background-color: #08080d !important;
}
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
    background-color: #0d0d14 !important;
    border-right: 1px solid #2a1a1a !important;
}
[data-testid="stHeader"] {
    background-color: #08080d !important;
    border-bottom: 1px solid #1a0a0a !important;
}
.main .block-container {
    background-color: #08080d !important;
}
/* ── Text ── */
.stApp, .stMarkdown, p, span, label, div {
    color: #e8e8e8 !important;
}
h1, h2, h3, h4, h5, h6 {
    color: #ffffff !important;
}
.stCaption, [data-testid="stCaptionContainer"] {
    color: #999 !important;
}
/* ── Sidebar text ── */
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] div,
[data-testid="stSidebar"] .stMarkdown {
    color: #e0e0e0 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #cc1111 !important;
}
/* ── Inputs ── */
input, textarea, select,
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stSelectbox"] select {
    background-color: #141420 !important;
    color: #e8e8e8 !important;
    border: 1px solid #2a2a3a !important;
    border-radius: 6px !important;
}
/* ── Placeholder text — readable but clearly distinct from entered values ── */
input::placeholder, textarea::placeholder,
[data-testid="stTextInput"] input::placeholder,
[data-testid="stNumberInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder {
    color: #888888 !important;
    opacity: 1 !important;
}
[data-baseweb="select"] > div,
[data-baseweb="select"] > div:hover,
[data-baseweb="select"] > div:focus-within,
[data-baseweb="input"] > div,
[data-baseweb="input"] > div:hover {
    background-color: #141420 !important;
    border: 1px solid #2a2a3a !important;
}
[data-baseweb="select"] > div:hover {
    border-color: #8b0000 !important;
}
[data-baseweb="select"] *,
[data-baseweb="select"]:hover *,
[data-baseweb="select"] > div > div,
[data-baseweb="select"] > div > div * {
    background-color: #141420 !important;
    color: #e8e8e8 !important;
}
/* ── Buttons ── */
button[kind="primary"], [data-testid="baseButton-primary"] {
    background-color: #cc1111 !important;
    color: #ffffff !important;
    border: none !important;
    font-weight: 700 !important;
}
button[kind="primary"]:hover, [data-testid="baseButton-primary"]:hover {
    background-color: #ee2222 !important;
}
button[kind="secondary"], [data-testid="baseButton-secondary"] {
    background-color: #1a1a24 !important;
    color: #e8e8e8 !important;
    border: 1px solid #3a2a2a !important;
}
button[kind="secondary"]:hover {
    background-color: #2a1a1a !important;
    border-color: #cc1111 !important;
}
/* ── Form submit buttons ── */
[data-testid="stFormSubmitButton"] button,
[data-testid="stFormSubmitButton"] > button,
.stFormSubmitButton button {
    background-color: #1a1a24 !important;
    color: #e8e8e8 !important;
    border: 1px solid #3a2a2a !important;
}
[data-testid="stFormSubmitButton"] button:hover,
[data-testid="stFormSubmitButton"] > button:hover,
.stFormSubmitButton button:hover {
    background-color: #2a1a1a !important;
    border-color: #cc1111 !important;
    color: #ffffff !important;
}
[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"],
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"],
.stFormSubmitButton button[kind="primaryFormSubmit"] {
    background-color: #cc1111 !important;
    color: #ffffff !important;
    border: none !important;
    font-weight: 700 !important;
}
[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"]:hover {
    background-color: #ee2222 !important;
}
/* ── Expanders ── */
[data-testid="stExpander"] {
    background-color: #0f0f18 !important;
    border: 1px solid #2a2a3a !important;
    border-radius: 8px !important;
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary:hover,
[data-testid="stExpander"] details summary,
[data-testid="stExpander"] > details > summary {
    background-color: #0f0f18 !important;
    color: #e8e8e8 !important;
}
[data-testid="stExpander"] details,
[data-testid="stExpander"] details[open],
[data-testid="stExpander"] details[open] > summary {
    background-color: #0f0f18 !important;
    color: #e8e8e8 !important;
}
/* ── Metrics ── */
[data-testid="metric-container"] {
    background-color: #0f0f18 !important;
    border: 1px solid #2a1a1a !important;
    border-radius: 8px !important;
    padding: 12px !important;
}
[data-testid="stMetricLabel"] { color: #999 !important; text-align: center !important; }
[data-testid="stMetricValue"] { color: #e8e8e8 !important; text-align: center !important; }
/* Prevent metric values from truncating with "…" — allow wrap and fluid font size */
[data-testid="stMetricValue"] > div {
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: unset !important;
    font-size: clamp(1rem, 1.8vw, 2rem) !important;
    line-height: 1.2 !important;
    word-break: break-word !important;
}
/* ── Tabs & sections ── */
[data-testid="stHorizontalBlock"] { gap: 1rem; }
[data-testid="stVerticalBlock"] { gap: 0.5rem; }
/* ── File uploader dropzone — white bg so use dark text ── */
/* Scope to instructions only; the span rule was too broad and made the button icon dark */
[data-testid="stFileUploaderDropzoneInstructions"],
[data-testid="stFileUploaderDropzoneInstructions"] *,
[data-testid="stFileUploaderDropzone"] small,
[data-testid="stFileUploaderDropzone"] p,
[data-testid="stFileUploaderDropzone"] span:not(button span) {
    color: #333333 !important;
}
/* Undo dark color for anything inside the Upload button itself */
[data-testid="stFileUploaderDropzone"] button,
[data-testid="stFileUploaderDropzone"] button * {
    color: #ffffff !important;
}
/* Upload button icon — color forced white via JS MutationObserver (CSS filter
   is blocked by BaseWeb inline styles; see components.html fixUploaderIcons) */
[data-testid="stFileUploaderDropzone"] button svg,
[data-testid="stFileUploader"] button svg {
    color: #ffffff !important;
}
[data-testid="stFileUploaderDropzone"] button svg path,
[data-testid="stFileUploader"] button svg path {
    fill: currentColor !important;
}
/* ── File uploader — all text dark since bg is white ── */
[data-testid="stFileUploader"] *:not(button):not(button *):not(svg):not(path) {
    color: #333333 !important;
}
[data-testid="stFileUploader"] button,
[data-testid="stFileUploader"] button * {
    color: #ffffff !important;
}
/* ── Alerts / Info ── */
[data-testid="stAlert"] {
    background-color: #2a0000 !important;
    border-left: 4px solid #ff2222 !important;
}
/* ── Sliders ── */
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {
    background-color: #cc1111 !important;
}
/* ── Checkboxes ── */
[data-testid="stCheckbox"] input:checked + div {
    background-color: #cc1111 !important;
    border-color: #cc1111 !important;
}
/* ── Plotly chart backgrounds ── */
.js-plotly-plot .plotly, .js-plotly-plot .plotly .main-svg {
    background: transparent !important;
}
/* ── Dividers ── */
hr { border-color: #2a1a1a !important; }
/* ── Scrollbars ── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0d0d14; }
::-webkit-scrollbar-thumb { background: #3a1a1a; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #cc1111; }
/* ── Multiselect tags ── */
[data-baseweb="tag"] {
    background-color: #2a0a0a !important;
    border: 1px solid #cc1111 !important;
}
[data-baseweb="tag"] span { color: #e8e8e8 !important; }
/* ── Tooltips (render outside stApp, need explicit rules) ── */
[data-baseweb="tooltip"],
[data-baseweb="tooltip"] div,
[role="tooltip"],
[role="tooltip"] div,
.stTooltipContent,
div[class*="tooltip"],
div[class*="Tooltip"] {
    background-color: #1a1a1a !important;
    background: #1a1a1a !important;
    color: #e8e8e8 !important;
    border: 1px solid #8b0000 !important;
    border-radius: 6px !important;
}
[data-baseweb="tooltip"] *,
[role="tooltip"] * {
    color: #e8e8e8 !important;
    background-color: transparent !important;
}
/* ── Dropdown popup — the popover renders outside stApp so needs its own rules ── */
[data-baseweb="popover"],
[data-baseweb="popover"] > div,
[data-baseweb="menu"],
[data-baseweb="menu"] > ul {
    background: #111111 !important;
    background-color: #111111 !important;
    border: 1px solid #8b0000 !important;
}
/* Option rows — default state (target li AND its inner div) */
li[role="option"],
li[role="option"] > div,
[data-baseweb="menu-item"],
[data-baseweb="menu-item"] > div {
    background: #111111 !important;
    background-color: #111111 !important;
    color: #e8e8e8 !important;
}
/* Hover — use both shorthand and longhand, target li AND inner div */
li[role="option"]:hover,
li[role="option"]:hover > div,
li[role="option"]:hover > div > div,
li[role="option"]:focus,
li[role="option"]:focus > div,
[data-baseweb="menu-item"]:hover,
[data-baseweb="menu-item"]:hover > div,
[data-baseweb="option"]:hover,
[data-baseweb="option"]:hover > div {
    background: #8b0000 !important;
    background-color: #8b0000 !important;
    color: #ffffff !important;
}
/* Currently selected / active option */
[aria-selected="true"][role="option"],
[aria-selected="true"][role="option"] > div,
[data-baseweb="popover"] [aria-selected="true"],
[data-baseweb="popover"] [aria-selected="true"] > div {
    background: #5a0000 !important;
    background-color: #5a0000 !important;
    color: #ff8888 !important;
}
/* Force text color throughout — placed AFTER background rules so it doesn't fight them */
[data-baseweb="popover"] *:not(li):not([role="option"]) {
    color: #e8e8e8 !important;
}
li[role="option"] * { color: inherit !important; }
</style>"""
    else:
        css = """
<style>
.stApp, [data-testid="stAppViewContainer"] {
    background-color: #f5f5f7 !important;
}
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
    background-color: #ffffff !important;
    border-right: 1px solid #e0e0e0 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #cc1111 !important;
}
button[kind="primary"], [data-testid="baseButton-primary"] {
    background-color: #cc1111 !important;
    color: #ffffff !important;
    border: none !important;
    font-weight: 700 !important;
}
button[kind="primary"]:hover { background-color: #ee2222 !important; }
[data-testid="stHeader"] { background-color: #ffffff !important; }
</style>"""
    st.markdown(css, unsafe_allow_html=True)

# ── Paths ─────────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent

# ── Supabase client ───────────────────────────────────────────────────────────
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
_sb = _sb_create_client(_SUPABASE_URL, _SUPABASE_KEY) if (_sb_create_client and _SUPABASE_URL and _SUPABASE_KEY) else None

# ── Auth helpers ──────────────────────────────────────────────────────────────
import hashlib, secrets as _secrets

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

def _verify_login(username: str, password: str) -> bool:
    if not _sb: return False
    try:
        rows = _sb.table("credentials").select("salt,password_hash").eq("username", username).execute().data
        if not rows: return False
        return _verify_password(password, rows[0]["salt"], rows[0]["password_hash"])
    except Exception:
        return False

def _register_user(username: str, password: str) -> bool:
    if not _sb: return False
    salt, hsh = _hash_password(password)
    try:
        _sb.table("credentials").insert({"username": username, "salt": salt, "password_hash": hsh}).execute()
        return True
    except Exception:
        return False

# ── Auth gate — must resolve before any other UI ──────────────────────────────
if "rf_user" not in st.session_state:
    st.session_state["rf_user"] = None

if st.session_state["rf_user"] is None:
    # ── Inject minimal dark theme for login page ──────────────────────────────
    st.markdown("""<style>
.stApp,[data-testid="stAppViewContainer"]{background:#08080d!important}
.stApp *{color:#e8e8e8!important}
input,textarea{background:#141420!important;color:#e8e8e8!important;border:1px solid #2a2a3a!important;border-radius:6px!important;caret-color:#e8e8e8!important}
input::placeholder,textarea::placeholder{color:#888!important;opacity:1!important}
input:-webkit-autofill,input:-webkit-autofill:hover,input:-webkit-autofill:focus{-webkit-text-fill-color:#e8e8e8!important;-webkit-box-shadow:0 0 0 1000px #141420 inset!important}
[data-baseweb="input"],[data-baseweb="input"]>div,[data-baseweb="base-input"]{background:#141420!important;color:#e8e8e8!important}
[data-baseweb="input"] input{background:#141420!important;color:#e8e8e8!important}
[data-testid="stTextInput"] input{background:#141420!important;color:#e8e8e8!important}
[data-testid="baseButton-primary"]{background:#cc1111!important;color:#fff!important;font-weight:700!important;border:none!important}
[data-testid="baseButton-secondary"]{background:#1a1a24!important;color:#e8e8e8!important;border:1px solid #3a2a2a!important}
[data-testid="stAlert"]{background:#2a0000!important;border-left:4px solid #ff2222!important}
[data-testid="stTabs"] [role="tab"]{color:#e8e8e8!important}
[data-testid="stTabs"] [role="tab"][aria-selected="true"]{color:#cc1111!important}
</style>""", unsafe_allow_html=True)

    # ── Login / Register UI ───────────────────────────────────────────────────
    _LOGO_SRC_LOGIN = _load_logo_b64("RaceFusion.jpg")
    if _LOGO_SRC_LOGIN:
        st.markdown(
            f'<img src="{_LOGO_SRC_LOGIN}" style="max-width:420px;width:55%;'
            f'margin:32px auto 8px auto;display:block;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🏁 RaceFusion")

    st.markdown("<div style='text-align:center;color:#888;margin-bottom:32px;'>RacePak Data Dashboard</div>",
                unsafe_allow_html=True)

    _auth_tab_login, _auth_tab_reg = st.tabs(["Log In", "Create Account"])

    with _auth_tab_login:
        st.markdown("### Log In")
        _li_user = st.text_input("Username", key="li_user", placeholder="your username")
        _li_pass = st.text_input("Password", type="password", key="li_pass", placeholder="••••••••")
        if st.button("Log In", type="primary", key="li_btn"):
            _u = _li_user.strip().lower()
            if _verify_login(_u, _li_pass):
                st.session_state["rf_user"] = _u
                st.rerun()
            elif _check_user_exists(_u):
                st.error("Incorrect password.")
            else:
                st.error("Username not found. Create an account on the right.")

    with _auth_tab_reg:
        st.markdown("### Create Account")
        _reg_user = st.text_input("Choose a username", key="reg_user", placeholder="lowercase, no spaces")
        _reg_pass = st.text_input("Choose a password", type="password", key="reg_pass", placeholder="••••••••")
        _reg_pass2 = st.text_input("Confirm password", type="password", key="reg_pass2", placeholder="••••••••")
        if st.button("Create Account", type="primary", key="reg_btn"):
            _u = _reg_user.strip().lower()
            if not _u:
                st.error("Username cannot be empty.")
            elif not re.match(r"^[a-z0-9_\-]{2,32}$", _u):
                st.error("Username must be 2–32 characters: letters, numbers, _ or - only.")
            elif _check_user_exists(_u):
                st.error("That username is taken. Please choose another.")
            elif len(_reg_pass) < 6:
                st.error("Password must be at least 6 characters.")
            elif _reg_pass != _reg_pass2:
                st.error("Passwords do not match.")
            else:
                if _register_user(_u, _reg_pass):
                    st.session_state["rf_user"] = _u
                    st.rerun()
                else:
                    st.error("Registration failed — Supabase may not be configured. Check your .env.")

    st.stop()   # block rest of app until logged in

# ── Logged in ─────────────────────────────────────────────────────────────────
_current_user: str = st.session_state["rf_user"]

# ── Session heartbeat — upsert last_seen on every page load ──────────────────
import sys as _sys
print(f"[RF-DEBUG] current_user={_current_user!r}  rf_user={st.session_state.get('rf_user')!r}", file=_sys.stderr, flush=True)
if _sb:
    try:
        _sb.table("sessions").upsert(
            {"username": _current_user, "last_seen": "now()"},
            on_conflict="username",
        ).execute()
        print(f"[RF-DEBUG] sessions upsert OK for {_current_user!r}", file=_sys.stderr, flush=True)
    except Exception as _sess_err:
        print(f"[RF-DEBUG] sessions upsert FAILED: {_sess_err}", file=_sys.stderr, flush=True)
else:
    print("[RF-DEBUG] _sb is None — Supabase not connected", file=_sys.stderr, flush=True)

# ── Upload session state — initialize once, never undefined ──────────────────
for _k, _v in {
    "active_run_id":  None,        # canonical active run filename
    "upload_gen":     0,           # incremented by Save & Close to reset form state
    "current_page":   "dashboard", # "dashboard" | "predictor"
}.items():
    if _k not in st.session_state:
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

# Distinct colors for overlaid traces (up to 12)
TRACE_COLORS = [
    "#EF553B",  # red
    "#00CC96",  # green
    "#636EFA",  # blue
    "#FFA15A",  # orange
    "#AB63FA",  # purple
    "#19D3F3",  # cyan
    "#FF6692",  # pink
    "#B6E880",  # lime
    "#FECB52",  # yellow
    "#FF97FF",  # magenta
    "#72B7B2",  # teal
    "#FF8C00",  # dark orange
]


# ── Config persistence ────────────────────────────────────────────────────────
def load_config() -> dict:
    if not _sb: return {}
    username = st.session_state.get("rf_user", "")
    try:
        rows = _sb.table("user_configs").select("config").eq("username", username).execute().data
        if rows:
            data = rows[0]["config"] or {}
            data.pop("anthropic_api_key", None)
            return data
    except Exception:
        pass
    return {}

def save_config(cfg: dict):
    if not _sb: return
    username = st.session_state.get("rf_user", "")
    safe = {k: v for k, v in cfg.items() if k != "anthropic_api_key"}
    try:
        _sb.table("user_configs").upsert(
            {"username": username, "config": safe, "updated_at": "now()"},
            on_conflict="username"
        ).execute()
    except Exception as e:
        st.warning(f"Config save failed: {e}")


# ── Channel rules / alert checking ───────────────────────────────────────────
def check_alerts(df: "pd.DataFrame", time_col: str, rules: dict) -> list[dict]:
    """
    Evaluate channel rules against the full run dataframe.
    Returns a list of alert dicts:
      {channel, rule_type ("max"|"min"), threshold, value, time_s}
    """
    alerts = []
    for ch, rule in rules.items():
        if ch not in df.columns:
            continue
        s = df[ch].dropna()
        if s.empty:
            continue
        if "max" in rule:
            peak_idx = s.idxmax()
            peak_val = s[peak_idx]
            if peak_val > rule["max"]:
                alerts.append({
                    "channel": ch,
                    "rule_type": "max",
                    "threshold": rule["max"],
                    "value": peak_val,
                    "time_s": float(df.loc[peak_idx, time_col]),
                })
        if "min" in rule:
            low_idx = s.idxmin()
            low_val = s[low_idx]
            if low_val < rule["min"]:
                alerts.append({
                    "channel": ch,
                    "rule_type": "min",
                    "threshold": rule["min"],
                    "value": low_val,
                    "time_s": float(df.loc[low_idx, time_col]),
                })
    return alerts


# ── Run record persistence ────────────────────────────────────────────────────
def load_run(csv_name: str) -> dict:
    if not _sb: return {}
    username = st.session_state.get("rf_user", "")
    try:
        rows = _sb.table("runs").select("run_data").eq("username", username).eq("csv_filename", csv_name).execute().data
        return rows[0]["run_data"] if rows else {}
    except Exception:
        return {}

def save_run(csv_name: str, record: dict):
    if not _sb: return
    username = st.session_state.get("rf_user", "")
    try:
        existing = _sb.table("runs").select("id").eq("username", username).eq("csv_filename", csv_name).execute().data
        if existing:
            _sb.table("runs").update({"run_data": record, "updated_at": "now()"}).eq("username", username).eq("csv_filename", csv_name).execute()
        else:
            _sb.table("runs").insert({"username": username, "csv_filename": csv_name, "run_data": record}).execute()
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


# ── CSV parser ────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Parsing data…")
def load_racepak_csv(file_bytes: bytes) -> pd.DataFrame:
    text = file_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()

    raw_headers = [h.strip() for h in lines[0].split(",")]
    if raw_headers and raw_headers[-1] == "":
        raw_headers = raw_headers[:-1]

    # De-duplicate column names
    seen: dict[str, int] = {}
    headers: list[str] = []
    for h in raw_headers:
        if h in seen:
            seen[h] += 1
            headers.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 1
            headers.append(h)

    records = []
    for line in lines[1:]:
        if not line.strip():
            continue
        vals = [v.strip() for v in line.split(",")]
        vals = vals[: len(headers)]
        while len(vals) < len(headers):
            vals.append("")
        records.append(vals)

    df = pd.DataFrame(records, columns=headers)
    for col in df.columns:
        series = df[col].replace({"-###": None, "": None})
        df[col] = pd.to_numeric(series, errors="coerce")
    return df

def get_time_col(df: pd.DataFrame) -> str:
    for candidate in ["Time", "Track Time", "time"]:
        if candidate in df.columns:
            return candidate
    return df.columns[0]


# ── Timeslip scanner ──────────────────────────────────────────────────────────
def scan_timeslip(image_bytes: bytes, media_type: str, api_key: str, car_number: str = "") -> dict:
    """Call Claude vision to extract timeslip fields."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(image_bytes).decode()

    car_hint = ""
    if car_number.strip():
        car_hint = (
            f"\n\nIMPORTANT: This timeslip image may contain data for multiple cars. "
            f"Extract ONLY the row or entry for car number \"{car_number.strip()}\". "
            f"Ignore any other car's data on the same slip."
        )

    prompt = f"""You are reading a drag racing timeslip. Extract every field you can see.
Return a JSON object with these keys (use null for anything not visible):
{{
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "track_name": "full track name as printed",
  "track_location": "City, State (or City, Country) — look for address or city/state text near the track name",
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
  "wind": "speed and direction as printed e.g. 14.25 SE" or null,
  "density_alt_ft": float or null,
  "issues": "any notes or issues printed on the slip" or null
}}
Many timeslips print weather conditions (temp, barometric pressure, humidity, wind, corrected/density altitude) — extract those too if present.
Return only the JSON object. No markdown, no explanation.{car_hint}"""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


# ── Geocoding ─────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Geocoding location…")
def geocode(location: str) -> tuple[float | None, float | None, str]:
    """
    Accepts:
      - "lat, lon"  e.g. "42.694, -88.059"  → used directly
      - Any city/place name string           → queried via Open-Meteo geocoding API
    Tries progressively simpler forms of the name until one hits.
    """
    location = location.strip()

    # ── Direct coordinates? ──────────────────────────────────────────────────
    coord_match = re.match(r"^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$", location)
    if coord_match:
        lat, lon = float(coord_match.group(1)), float(coord_match.group(2))
        return lat, lon, f"{lat:.4f}, {lon:.4f}"

    # ── Try progressively simpler search terms ───────────────────────────────
    # e.g. "Union Grove, WI" → try "Union Grove, WI", then "Union Grove WI", then "Union Grove"
    candidates = [location]
    if "," in location:
        # Replace comma+space with just space
        candidates.append(location.replace(", ", " ").replace(",", " "))
        # Try just the city part before the first comma
        candidates.append(location.split(",")[0].strip())

    url = "https://geocoding-api.open-meteo.com/v1/search"
    for candidate in candidates:
        try:
            r = requests.get(url, params={"name": candidate, "count": 1}, timeout=10)
            data = r.json()
            if data.get("results"):
                res = data["results"][0]
                label = f"{res.get('name','')}, {res.get('admin1','')}, {res.get('country','')}".strip(", ")
                return res["latitude"], res["longitude"], label
        except Exception:
            continue

    return None, None, location


# ── Historical weather ────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Fetching weather…")
def fetch_weather(lat: float, lon: float, date_str: str, hour: int = 12) -> dict:
    """Fetch hourly weather from Open-Meteo archive for a given date and hour."""
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date_str,
        "end_date": date_str,
        "hourly": "temperature_2m,relativehumidity_2m,surface_pressure,windspeed_10m,winddirection_10m",
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "timezone": "auto",
    }
    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    target = f"{date_str}T{hour:02d}:00"
    idx = times.index(target) if target in times else min(hour, len(times) - 1)

    def val(key):
        arr = hourly.get(key, [])
        return arr[idx] if idx < len(arr) else None

    return {
        "temperature_f": val("temperature_2m"),
        "humidity_pct":  val("relativehumidity_2m"),
        "pressure_hpa":  val("surface_pressure"),
        "windspeed_mph": val("windspeed_10m"),
        "wind_dir_deg":  val("winddirection_10m"),
    }

def wind_dir_label(deg: float | None) -> str:
    if deg is None:
        return "—"
    dirs = ["N","NE","E","SE","S","SW","W","NW"]
    return dirs[round(deg / 45) % 8]

def calc_density_altitude(temp_f: float | None, humidity_pct: float | None, pressure_hpa: float | None) -> float | None:
    """
    Density Altitude in feet — motorsports standard (airdensityonline.com / NHRA).

    Reference conditions: 29.92 inHg, dry air, sea level.
    Uses DRY AIR density only: rho = P_dry / (R_d × T_k)
    where P_dry = station_pressure − vapor_pressure.

    Water vapor displaces oxygen-bearing dry air; excluding the vapor density
    term captures this effect and matches the drag racing industry standard.
    The full-moist-air formula (adding vapor density) overstates air density
    and produces DA readings ~500 ft too low.

    DA = 145442.16 × (1 − (ρ_dry / 1.225)^0.234969)
    """
    if any(v is None for v in [temp_f, humidity_pct, pressure_hpa]):
        return None
    import math
    T_c   = (temp_f - 32) * 5 / 9
    T_k   = T_c + 273.15
    P_pa  = pressure_hpa * 100.0              # hPa → Pa
    RH    = humidity_pct / 100.0
    # Saturation vapor pressure (Magnus formula)
    e_s   = 610.78 * math.exp(17.27 * T_c / (T_c + 237.3))
    e_pa  = RH * e_s                          # actual vapor pressure (Pa)
    P_dry = P_pa - e_pa                       # dry-air partial pressure (Pa)
    # Dry-air density only — motorsports standard, matches airdensityonline.com
    rho   = P_dry / (287.058 * T_k)          # kg/m³
    rho_sl = 1.225                            # ISA sea-level density (kg/m³)
    return 145442.16 * (1 - (rho / rho_sl) ** 0.234969)  # feet


# ── Race Day Predictor helpers ────────────────────────────────────────────────

def fetch_current_weather(lat: float, lon: float) -> dict:
    """Fetch live conditions from Open-Meteo forecast API (not archive).

    Uses forecast API with real-time data assimilation (forecast_days=1)
    for better accuracy than ERA5 reanalysis on current conditions.
    Variable names match current Open-Meteo API convention with legacy fallbacks.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":         lat,
        "longitude":        lon,
        "current":          "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m",
        "forecast_days":    1,
        "temperature_unit": "fahrenheit",
        "windspeed_unit":   "mph",
        "timezone":         "auto",
    }
    r   = requests.get(url, params=params, timeout=15)
    cur = r.json().get("current", {})
    return {
        "temperature_f": cur.get("temperature_2m"),
        # Accept both current (relative_humidity_2m) and legacy (relativehumidity_2m) key names
        "humidity_pct":  cur.get("relative_humidity_2m") or cur.get("relativehumidity_2m"),
        "pressure_hpa":  cur.get("surface_pressure"),
        "windspeed_mph": cur.get("wind_speed_10m") or cur.get("windspeed_10m"),
    }


def _rdp_load_run_history(username: str) -> list[dict]:
    """Return all runs for username that have both a valid ET and a DA."""
    if not _sb:
        return []
    try:
        rows = _sb.table("runs").select("run_data,created_at").eq("username", username).execute().data
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
        da = slip.get("density_alt_ft") or wx.get("density_alt_ft")
        if da is None:
            da = calc_density_altitude(wx.get("temperature_f"), wx.get("humidity_pct"), wx.get("pressure_hpa"))
        if da is None:
            continue
        results.append({
            "date":  slip.get("date") or row.get("created_at", "")[:10],
            "track": slip.get("track_name") or slip.get("track_location") or "—",
            "et":    et,
            "da":    float(da),
        })
    return results


def _rdp_percentile(data: list, pct: float) -> float:
    if len(data) == 1:
        return data[0]
    k  = (len(data) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def _rdp_linear_regression(xs: list, ys: list):
    n = len(xs)
    if n < 2:
        return None, None
    sx  = sum(xs);  sy  = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None, None
    slope     = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def _rdp_r_squared(xs, ys, slope, intercept) -> float:
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return 1 - ss_res / ss_tot if ss_tot > 0 else 0.0


def detect_shift_points(df, time_col: str, rpm_col: str = "Engine RPM",
                        min_rpm_drop: int = 700, post_window_s: float = 0.45,
                        min_shift_rpm: int = 3200, debounce_s: float = 0.8) -> list[dict]:
    """
    Detect gear-shift events from the Engine RPM trace.

    A shift is a local RPM peak (higher than its neighbor) followed by a drop
    of at least `min_rpm_drop` RPM within `post_window_s` seconds.

    Returns a list of dicts: [{'gear': '1→2', 'time': t, 'rpm': r}, …]
    sorted by time.
    """
    if df is None or rpm_col not in df.columns or time_col not in df.columns:
        return []

    data = df[[time_col, rpm_col]].dropna().sort_values(time_col)
    data = data[data[time_col] >= 0.05]   # ignore pre-launch noise
    if len(data) < 20:
        return []

    times = data[time_col].values.astype(float)
    rpms  = data[rpm_col].values.astype(float)

    # Estimate sample interval and window size in samples
    dt  = float(times[-1] - times[0]) / max(len(times) - 1, 1)
    win = max(2, int(post_window_s / dt)) if dt > 0 else 10

    shifts: list[dict] = []
    last_t = -999.0

    for i in range(1, len(rpms) - win):
        r = rpms[i]
        if r < min_shift_rpm:
            continue
        # Must be a local peak (higher than both immediate neighbors)
        if r <= rpms[i - 1]:
            continue
        # RPM must drop enough in the following window
        future_min = float(rpms[i + 1: i + win + 1].min())
        if (r - future_min) < min_rpm_drop:
            continue
        t = float(times[i])
        # Debounce: within debounce window, keep whichever peak is higher
        if (t - last_t) < debounce_s:
            if shifts and r > shifts[-1]["rpm"]:
                shifts[-1] = {"time": round(t, 2), "rpm": int(r)}
                last_t = t
        else:
            shifts.append({"time": round(t, 2), "rpm": int(r)})
            last_t = t

    # Label each shift as gear change
    _labels = ["1→2", "2→3", "3→4", "4→5", "5→6"]
    for idx, s in enumerate(shifts):
        s["gear"] = _labels[idx] if idx < len(_labels) else f"Shift {idx + 1}"

    return shifts


def calc_rwhp(weight_lbs: float, et: float | None, mph: float | None) -> dict:
    """
    Estimate rear-wheel horsepower from timeslip data.
    From trap speed: RWHP = Weight × (MPH / 234)³   (Hale formula)
    From ET:         RWHP = Weight × (5.825 / ET)³
    Both are standard drag racing estimates — MPH-based is more accurate.
    """
    result = {}
    if mph and mph > 0:
        result["from_mph"] = weight_lbs * (mph / 234.0) ** 3
    if et and et > 0:
        result["from_et"] = weight_lbs * (5.825 / et) ** 3
    return result


# ── Load config once, before any sidebar widgets that need it ─────────────────
cfg = load_config()

# ── Theme toggle (must run before CSS injection) ──────────────────────────────
if "dark_mode" not in st.session_state:
    st.session_state["dark_mode"] = True
_dark_mode = st.session_state["dark_mode"]
_inject_theme(_dark_mode)

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
if _dark_mode:
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

st.sidebar.caption("RacePak Data Dashboard")

# ── User badge + logout ───────────────────────────────────────────────────────
_ub_col1, _ub_col2 = st.sidebar.columns([3, 2])
_ub_col1.markdown(f"👤 **{_current_user}**")
if _ub_col2.button("Log Out", key="logout_btn"):
    st.session_state["rf_user"] = None
    st.rerun()

_theme_col1, _theme_col2 = st.sidebar.columns([3, 2])
_theme_col1.caption("Theme")
if _theme_col2.button("☀️ Light" if _dark_mode else "🌑 Dark", key="theme_toggle"):
    st.session_state["dark_mode"] = not _dark_mode
    st.rerun()

_cur_page = st.session_state.get("current_page", "dashboard")
if _cur_page == "dashboard":
    if st.sidebar.button("🏁 Race Day Predictor", use_container_width=True, key="nav_to_predictor"):
        st.session_state["current_page"] = "predictor"
        st.rerun()
else:
    if st.sidebar.button("📊 Run Analysis", use_container_width=True, key="nav_to_dashboard"):
        st.session_state["current_page"] = "dashboard"
        st.rerun()

st.sidebar.markdown("---")

# ── Car Profile (static specs, saved to config) ───────────────────────────────
st.sidebar.markdown("### 🏎️ Car Profile")
car_number_input = st.sidebar.text_input(
    "Your car number",
    value=cfg.get("car_number", ""),
    placeholder="e.g. 1234",
    help="If the slip shows multiple cars, Claude will extract only yours",
)
if car_number_input.strip() and car_number_input.strip() != cfg.get("car_number", ""):
    cfg["car_number"] = car_number_input.strip()
    save_config(cfg)

with st.sidebar.expander("Car specs", expanded=False):
    st.caption("Fill in once — included in every AI analysis.")

    # ── Sanctioning Body & Class ──────────────────────────────────────────────
    st.markdown("**Sanctioning Body & Class**")
    _cp_sanction = st.text_input(
        "Sanctioning Body", value=cfg.get("sanctioning_body", ""),
        placeholder="e.g. NHRA, IHRA, NMCA, PDRA, local track",
        key="cp_sanction",
        help="The organization whose rulebook governs this car. Used by the AI to apply correct class rules and legal limits.",
    )
    _cp_class_name = st.text_input(
        "Class Name", value=cfg.get("class_name", ""),
        placeholder="e.g. Top Alcohol Dragster, Super Gas, Pro Mod, Bracket",
        key="cp_class_name",
        help="The exact class name. The AI will look up index, dial-in requirements, and rulebook restrictions for this class.",
    )

    # ── Engine & Fuel ─────────────────────────────────────────────────────────
    st.markdown("**Engine & Fuel**")
    _cp_eng  = st.text_input(
        "Engine", value=cfg.get("engine_desc", ""),
        placeholder="e.g. 540 BBC, 565 SBC", key="cp_eng",
    )
    _fuel_opts = ["", "Gasoline", "Methanol", "Nitromethane"]
    _cp_fuel = st.selectbox(
        "Fuel type", options=_fuel_opts, key="cp_fuel",
        index=_fuel_opts.index(cfg.get("fuel_type", ""))
              if cfg.get("fuel_type", "") in _fuel_opts else 0,
    )
    _cp_carb = st.text_input(
        "Carburetor / Fuel system", value=cfg.get("carb_desc", ""),
        placeholder="e.g. 2× 1050 Dominator / EFI", key="cp_carb",
    )

    # ── Blower ────────────────────────────────────────────────────────────────
    st.markdown("**Blower**")
    _cp_bl_col1, _cp_bl_col2 = st.columns(2)
    _cp_blower_type  = _cp_bl_col1.selectbox(
        "Type", options=["", "Roots", "Screw"], key="cp_blower_type",
        index=["", "Roots", "Screw"].index(cfg.get("blower_type", ""))
              if cfg.get("blower_type", "") in ["", "Roots", "Screw"] else 0,
    )
    _cp_blower_style = _cp_bl_col2.selectbox(
        "Style", options=["", "Standard", "Hi-Helix"], key="cp_blower_style",
        index=["", "Standard", "Hi-Helix"].index(cfg.get("blower_style", ""))
              if cfg.get("blower_style", "") in ["", "Standard", "Hi-Helix"] else 0,
    )
    _cp_blower_size  = st.text_input(
        "Blower size", value=cfg.get("blower_size", ""),
        placeholder="e.g. 14-71, 10-71, 8-71", key="cp_blower_size",
    )

    # ── Drivetrain ────────────────────────────────────────────────────────────
    st.markdown("**Drivetrain**")
    _cp_conv  = st.text_input(
        "Torque Converter", value=cfg.get("converter_desc", ""),
        placeholder="e.g. Neal Chance 3600 stall, lock-up", key="cp_conv",
    )
    _cp_trans = st.text_input(
        "Transmission", value=cfg.get("transmission", ""),
        placeholder="e.g. Powerglide, TH400, Jerico 4-speed", key="cp_trans",
    )
    _cp_num_gears = st.number_input(
        "Number of forward gears", min_value=1, max_value=6,
        value=int(cfg.get("num_gears", 2)), step=1, key="cp_num_gears",
    )
    _cp_gear_ratios = {}
    _gear_cols = st.columns(min(int(_cp_num_gears), 3))
    _gear_labels = ["1st", "2nd", "3rd", "4th", "5th", "6th"]
    _saved_ratios = cfg.get("gear_ratios", {})
    for _gi in range(int(_cp_num_gears)):
        _col = _gear_cols[_gi % len(_gear_cols)]
        _cp_gear_ratios[str(_gi + 1)] = _col.text_input(
            _gear_labels[_gi], value=_saved_ratios.get(str(_gi + 1), ""),
            placeholder="e.g. 1.76", key=f"cp_gear_{_gi+1}",
        )
    _cp_rear_ratio = st.text_input(
        "Rear end ratio", value=cfg.get("rear_gear_ratio", ""),
        placeholder="e.g. 4.11", key="cp_rear_ratio",
    )

    # ── Suspension, Tires & Weight ────────────────────────────────────────────
    st.markdown("**Suspension, Tires & Weight**")
    _susp_opts = ["", "Hardtail", "Shocks / Leaf", "Shocks / Coilover", "Four-link"]
    _cp_susp = st.selectbox(
        "Suspension type", options=_susp_opts, key="cp_susp",
        index=_susp_opts.index(cfg.get("suspension_type", ""))
              if cfg.get("suspension_type", "") in _susp_opts else 0,
    )
    _cp_tire = st.text_input(
        "Rear tire size", value=cfg.get("tire_size", ""),
        placeholder="e.g. 33×10.5 ET, 275/60-15", key="cp_tire",
    )
    _cp_weight = st.number_input(
        "Car weight with driver (lbs)", min_value=500, max_value=10000,
        value=int(cfg.get("car_weight_lbs", 3200)), step=50, key="cp_weight",
        help="Used for G-force cross-check and RWHP estimate.",
    )

    _cp_notes = st.text_area(
        "Additional notes", value=cfg.get("car_notes", ""),
        placeholder="Cam specs, injector size, anything else the AI tuner should know…",
        height=70, key="cp_notes",
    )

    _cp_vals = {
        "sanctioning_body": _cp_sanction,
        "class_name":       _cp_class_name,
        "engine_desc":    _cp_eng,
        "fuel_type":      _cp_fuel,
        "carb_desc":      _cp_carb,
        "blower_type":    _cp_blower_type,
        "blower_style":   _cp_blower_style,
        "blower_size":    _cp_blower_size,
        "converter_desc": _cp_conv,
        "transmission":   _cp_trans,
        "num_gears":      int(_cp_num_gears),
        "gear_ratios":    _cp_gear_ratios,
        "rear_gear_ratio":_cp_rear_ratio,
        "suspension_type":_cp_susp,
        "tire_size":      _cp_tire,
        "car_weight_lbs": int(_cp_weight),
        "car_notes":      _cp_notes,
    }
    _car_profile_changed = any(cfg.get(k) != v for k, v in _cp_vals.items())
    if _car_profile_changed:
        cfg.update(_cp_vals)
        save_config(cfg)

# weight_input still needed elsewhere (RWHP display, AI payload)
weight_input = int(cfg.get("car_weight_lbs", 3200))

st.sidebar.markdown("---")

# ── Run Manager ───────────────────────────────────────────────────────────────
st.sidebar.markdown("### 🗂️ Run Manager")

_saved_runs = list_saved_runs()
import sys
print(f'[RF-DEBUG] RENDER START: active_run_id={st.session_state.get("active_run_id")!r}, run_selector={st.session_state.get("run_selector")!r}, _run_selector_idx={st.session_state.get("_run_selector_idx")!r}, saved_runs_count={len(_saved_runs)}, qp_run={st.query_params.get("run")!r}', file=sys.stderr, flush=True)
_qp_run = st.query_params.get("run")
if _qp_run and not st.session_state.get("active_run_id"):
    st.session_state["active_run_id"] = _qp_run
_NEW_RUN = "⊕  New run…"

def _delete_run_files(csv_filename: str):
    """Delete all data associated with a run from Supabase."""
    if not _sb: return
    username = st.session_state.get("rf_user", "")
    _key = _get_slip_storage_key(csv_filename)
    if _key:
        _delete_slip_from_storage(_key)
    try:
        _sb.table("runs").delete().eq("username", username).eq("csv_filename", csv_filename).execute()
    except Exception as e:
        st.warning(f"Delete failed: {e}")

# Reset selector after delete
if st.session_state.get("_reset_selector"):
    st.session_state.pop("_run_selector_idx", None)
    st.session_state.pop("run_selector", None)
    st.session_state["active_run_id"] = None
    st.query_params.pop("run", None)
    st.session_state["_reset_selector"] = False

# Show post-delete success messages (visible for one render cycle)
if st.session_state.pop("_delete_success", False):
    st.sidebar.success("✅ Run deleted.")
if st.session_state.pop("_delete_all_success", False):
    st.sidebar.success("✅ All runs deleted.")

# ── active_run_id: single source of truth for which run is active ─────────────
# File uploads set this. It is only cleared by explicit user action (delete or
# picking "New run" from the dropdown). A rerun caused by a file upload must
# NEVER clear it — that is what was causing the screen to go blank.
#
# on_change fires when the USER moves the dropdown; file-upload reruns do not
# fire on_change. We use this to distinguish the two cases.
def _on_run_selector_change():
    st.session_state["_user_changed_run"] = True

_user_changed_run = st.session_state.pop("_user_changed_run", False)
_active_run_id = st.session_state.get("active_run_id")

if _active_run_id and not _user_changed_run:
    # File-upload rerun — force the selectbox back to the active run.
    # (Without this, Streamlit can silently reset the selectbox to index 0.)
    _sync_idx = 0
    for _i, _r in enumerate(_saved_runs):
        if _r["filename"] == _active_run_id:
            _sync_idx = _i + 1
            break
    if _sync_idx > 0:
        # Only pop `run_selector` when the index genuinely needs to change —
        # popping on every render can spuriously fire the on_change callback.
        if st.session_state.get("_run_selector_idx") != _sync_idx:
            st.session_state["_run_selector_idx"] = _sync_idx
            st.session_state.pop("run_selector", None)   # force index param to take effect
    elif _saved_runs:
        # _saved_runs is non-empty but doesn't contain this run → deleted externally
        st.session_state["active_run_id"] = None
        st.query_params.pop("run", None)
    # If _saved_runs is empty, Supabase may have failed — preserve active_run_id

if "_restore_run_selector" in st.session_state:
    _pending_idx = st.session_state.pop("_restore_run_selector")
    # Only apply if active_run_id still matches what we were restoring
    _current_aid = st.session_state.get("active_run_id")
    if _current_aid and _pending_idx > 0:
        _expected_name = _saved_runs[_pending_idx - 1]["filename"] if _pending_idx <= len(_saved_runs) else None
        if _expected_name == _current_aid:
            st.session_state["run_selector"] = _pending_idx

_run_options  = [_NEW_RUN] + [r["label"] for r in _saved_runs]
_safe_sel_idx = min(
    st.session_state.get("_run_selector_idx", 0),
    max(0, len(_run_options) - 1)
)
_sel_idx_raw  = st.sidebar.selectbox(
    "Select run",
    options=range(len(_run_options)),
    format_func=lambda i: _run_options[i],
    index=_safe_sel_idx,
    key="run_selector",
    help="Pick a saved run or upload a new one",
    on_change=_on_run_selector_change,
)
st.session_state["_run_selector_idx"] = _sel_idx_raw

# Prevent IndexError when _saved_runs is empty or shorter than selectbox expects
if _sel_idx_raw > 0 and (_sel_idx_raw - 1) >= len(_saved_runs):
    _sel_idx_raw = 0
    st.session_state["run_selector"] = 0
    st.session_state["_run_selector_idx"] = 0

# Guard against false-positive "user changed run" caused by widget reset after st.rerun()
if _user_changed_run and _sel_idx_raw == 0:
    _aid = st.session_state.get("active_run_id")
    _qp  = st.query_params.get("run")
    if _aid and _qp == _aid:
        # active_run_id and query params both confirm a run is active —
        # the selectbox reset to 0 due to st.rerun(), not user action. Ignore it.
        _user_changed_run = False
        _true_idx = next(
            (i + 1 for i, r in enumerate(_saved_runs) if r["filename"] == _aid), 0
        )
        if _true_idx > 0:
            st.session_state["_restore_run_selector"] = _true_idx
            st.session_state["_run_selector_idx"] = _true_idx
            _sel_idx_raw = _true_idx

# Set active run state and keep active_run_id in sync with the selectbox.
# NOTE: _active_csv_bytes is NOT loaded here — it's loaded after the processing
# zone, once we know the final active run. Loading it here would use stale data
# if an upload is being processed on this render.
if _sel_idx_raw == 0:
    _active_csv_name  = None
    _active_has_csv   = False
    # Only clear active_run_id when the user deliberately chose "New run"
    if _user_changed_run:
        st.session_state["active_run_id"] = None
        st.query_params.pop("run", None)
else:
    _sel_run_meta     = _saved_runs[_sel_idx_raw - 1]
    _active_csv_name  = _sel_run_meta["filename"]
    _active_has_csv   = _sel_run_meta["has_csv"]
    st.session_state["active_run_id"] = _active_csv_name   # always keep in sync
    st.query_params["run"] = _active_csv_name
    # Delete button
    if st.sidebar.button("🗑️ Delete this run", key="delete_run_btn", type="primary",
                         use_container_width=True):
        _delete_run_files(_active_csv_name)
        st.session_state["_reset_selector"] = True
        st.session_state["_delete_success"] = True
        st.rerun()

# Delete ALL runs — available regardless of selection
# _delete_all_open drives the expander so we can collapse it after deletion
with st.sidebar.expander("🗑️ Delete ALL runs",
                         expanded=st.session_state.pop("_delete_all_open", False)):
    st.caption("Permanently removes every saved run, CSV, and timeslip image.")
    _confirm_all = st.checkbox("Yes, delete everything", key="confirm_delete_all")
    if st.button("Delete all runs", disabled=not _confirm_all, type="primary", key="delete_all_btn"):
        _all_for_delete = list_saved_runs()
        for _r in _all_for_delete:
            _k = _r["record"].get("timeslip_storage_key")
            if _k:
                _delete_slip_from_storage(_k)
        if _sb:
            try:
                _sb.table("runs").delete().eq("username", _current_user).execute()
            except Exception as e:
                st.warning(f"Delete all failed: {e}")
        # Reset checkbox, collapse expander, flag success message
        st.session_state.pop("confirm_delete_all", None)
        st.session_state["_reset_selector"] = True
        st.session_state["_delete_all_success"] = True
        # _delete_all_open intentionally not set → expander defaults to collapsed
        st.rerun()

st.sidebar.markdown("---")

# ── RacePak Data ──────────────────────────────────────────────────────────────
_rp_hdr_col, _rp_help_col = st.sidebar.columns([5, 1])
_rp_hdr_col.markdown("### 📂 RacePak Data")
with _rp_help_col:
    if st.button("❓", key="racepak_help_btn", help="How to export from DataLink II"):
        st.session_state["_show_racepak_help"] = not st.session_state.get("_show_racepak_help", False)

if st.session_state.get("_show_racepak_help", False):
    with st.sidebar.expander("📋 How to Export Your RacePak Data", expanded=True):
        st.markdown("""
**How to Export Your RacePak Data**

1. Open your run in DataLink II software
2. Make all channels active
3. Go to **File** and select **Print/Save ASCII File**
4. In the dialog that opens, set the following:
   - **Column Delimiter:** Comma
   - **New Page Every:** 0 Lines
   - **Sampling Interval:** 0.02
   - Leave **"Directly Print in ASCII (no preview)"** unchecked
5. Click OK and save the file
6. Return to RaceFusion and upload the saved file in the **RacePak Data** section
""")

if _sel_idx_raw == 0:   # "New run…"
    st.sidebar.caption("📋 Use the **Create New Run** form in the main area →")
elif _active_has_csv:
    st.sidebar.caption(f"✅ Loaded: {_active_csv_name}")
elif _active_csv_name and _active_csv_name.endswith(".run"):
    # Timeslip-only run — auto-process CSV as soon as one is selected
    _csv_up_key    = f"_add_csv_up_{_active_csv_name}"
    _csv_saved_key = f"_csv_saved_{_active_csv_name}"
    _add_csv_file  = st.sidebar.file_uploader(
        "Add RacePak CSV to this run",
        type=["csv"],
        key=_csv_up_key,
        help="Attach a RacePak CSV to combine with your timeslip data",
    )
    if _add_csv_file is not None and not st.session_state.get(_csv_saved_key):
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
_ts_hdr_col, _ts_help_col = st.sidebar.columns([5, 1])
_ts_hdr_col.markdown("### 🎫 Timeslip Scanner")
with _ts_help_col:
    if st.button("❓", key="timeslip_help_btn", help="How to scan your timeslip"):
        st.session_state["_show_timeslip_help"] = not st.session_state.get("_show_timeslip_help", False)

if st.session_state.get("_show_timeslip_help", False):
    with st.sidebar.expander("📋 How to Scan Your Timeslip", expanded=True):
        st.markdown("""
**How to Scan Your Timeslip**

1. Take a clear photo of your timeslip with your phone
2. Transfer the photo to your computer
3. Upload the photo in the **Timeslip Scanner** section
""")
api_key = os.environ.get("ANTHROPIC_API_KEY", "")
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

                    # 2. Scan inline — same pattern as Create New Run form
                    if api_key:
                        _add_slip_status.write("🎫 Scanning timeslip…")
                        try:
                            _scan_result = scan_timeslip(_sl_bytes, _sl_mime, api_key, car_number_input)
                            _existing_run["timeslip"] = _scan_result

                            # 3. Fetch weather
                            _slip_date = _scan_result.get("date")
                            if _slip_date:
                                _slip_hour = 12
                                if _scan_result.get("time"):
                                    try:
                                        _slip_hour = int(str(_scan_result["time"]).split(":")[0])
                                    except Exception:
                                        _slip_hour = 12
                                _wx_lat, _wx_lon, _wx_label = None, None, ""
                                _as_track = (_scan_result.get("track_location")
                                             or _scan_result.get("track_name") or "")
                                if _as_track:
                                    _add_slip_status.write(f"📍 Geocoding {_as_track}…")
                                    _wx_lat, _wx_lon, _wx_label = geocode(_as_track)
                                if _wx_lat is None and cfg.get("lat"):
                                    _wx_lat  = cfg["lat"]
                                    _wx_lon  = cfg["lon"]
                                    _wx_label = cfg.get("location_label", "")
                                if _wx_lat is not None:
                                    _add_slip_status.write("🌤️ Fetching weather…")
                                    try:
                                        _wx = fetch_weather(_wx_lat, _wx_lon, _slip_date, _slip_hour)
                                        _da = calc_density_altitude(
                                            _wx.get("temperature_f"),
                                            _wx.get("humidity_pct"),
                                            _wx.get("pressure_hpa"),
                                        )
                                        if _da is not None:
                                            _wx["density_alt_ft"] = round(_da)
                                        _existing_run["weather"]          = _wx
                                        _existing_run["weather_date"]     = _slip_date
                                        _existing_run["weather_location"] = _wx_label
                                    except Exception as _wx_e:
                                        _add_slip_status.write(f"⚠️ Weather unavailable: {_wx_e}")
                        except Exception as _scan_e:
                            _add_slip_status.write(f"⚠️ Scan failed: {_scan_e}")

                    save_run(_active_csv_name, _existing_run)
                    st.session_state["active_run_id"] = _active_csv_name
                    st.query_params["run"] = _active_csv_name
                    st.session_state[_slip_saved_key] = True
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
            st.rerun()
        if _del_slip_col.button("🗑️ Delete", key="del_slip_btn", use_container_width=True):
            _delete_slip_from_storage(_existing_slip_key)
            _existing_run.pop("timeslip", None)
            _existing_run.pop("weather", None)
            _existing_run.pop("timeslip_storage_key", None)
            save_run(_active_csv_name, _existing_run)
            st.rerun()
        st.sidebar.caption(f"✅ Timeslip on file: {_existing_slip_key.split('/')[-1]}")

st.sidebar.markdown("---")

# ── Location
st.sidebar.markdown("### 🌤️ Track Location")
location_input = st.sidebar.text_input(
    "City or track name",
    value=cfg.get("location_name", ""),
    placeholder="e.g. Union Grove WI",
    help="Enter city name (no comma), or paste lat/lon coordinates like 42.694, -88.059",
)
if st.sidebar.button("Save location"):
    if location_input.strip():
        lat, lon, label = geocode(location_input.strip())
        if lat:
            cfg["location_name"] = location_input.strip()
            cfg["location_label"] = label
            cfg["lat"] = lat
            cfg["lon"] = lon
            save_config(cfg)
            st.sidebar.success(f"Saved: {label}")
        else:
            st.sidebar.error("Location not found — try a different name.")

if cfg.get("location_label"):
    st.sidebar.caption(f"📍 {cfg['location_label']}")

# ── Admin Panel (weeber70 only) ───────────────────────────────────────────────
_admin_user = st.session_state.get("rf_user", "")
print(f"[RF-DEBUG] admin check: rf_user={_admin_user!r}  is_admin={_admin_user == 'weeber70'}", file=_sys.stderr, flush=True)
if _admin_user == "weeber70" and _sb:
    st.sidebar.markdown("---")
    with st.sidebar.expander("🔒 Admin Panel", expanded=False):

        def _time_ago(ts_str: str) -> str:
            if not ts_str:
                return "never"
            try:
                from datetime import datetime, timezone
                _ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                _s  = int((datetime.now(timezone.utc) - _ts).total_seconds())
                if _s < 60:    return f"{_s}s ago"
                if _s < 3600:  return f"{_s // 60}m ago"
                if _s < 86400: return f"{_s // 3600}h ago"
                return f"{_s // 86400}d ago"
            except Exception:
                return ts_str

        try:
            from datetime import datetime, timezone, timedelta

            _ten_ago      = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            _active_rows  = _sb.table("sessions").select("username").gte("last_seen", _ten_ago).execute().data
            _active_count = len(_active_rows)

            _cred_res    = _sb.table("credentials").select("username", count="exact").execute()
            _total_users = _cred_res.count or len(_cred_res.data)

            _runs_res    = _sb.table("runs").select("username", count="exact").execute()
            _total_runs  = _runs_res.count or len(_runs_res.data)

            try:
                _slip_res   = _sb.table("runs").select("id", count="exact").not_.is_("run_data->>timeslip_storage_key", "null").execute()
                _total_slip = _slip_res.count or 0
            except Exception:
                _total_slip = "—"

            _a1, _a2 = st.columns(2)
            _a1.metric("Active now",        _active_count)
            _a2.metric("Accounts",          _total_users)
            _b1, _b2 = st.columns(2)
            _b1.metric("Runs logged",       _total_runs)
            _b2.metric("Timeslips scanned", _total_slip)

            st.markdown("---")

            _all_creds    = _sb.table("credentials").select("username").execute().data
            _all_sessions = {r["username"]: r["last_seen"]
                             for r in _sb.table("sessions").select("username,last_seen").execute().data}
            _run_rows     = _runs_res.data or _sb.table("runs").select("username").execute().data
            _run_counts   = {}
            for _rr in _run_rows:
                _u = _rr.get("username", "")
                _run_counts[_u] = _run_counts.get(_u, 0) + 1

            _rows_html = ""
            for _cu in sorted(_all_creds, key=lambda x: x["username"]):
                _un  = _cu["username"]
                _ls  = _time_ago(_all_sessions.get(_un, ""))
                _rc  = _run_counts.get(_un, 0)
                _bold = "font-weight:700;" if _un == "weeber70" else ""
                _rows_html += (
                    f'<tr>'
                    f'<td style="color:#ccc;{_bold}padding:3px 6px 3px 0;">{_un}</td>'
                    f'<td style="color:#888;padding:3px 6px;">{_ls}</td>'
                    f'<td style="color:#cc1111;text-align:right;padding:3px 0;">{_rc}</td>'
                    f'</tr>'
                )

            st.markdown(f"""
<div style="font-size:0.82rem;font-family:monospace;">
<table style="width:100%;border-collapse:collapse;">
<thead><tr>
  <th style="color:#666;text-align:left;padding:2px 6px 4px 0;border-bottom:1px solid #2a2a3a;">User</th>
  <th style="color:#666;text-align:left;padding:2px 6px 4px;border-bottom:1px solid #2a2a3a;">Last seen</th>
  <th style="color:#666;text-align:right;padding:2px 0 4px;border-bottom:1px solid #2a2a3a;">Runs</th>
</tr></thead>
<tbody>{_rows_html}</tbody>
</table>
</div>""", unsafe_allow_html=True)

        except Exception as _admin_err:
            st.warning(f"Admin data unavailable: {_admin_err}")

# ── Race Day Predictor page ───────────────────────────────────────────────────
if st.session_state.get("current_page") == "predictor":
    import urllib.parse as _rdp_urlparse

    st.markdown("# 🏁 Race Day Predictor")
    st.markdown(
        "<p style='color:#888;margin-top:-12px;'>Predicted ET and suggested dial based on your car's history + today's air.</p>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ── Current Conditions ────────────────────────────────────────────────────
    st.markdown("## 🌤️ Current Conditions")
    _rdp_cfg        = cfg   # cfg already loaded above
    _rdp_lat        = _rdp_cfg.get("lat")
    _rdp_lon        = _rdp_cfg.get("lon")
    _rdp_loc_label  = _rdp_cfg.get("location_label", "") or _rdp_cfg.get("location_name", "")

    if not _rdp_lat or not _rdp_lon:
        st.warning("No track location set. Go to Track Location in the sidebar and save your location.")
    else:
        st.caption(f"📍 {_rdp_loc_label}")

        if "rdp_weather" not in st.session_state:
            st.session_state["rdp_weather"] = None

        if st.button("🔄 Refresh Weather", type="secondary", key="rdp_refresh"):
            st.session_state["rdp_weather"] = None

        if st.session_state["rdp_weather"] is None:
            with st.spinner("Fetching current conditions…"):
                try:
                    st.session_state["rdp_weather"] = fetch_current_weather(float(_rdp_lat), float(_rdp_lon))
                except Exception as _rdp_wx_err:
                    st.error(f"Weather fetch failed: {_rdp_wx_err}")
                    st.session_state["rdp_weather"] = {}

        _rdp_wx  = st.session_state["rdp_weather"] or {}
        _rdp_da  = calc_density_altitude(_rdp_wx.get("temperature_f"), _rdp_wx.get("humidity_pct"), _rdp_wx.get("pressure_hpa"))

        _rc1, _rc2, _rc3, _rc4 = st.columns(4)
        _rc1.metric("🌡️ Temperature",  f"{_rdp_wx['temperature_f']:.1f} °F"            if _rdp_wx.get("temperature_f") is not None else "—")
        _rc2.metric("💧 Humidity",      f"{_rdp_wx['humidity_pct']:.0f}%"               if _rdp_wx.get("humidity_pct")  is not None else "—")
        _rc3.metric("📊 Baro Pressure", f"{_rdp_wx['pressure_hpa'] * 0.02953:.2f} inHg" if _rdp_wx.get("pressure_hpa") is not None else "—")
        _rc4.metric("📐 Density Alt",   f"{_rdp_da:,.0f} ft"                             if _rdp_da is not None else "—")

        st.markdown("---")

        # ── ET Prediction ─────────────────────────────────────────────────────
        st.markdown("## 🎯 ET Prediction")

        if _rdp_da is None:
            st.warning("Cannot compute DA — check that weather data loaded correctly.")
        else:
            _rdp_history = _rdp_load_run_history(_current_user)

            if not _rdp_history:
                st.info("No historical runs with both ET and DA found. Log runs with timeslips to enable predictions.")
            else:
                # IQR outlier detection
                _rdp_all_ets = sorted(r["et"] for r in _rdp_history)
                _rdp_n       = len(_rdp_all_ets)
                _rdp_q1      = _rdp_percentile(_rdp_all_ets, 25)
                _rdp_q3      = _rdp_percentile(_rdp_all_ets, 75)
                _rdp_iqr     = _rdp_q3 - _rdp_q1
                _rdp_lo      = _rdp_q1 - 1.5 * _rdp_iqr
                _rdp_hi      = _rdp_q3 + 1.5 * _rdp_iqr
                _rdp_mean_et = sum(_rdp_all_ets) / _rdp_n

                _rdp_included = []
                _rdp_excluded = []
                for _rdp_r in _rdp_history:
                    if _rdp_r["et"] < _rdp_lo or _rdp_r["et"] > _rdp_hi:
                        _rdp_excluded.append({**_rdp_r, "status": "excluded — outlier (IQR method)"})
                    else:
                        _rdp_included.append({**_rdp_r, "status": "included"})

                _rdp_n_incl = len(_rdp_included)
                if _rdp_n_incl < 2:
                    st.warning("Not enough clean runs for regression (need at least 2 after outlier removal).")
                else:
                    _rdp_xs = [r["da"] for r in _rdp_included]
                    _rdp_ys = [r["et"] for r in _rdp_included]
                    _rdp_slope, _rdp_intercept = _rdp_linear_regression(_rdp_xs, _rdp_ys)

                    if _rdp_slope is None:
                        st.error("Regression failed — all runs may have identical DA values.")
                    else:
                        _rdp_r2       = _rdp_r_squared(_rdp_xs, _rdp_ys, _rdp_slope, _rdp_intercept)
                        _rdp_pred_et  = _rdp_slope * _rdp_da + _rdp_intercept
                        _rdp_dial     = _rdp_pred_et + 0.02

                        if _rdp_n_incl < 5:
                            _rdp_conf_label  = "⚠️ Low confidence"
                            _rdp_conf_detail = "— log more runs for accurate predictions"
                            _rdp_conf_color  = "#cc8800"
                        elif _rdp_n_incl < 15:
                            _rdp_conf_label  = "🟡 Moderate confidence"
                            _rdp_conf_detail = f"— based on {_rdp_n_incl} runs"
                            _rdp_conf_color  = "#ccaa00"
                        else:
                            _rdp_conf_label  = "🟢 High confidence"
                            _rdp_conf_detail = f"— based on {_rdp_n_incl} runs"
                            _rdp_conf_color  = "#22aa55"

                        _rp1, _rp2, _rp3 = st.columns(3)
                        _rp1.metric("Predicted ET",      f"{_rdp_pred_et:.3f} s")
                        _rp2.metric("Suggested Dial",    f"{_rdp_dial:.3f} s", help="+0.02 s buffer to help avoid breakout")
                        _rp3.metric("Today's DA",        f"{_rdp_da:,.0f} ft")

                        st.markdown(
                            f"<div style='margin-top:4px;font-size:0.9rem;'>"
                            f"<span style='color:{_rdp_conf_color};font-weight:700;'>{_rdp_conf_label}</span>"
                            f"<span style='color:#888;'> {_rdp_conf_detail} &nbsp;·&nbsp; R² = {_rdp_r2:.3f}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        if _rdp_excluded:
                            st.markdown(
                                f"<p style='color:#888;font-size:0.82rem;margin-top:12px;'>"
                                f"⚠️ {len(_rdp_excluded)} run(s) excluded as outliers "
                                f"(IQR fences: {_rdp_lo:.3f}s – {_rdp_hi:.3f}s).</p>",
                                unsafe_allow_html=True,
                            )

                st.markdown("---")

                # ── Run History Table ─────────────────────────────────────────
                st.markdown("## 📋 Run History Used in Prediction")
                _rdp_display = []
                for _rdp_r in _rdp_included:
                    _rdp_display.append({**_rdp_r, "status": "✅ Included"})
                for _rdp_r in _rdp_excluded:
                    _rdp_display.append(_rdp_r)
                _rdp_display.sort(key=lambda x: x["date"], reverse=True)

                _rdp_rows_html = ""
                for _rdp_row in _rdp_display:
                    _is_ex   = _rdp_row["status"].startswith("excluded")
                    _rc      = "#555" if _is_ex else "#ddd"
                    _sc      = "#888" if _is_ex else "#4caf50"
                    _op      = "0.55" if _is_ex else "1"
                    _st_lbl  = f"❌ {_rdp_row['status']}" if _is_ex else "✅ Included"
                    _rdp_rows_html += (
                        f"<tr style='opacity:{_op};'>"
                        f"<td style='padding:5px 10px 5px 0;color:{_rc};'>{_rdp_row['date']}</td>"
                        f"<td style='padding:5px 10px;color:{_rc};'>{_rdp_row['track']}</td>"
                        f"<td style='padding:5px 10px;color:{_rc};text-align:right;'>{_rdp_row['et']:.3f}</td>"
                        f"<td style='padding:5px 10px;color:{_rc};text-align:right;'>{_rdp_row['da']:,.0f}</td>"
                        f"<td style='padding:5px 0;color:{_sc};font-size:0.85rem;'>{_st_lbl}</td>"
                        f"</tr>"
                    )
                st.markdown(f"""
<div style="border:1px solid #1e1e2a;border-radius:10px;padding:16px 20px;background:#0a0a14;overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:0.9rem;font-family:monospace;">
<thead><tr style="border-bottom:1px solid #2a2a3a;">
  <th style="color:#666;text-align:left;padding:4px 10px 8px 0;">Date</th>
  <th style="color:#666;text-align:left;padding:4px 10px 8px;">Track</th>
  <th style="color:#666;text-align:right;padding:4px 10px 8px;">ET (s)</th>
  <th style="color:#666;text-align:right;padding:4px 10px 8px;">DA (ft)</th>
  <th style="color:#666;text-align:left;padding:4px 0 8px;">Status</th>
</tr></thead>
<tbody>{_rdp_rows_html}</tbody>
</table>
</div>""", unsafe_allow_html=True)
                st.markdown(
                    f"<p style='color:#555;font-size:0.8rem;margin-top:8px;'>"
                    f"{_rdp_n_incl} runs included · {len(_rdp_excluded)} excluded · "
                    f"Q1 {_rdp_q1:.3f}s · Q3 {_rdp_q3:.3f}s · "
                    f"fences {_rdp_lo:.3f}–{_rdp_hi:.3f}s</p>",
                    unsafe_allow_html=True,
                )

    st.stop()  # Don't render the dashboard when on predictor page

# ── Main area ─────────────────────────────────────────────────────────────────
print(f'[RF-DEBUG] MAIN CHECK: active_run_id={st.session_state.get("active_run_id")!r}, _sel_idx_raw={_sel_idx_raw}, _user_changed_run={_user_changed_run}, _reset_selector={st.session_state.get("_reset_selector")!r}', file=sys.stderr, flush=True)
if st.session_state.get("active_run_id") is None and _sel_idx_raw == 0:
    # ── Create New Run form ───────────────────────────────────────────────────
    _fg = "#888" if _dark_mode else "#666"
    if _LOGO_SRC:
        st.markdown(
            f'<div style="text-align:center;padding:32px 20px 8px;">'
            f'<img src="{_LOGO_SRC}" style="max-width:460px;width:65%;"></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("<h2 style='text-align:center'>🏁 RaceFusion</h2>", unsafe_allow_html=True)

    st.markdown("### Create New Run")
    st.caption("Upload what you have — all fields are optional. Click **Create Run** when ready.")

    with st.form(f"create_run_form_{st.session_state['upload_gen']}", clear_on_submit=True):
        _form_csv_col, _form_slip_col = st.columns(2)
        with _form_csv_col:
            st.markdown("**📂 RacePak CSV**")
            _form_csv_file = st.file_uploader(
                "RacePak CSV", type=["csv"],
                help="Export from RacePak DataLink or V-Net",
                label_visibility="collapsed",
            )
        with _form_slip_col:
            st.markdown("**🎫 Timeslip Photo**")
            _form_slip_file = st.file_uploader(
                "Timeslip photo", type=["jpg", "jpeg", "png", "webp"],
                help="Clear photo of your printed timeslip",
                label_visibility="collapsed",
            )
        _form_note = st.text_input(
            "Run note (optional)",
            placeholder="e.g. 1st qualifying pass, 80 °F, sticky track",
        )
        _form_submitted = st.form_submit_button(
            "🏁 Create Run", type="primary", use_container_width=True,
        )

    if _form_submitted:
        if _form_csv_file is None and _form_slip_file is None:
            st.error("Upload at least a RacePak CSV or a timeslip photo.")
        else:
            # ── Determine run filename ────────────────────────────────────────
            if _form_csv_file is not None:
                _new_run_id    = _form_csv_file.name
                _new_csv_bytes = _form_csv_file.read()
            else:
                from datetime import datetime as _dt_form
                _new_run_id    = f"slip_{_dt_form.now().strftime('%Y%m%d_%H%M%S')}.run"
                _new_csv_bytes = None

            _new_run_rec = {}
            if _form_note.strip():
                _new_run_rec["run_note"] = _form_note.strip()

            with st.status("Creating run…", expanded=True) as _create_status:

                # ── Save CSV ──────────────────────────────────────────────────
                if _new_csv_bytes is not None:
                    _create_status.write("💾 Saving CSV data…")
                    _stale_key = _get_slip_storage_key(_new_run_id)
                    if _stale_key:
                        _delete_slip_from_storage(_stale_key)
                    save_run_csv(_new_run_id, _new_csv_bytes)

                save_run(_new_run_id, _new_run_rec)

                # ── Upload + scan timeslip ────────────────────────────────────
                if _form_slip_file is not None:
                    _create_status.write("📤 Uploading timeslip…")
                    _sl_bytes = _form_slip_file.read()
                    _sl_ext   = _form_slip_file.name.rsplit(".", 1)[-1].lower()
                    _sl_stem  = re.sub(r"[^\w\-]", "_", Path(_new_run_id).stem)
                    _sl_s_key = f"{_current_user}/{_sl_stem}.{_sl_ext}"
                    _sl_mime  = {"jpg":"image/jpeg","jpeg":"image/jpeg",
                                 "png":"image/png","webp":"image/webp"}.get(_sl_ext, "image/jpeg")
                    if _sb:
                        try:
                            _sb.storage.from_("timeslips").upload(
                                path=_sl_s_key, file=_sl_bytes,
                                file_options={"upsert": "true", "content-type": _sl_mime},
                            )
                        except Exception as _sl_se:
                            st.warning(f"Timeslip upload failed: {_sl_se}")
                    _new_run_rec["timeslip_storage_key"] = _sl_s_key

                    if api_key:
                        _create_status.write("🎫 Scanning timeslip…")
                        try:
                            _scan_result = scan_timeslip(_sl_bytes, _sl_mime, api_key, car_number_input)
                            _new_run_rec["timeslip"] = _scan_result

                            # ── Fetch weather ─────────────────────────────────
                            _slip_date = _scan_result.get("date")
                            if _slip_date:
                                _slip_hour = 12
                                if _scan_result.get("time"):
                                    try:
                                        _slip_hour = int(str(_scan_result["time"]).split(":")[0])
                                    except Exception:
                                        _slip_hour = 12
                                _wx_lat, _wx_lon, _wx_label = None, None, ""
                                _track = (_scan_result.get("track_location")
                                          or _scan_result.get("track_name") or "")
                                if _track:
                                    _create_status.write(f"📍 Geocoding {_track}…")
                                    _wx_lat, _wx_lon, _wx_label = geocode(_track)
                                if _wx_lat is None and cfg.get("lat"):
                                    _wx_lat  = cfg["lat"]
                                    _wx_lon  = cfg["lon"]
                                    _wx_label = cfg.get("location_label", "")
                                if _wx_lat is not None:
                                    _create_status.write("🌤️ Fetching weather…")
                                    try:
                                        _wx = fetch_weather(_wx_lat, _wx_lon, _slip_date, _slip_hour)
                                        _da = calc_density_altitude(
                                            _wx.get("temperature_f"),
                                            _wx.get("humidity_pct"),
                                            _wx.get("pressure_hpa"),
                                        )
                                        if _da is not None:
                                            _wx["density_alt_ft"] = round(_da)
                                        _new_run_rec["weather"]          = _wx
                                        _new_run_rec["weather_date"]     = _slip_date
                                        _new_run_rec["weather_location"] = _wx_label
                                    except Exception as _wx_e:
                                        st.warning(f"Weather fetch failed: {_wx_e}")
                        except Exception as _scan_e:
                            st.warning(f"Timeslip scan failed: {_scan_e}")

                    save_run(_new_run_id, _new_run_rec)

                _create_status.update(label="✅ Run created!", state="complete")

            st.session_state["active_run_id"] = _new_run_id
            st.query_params["run"] = _new_run_id
            st.session_state.pop("_restore_run_selector", None)
            st.rerun()

    st.stop()

# ── Load RacePak data (may be None for closed runs) ───────────────────────────
csv_name = st.session_state.get("active_run_id")
if csv_name is None and _sel_idx_raw > 0 and _sel_idx_raw <= len(_saved_runs):
    csv_name = _saved_runs[_sel_idx_raw - 1]["filename"]
    st.session_state["active_run_id"] = csv_name
    st.query_params["run"] = csv_name
# Load CSV bytes now — deferred from the sidebar so uploads are fully processed first
_run_meta_now     = next((r for r in _saved_runs if r["filename"] == csv_name), None)
_active_csv_bytes = load_run_csv_bytes(csv_name) if (_run_meta_now and _run_meta_now["has_csv"]) else None
_csv_available    = _active_csv_bytes is not None

if _csv_available:
    df = load_racepak_csv(_active_csv_bytes)
    time_col = get_time_col(df)
    available_channels = [c for c in df.columns if c != time_col]
    channel_to_group: dict[str, str] = {}
    for grp, chs in CHANNEL_GROUPS.items():
        for ch in chs:
            if ch in available_channels:
                channel_to_group[ch] = grp
    for ch in available_channels:
        if ch not in channel_to_group:
            channel_to_group[ch] = "📦 Other"
    groups_present = list(dict.fromkeys(
        [channel_to_group[ch] for ch in ALL_GROUPED if ch in available_channels]
        + [channel_to_group[ch] for ch in available_channels
           if channel_to_group[ch] not in
           [channel_to_group[c] for c in ALL_GROUPED if c in available_channels]]
    ))
else:
    df = None
    time_col = None
    available_channels = []
    channel_to_group = {}
    groups_present = []

# ── Sidebar: Channel Rules (always visible, after Track Location) ─────────────
st.sidebar.markdown("---")
st.sidebar.markdown("### ⚠️ Channel Rules")
_rules = cfg.get("channel_rules", {})

with st.sidebar.expander("Add / Edit Rule", expanded=False):
    _rule_ch = st.selectbox("Channel", options=[""] + available_channels, key="rule_ch")
    if _rule_ch:
        _existing = _rules.get(_rule_ch, {})
        _col_a, _col_b = st.columns(2)
        _use_min = _col_a.checkbox("Min", value="min" in _existing, key="rule_use_min")
        _use_max = _col_b.checkbox("Max", value="max" in _existing, key="rule_use_max")
        _min_val = _col_a.number_input(
            "Min value", value=float(_existing.get("min", 0)),
            disabled=not _use_min, key="rule_min_val",
        )
        _max_val = _col_b.number_input(
            "Max value", value=float(_existing.get("max", 0)),
            disabled=not _use_max, key="rule_max_val",
        )
        if st.button("💾 Save Rule", key="save_rule_btn"):
            _new_rule = {}
            if _use_min:
                _new_rule["min"] = _min_val
            if _use_max:
                _new_rule["max"] = _max_val
            if _new_rule:
                _rules[_rule_ch] = _new_rule
                cfg["channel_rules"] = _rules
                save_config(cfg)
                st.success(f"Rule saved for {_rule_ch}")
                st.rerun()

# List existing rules with remove buttons
if _rules:
    for _ch, _rule in list(_rules.items()):
        _parts = []
        if "min" in _rule:
            _parts.append(f"min {_rule['min']}")
        if "max" in _rule:
            _parts.append(f"max {_rule['max']}")
        _rcol1, _rcol2 = st.sidebar.columns([3, 1])
        _rcol1.caption(f"**{_ch}**: {' · '.join(_parts)}")
        if _rcol2.button("✕", key=f"del_rule_{_ch}"):
            del _rules[_ch]
            cfg["channel_rules"] = _rules
            save_config(cfg)
            st.rerun()
else:
    st.sidebar.caption("No rules set yet.")

# ── Load or init run record ───────────────────────────────────────────────────
run = load_run(csv_name)

# ── Load timeslip image from storage ─────────────────────────────────────────
# All upload processing (including saving to storage) is handled by the
# processing zone above. Here we just load whatever is already stored.
_slip_storage_key = run.get("timeslip_storage_key")
_slip_bytes = None
_slip_ext   = None
_slip_media = None
_SLIP_MIME  = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp"}

if _slip_storage_key and _sb:
    try:
        _raw = _sb.storage.from_("timeslips").download(_slip_storage_key)
        _slip_bytes = bytes(_raw)
        _slip_ext   = _slip_storage_key.rsplit(".", 1)[-1].lower()
        _slip_media = _SLIP_MIME.get(_slip_ext, "image/jpeg")
    except Exception:
        _slip_bytes = None
        _slip_storage_key = None

# ── Scan timeslip if image available and data not yet extracted ───────────────
if _slip_bytes is not None and "timeslip" not in run:
    if not api_key:
        _scan_status_area.warning("⚠️ ANTHROPIC_API_KEY not set — timeslip scanning unavailable.")
    else:
        with _scan_status_area.status("🎫 Scanning timeslip…", expanded=False) as _scan_status:
            try:
                slip_data = scan_timeslip(_slip_bytes, _slip_media, api_key, car_number_input)
                run["timeslip"] = slip_data
                run["csv_name"] = csv_name
                save_run(csv_name, run)
                _scan_status.update(label="✅ Timeslip scanned!", state="complete", expanded=False)
                st.rerun()
            except Exception as e:
                _scan_status.update(label="❌ Scan failed", state="error", expanded=True)
                st.error(f"Timeslip scan failed: {e}")


# ── Fetch weather if we have a date and location ──────────────────────────────
slip = run.get("timeslip", {})
if slip and "weather" not in run and slip.get("date"):
    date_str = slip["date"]
    # Parse run hour from timeslip time field
    hour = 12
    if slip.get("time"):
        try:
            hour = int(str(slip["time"]).split(":")[0])
        except Exception:
            hour = 12

    # Resolve lat/lon: timeslip track_location first, then manual config
    wx_lat, wx_lon, wx_label = None, None, ""

    track_loc = slip.get("track_location") or slip.get("track_name") or ""
    if track_loc:
        with st.sidebar.status(f"📍 Geocoding: {track_loc}…", expanded=False) as _geo_status:
            wx_lat, wx_lon, wx_label = geocode(track_loc)
            if wx_lat is None:
                _geo_status.update(label=f"📍 Couldn't geocode '{track_loc}'", state="error", expanded=False)
            else:
                _geo_status.update(label=f"📍 {wx_label}", state="complete", expanded=False)

    if wx_lat is None and cfg.get("lat"):
        wx_lat = cfg["lat"]
        wx_lon = cfg["lon"]
        wx_label = cfg.get("location_label", "")

    if wx_lat is not None:
        with st.sidebar.status("🌤️ Fetching weather…", expanded=False) as _wx_status:
            try:
                wx = fetch_weather(wx_lat, wx_lon, date_str, hour)
                # Compute DA from API values and persist it so the AI always has it
                _fetched_da = calc_density_altitude(
                    wx.get("temperature_f"), wx.get("humidity_pct"), wx.get("pressure_hpa")
                )
                if _fetched_da is not None:
                    wx["density_alt_ft"] = round(_fetched_da)
                run["weather"] = wx
                run["weather_date"] = date_str
                run["weather_location"] = wx_label
                save_run(csv_name, run)
                _wx_status.update(label="✅ Weather fetched!", state="complete", expanded=False)
                st.rerun()
            except Exception as e:
                _wx_status.update(label="❌ Weather fetch failed", state="error", expanded=True)
                st.sidebar.warning(f"Weather fetch failed: {e}")
    else:
        st.sidebar.info("📍 No track location found. Enter one in Track Location below to fetch weather.")

# _rd and _changelog loaded here so they're available throughout the dashboard
# If this run has no saved details yet, pre-fill from Car Profile in config
_rd        = run.get("run_details") or cfg.get("car_profile", {})
_changelog = run.get("changelog", [])


# ── Sidebar: chart controls (only shown when CSV is available) ────────────────
# All widget keys are scoped to csv_name so they reset cleanly when switching runs.
if _csv_available:
    st.sidebar.markdown("### Time Range")
    t_min = float(df[time_col].min())
    t_max = float(df[time_col].max())
    t_range = st.sidebar.slider(
        "Seconds", min_value=t_min, max_value=t_max,
        value=(t_min, t_max), step=0.02,
        key=f"t_range_{csv_name}",
    )
    df_view = df[(df[time_col] >= t_range[0]) & (df[time_col] <= t_range[1])]

    st.sidebar.markdown("### Groups to Show")
    selected_groups = st.sidebar.multiselect(
        "Channel groups", options=groups_present, default=groups_present[:4],
        help="Each group shows all its channels overlaid on one chart",
        key=f"sel_groups_{csv_name}",
    )
else:
    df_view = None
    selected_groups = []

if _csv_available:
    st.sidebar.markdown("### Hidden Channels")
    _flat_channels = [
        ch for ch in available_channels
        if df[ch].dropna().nunique() <= 1
    ]
    _saved_hidden = cfg.get("hidden_channels", [])
    _saved_hidden = [ch for ch in _saved_hidden if ch in available_channels]
    hidden_channels = st.sidebar.multiselect(
        "Channels to hide",
        options=available_channels,
        default=_saved_hidden,
        help="These channels are removed from all charts. Flat/no-data channels are good candidates.",
        key=f"hidden_ch_{csv_name}",
    )
    if _flat_channels:
        _flat_not_hidden = [ch for ch in _flat_channels if ch not in hidden_channels]
        if _flat_not_hidden:
            st.sidebar.caption(f"💡 Flat (no variation): {', '.join(_flat_not_hidden)}")
    if hidden_channels != _saved_hidden:
        cfg["hidden_channels"] = hidden_channels
        save_config(cfg)
    available_channels = [ch for ch in available_channels if ch not in hidden_channels]

    st.sidebar.markdown("### Custom Overlay")
    custom_channels = st.sidebar.multiselect(
        "Pick any channels to compare",
        options=available_channels,
        default=[],
        help="Select two or more channels to plot together on a single chart",
        key=f"custom_ch_{csv_name}",
    )

    st.sidebar.markdown("### Chart Style")
    chart_height = st.sidebar.slider("Chart height (px)", 200, 600, 320, 50,
                                     key=f"chart_h_{csv_name}")
    show_markers = st.sidebar.checkbox("Show data points", value=False,
                                       key=f"show_markers_{csv_name}")
    mode = "lines+markers" if show_markers else "lines"
else:
    hidden_channels = []
    custom_channels = []
    chart_height = 320
    mode = "lines"

# ── Overlay chart helper (defined here so it's available throughout dashboard) ─
def make_overlay_chart(channels, title, time_col, df_view, t_range, mode, height,
                       dark=True):
    """Return a Plotly figure with all listed channels overlaid.
    Channels with very different value ranges are split onto dual Y-axes.
    """
    valid = []
    for ch in channels:
        vals = df_view[ch].dropna()
        if not vals.empty:
            peak = float(vals.abs().max()) if vals.abs().max() > 0 else 1.0
            valid.append((ch, peak))
    if not valid:
        return None
    valid_sorted = sorted(valid, key=lambda x: x[1])
    peaks = [p for _, p in valid_sorted]
    use_dual = (peaks[-1] / peaks[0]) > 10 if len(peaks) > 1 else False
    left_chs  = [ch for ch, p in valid_sorted if p / peaks[0] <= 10]
    right_chs = [ch for ch, p in valid_sorted if p / peaks[0] >  10]
    color_index = {ch: i for i, (ch, _) in enumerate(valid)}
    fig = go.Figure()
    for ch in left_chs:
        i = color_index[ch]
        fig.add_trace(go.Scatter(
            x=df_view[time_col], y=df_view[ch], mode=mode, name=ch, yaxis="y",
            line=dict(width=2.2, color=TRACE_COLORS[i % len(TRACE_COLORS)]),
            marker=dict(size=3),
        ))
    for ch in right_chs:
        i = color_index[ch]
        fig.add_trace(go.Scatter(
            x=df_view[time_col], y=df_view[ch], mode=mode, name=ch, yaxis="y2",
            line=dict(width=2.2, color=TRACE_COLORS[i % len(TRACE_COLORS)]),
            marker=dict(size=3),
        ))
    if t_range[0] < 0:
        fig.add_vrect(
            x0=t_range[0], x1=min(0.0, t_range[1]),
            fillcolor="rgba(100,100,100,0.12)", layer="below", line_width=0,
            annotation_text="pre-launch", annotation_position="top left",
            annotation_font_size=10,
        )
    layout_extra = {}
    if use_dual and right_chs:
        layout_extra["yaxis2"] = dict(
            overlaying="y", side="right", showgrid=False,
            tickfont=dict(size=10),
            title=dict(text=" / ".join(right_chs), font=dict(size=10)),
        )
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=60 if use_dual else 0, t=44, b=0),
        xaxis_title=f"{time_col} (s)",
        hovermode="x unified",
        template="plotly_dark" if dark else "plotly_white",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(size=11, color="#e8e8e8"),
            bgcolor="rgba(0,0,0,0.6)",
            bordercolor="rgba(255,255,255,0.1)",
            borderwidth=1,
        ),
        **layout_extra,
    )
    return fig

# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════
if _LOGO_SRC:
    st.markdown(
        f'<img src="{_LOGO_SRC}" style="max-width:520px;width:60%;'
        f'margin:0 auto 4px auto;display:block;">',
        unsafe_allow_html=True,
    )
else:
    st.markdown("## 🏁 RaceFusion")

_run_display_name = _run_label(csv_name, run) if csv_name.endswith(".run") else csv_name
st.caption(f"Run: **{_run_display_name}**")

if not _csv_available:
    if csv_name.endswith(".run"):
        st.caption("🎫 Timeslip-only run — upload a timeslip photo in the sidebar to get started.")
    else:
        st.caption("ℹ️ No CSV data for this run — channel charts unavailable.")

# Save & Close button — always visible when a run is active, regardless of CSV
st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)
_sc_col1, _sc_col2 = st.columns([5, 2])
_sc_col1.markdown("")  # spacer
if _sc_col2.button("✅ Save & Close Run", use_container_width=True, type="primary",
                   key="save_close_btn",
                   help="Saves all run data and returns to upload screen for the next run"):
    # Just reset the UI — keep CSV, timeslip image, and JSON all intact
    st.session_state["upload_gen"] += 1
    st.session_state.pop("_last_uploaded_csv", None)   # allow clean re-upload of same filename
    st.session_state["_reset_selector"] = True
    st.rerun()

# ── Summary row ───────────────────────────────────────────────────────────────
# Pull timeslip values for ET / MPH / RWHP when available; fall back to CSV
_slip = run.get("timeslip", {})
_slip_et  = _slip.get("ft_1320")   # e.g. "7.432"
_slip_mph = _slip.get("mph_1320")  # e.g. "185.24"
_rwhp = calc_rwhp(weight_input, _slip_et, _slip_mph) if (_slip_et or _slip_mph) and weight_input else {}

# ET — timeslip preferred, fall back to CSV if available
if _slip_et:
    try:
        _et_val = float(_slip_et)
        _et_str = f"{_et_val:.3f} s"
        _et_src  = "timeslip"
    except (ValueError, TypeError):
        _et_val = None
        _et_str = _slip_et
        _et_src  = "timeslip"
elif df is not None and "Clock 1320ft" in df.columns:
    _et_col = df["Clock 1320ft"][df["Clock 1320ft"] > 0]
    _et_val = _et_col.max() if not _et_col.empty else None
    _et_str = f"{_et_val:.3f} s" if _et_val else "—"
    _et_src  = "RacePak"
else:
    _et_val, _et_str, _et_src = None, "—", ""

# Trap MPH — timeslip preferred
if _slip_mph:
    try:
        _mph_val = float(_slip_mph)
        _mph_str = f"{_mph_val:.2f} mph"
        _mph_src  = "timeslip"
    except (ValueError, TypeError):
        _mph_val = None
        _mph_str = _slip_mph
        _mph_src  = "timeslip"
elif df is not None and "G-Meter MPH" in df.columns:
    _mph_val = df["G-Meter MPH"].max()
    _mph_str = f"{_mph_val:.1f} mph"
    _mph_src  = "RacePak"
else:
    _mph_val, _mph_str, _mph_src = None, "—", ""

c1, c2, c3, c4, c5, c6 = st.columns(6)
if df is not None and "Engine RPM" in df.columns:
    c1.metric("Peak Engine RPM", f"{df['Engine RPM'].max():,.0f}", help="From RacePak data")
c2.metric("ET", _et_str, help=f"Source: {_et_src}" if _et_src else None)
c3.metric("Trap Speed", _mph_str, help=f"Source: {_mph_src}" if _mph_src else None)
if _rwhp.get("from_mph"):
    c4.metric("RWHP (trap speed)", f"{_rwhp['from_mph']:,.0f} hp", help="Weight × (MPH÷234)³")
elif _rwhp.get("from_et"):
    c4.metric("RWHP (ET)", f"{_rwhp['from_et']:,.0f} hp", help="Weight × (5.825÷ET)³")
if df is not None and "Accel G" in df.columns:
    c5.metric("Peak Accel G", f"{df['Accel G'].max():.2f} g")
if df is not None and "Boost Press" in df.columns:
    c6.metric("Peak Boost", f"{df['Boost Press'].max():.1f} psi")

st.markdown("---")

# ── Run Details & Changelog ───────────────────────────────────────────────────
_main_left, _main_right = st.columns(2)

# ── Left: Run Details form ────────────────────────────────────────────────────
# Wrapped in st.form so that typing in number/text fields does NOT trigger a
# full page rerun — only the submit buttons do.  This keeps active_run_id stable
# while the user edits values.
with _main_left:
    with st.expander("📋 Run Details", expanded=False):
        _rk = csv_name  # widget key shorthand — scoped to run so values reset on switch
        with st.form(f"run_details_form_{_rk}"):
            st.caption("**Tire Pressures (psi)**")
            _rd_col1, _rd_col2 = st.columns(2)
            _rd_tire_fl = _rd_col1.number_input("FL", min_value=0.0, max_value=60.0,
                            value=float(_rd.get("tire_pressure_fl", 0.0)), step=0.5, format="%.1f", key=f"rd_fl_{_rk}")
            _rd_tire_fr = _rd_col2.number_input("FR", min_value=0.0, max_value=60.0,
                            value=float(_rd.get("tire_pressure_fr", 0.0)), step=0.5, format="%.1f", key=f"rd_fr_{_rk}")
            _rd_tire_rl = _rd_col1.number_input("RL", min_value=0.0, max_value=60.0,
                            value=float(_rd.get("tire_pressure_rl", 0.0)), step=0.5, format="%.1f", key=f"rd_rl_{_rk}")
            _rd_tire_rr = _rd_col2.number_input("RR", min_value=0.0, max_value=60.0,
                            value=float(_rd.get("tire_pressure_rr", 0.0)), step=0.5, format="%.1f", key=f"rd_rr_{_rk}")

            st.caption("**Track / Tire Conditions**")
            _rd_col3, _rd_col4 = st.columns(2)
            _rd_track_temp = _rd_col3.number_input("Track Temp (°F)", min_value=-20.0, max_value=200.0,
                            value=float(_rd.get("track_temp_f", 0.0)), step=1.0, format="%.0f", key=f"rd_track_temp_{_rk}")
            _rd_tire_temp = _rd_col4.number_input("Tire Temp (°F)", min_value=0.0, max_value=300.0,
                            value=float(_rd.get("tire_temp_f", 0.0)), step=1.0, format="%.0f", key=f"rd_tire_temp_{_rk}")

            st.caption("**RPM**")
            _rd_col5, _rd_col6 = st.columns(2)
            _rd_launch_rpm  = _rd_col5.number_input("Launch RPM", min_value=0, max_value=15000,
                            value=int(_rd.get("launch_rpm", 0)), step=100, key=f"rd_launch_rpm_{_rk}")
            _rd_shift_point = _rd_col6.number_input("Shift Point", min_value=0, max_value=15000,
                            value=int(_rd.get("shift_point", 0)), step=100, key=f"rd_shift_{_rk}")

            st.caption("**Fuel System**")
            _rd_col7, _rd_col8 = st.columns(2)
            _rd_main_jet    = _rd_col7.number_input("Main Jet", min_value=0.0, max_value=999.0,
                            value=float(_rd.get("main_jet", 0.0)), step=0.001, format="%.3f", key=f"rd_main_jet_{_rk}")
            _rd_hs_jet      = _rd_col8.number_input("HS Jet", min_value=0.0, max_value=999.0,
                            value=float(_rd.get("hs_jet", 0.0)), step=0.001, format="%.3f", key=f"rd_hs_jet_{_rk}")
            _rd_hs_open_psi = _rd_col7.number_input("HS Open PSI", min_value=0.0, max_value=500.0,
                            value=float(_rd.get("hs_open_psi", 0.0)), step=1.0, format="%.0f", key=f"rd_hs_psi_{_rk}")

            st.caption("**Blower**")
            _rd_col9, _rd_col10 = st.columns(2)
            _rd_top_pulley  = _rd_col9.number_input("Top Pulley", min_value=0, max_value=100,
                            value=int(_rd.get("top_pulley", 0)), step=1, key=f"rd_top_pulley_{_rk}")
            _rd_bot_pulley  = _rd_col10.number_input("Bottom Pulley", min_value=0, max_value=100,
                            value=int(_rd.get("bottom_pulley", 0)), step=1, key=f"rd_bot_pulley_{_rk}")
            _rd_overdrive   = ((_rd_bot_pulley / _rd_top_pulley) - 1) if _rd_top_pulley else 0.0
            _rd_col9.caption(f"Overdrive: **{_rd_overdrive * 100:.2f}%**")
            _rd_col11, _rd_col12 = st.columns(2)
            _rd_wb_d = _rd_col11.number_input("Wheelie Bar – D", min_value=0.0, max_value=10.0,
                            value=float(_rd.get("wheelie_bar_d", 0.0)), step=0.001, format="%.3f", key=f"rd_wb_d_{_rk}")
            _rd_wb_p = _rd_col12.number_input("Wheelie Bar – P", min_value=0.0, max_value=10.0,
                            value=float(_rd.get("wheelie_bar_p", 0.0)), step=0.001, format="%.3f", key=f"rd_wb_p_{_rk}")

            st.caption("**Ignition**")
            _rd_spark_plug = st.text_input("Spark Plug", value=_rd.get("spark_plug", ""),
                            placeholder="e.g. NGK-R-5671-11", key=f"rd_spark_plug_{_rk}")
            _rd_col13, _rd_col14 = st.columns(2)
            _rd_plug_gap   = _rd_col13.text_input("Plug Gap", value=_rd.get("plug_gap", ""),
                            placeholder='0.016"', key=f"rd_plug_gap_{_rk}")
            _rd_valve_lash = _rd_col14.text_input("Lash INT/EXT", value=_rd.get("valve_lash", ""),
                            placeholder='0.016"/0.016"', key=f"rd_valve_lash_{_rk}")

            _rd_notes = st.text_area("Run notes", value=_rd.get("notes", ""),
                            placeholder="e.g. First pass of day, track freshly prepped...", height=70,
                            key=f"rd_notes_{_rk}")

            _rd_payload = {
                "tire_pressure_fl": _rd_tire_fl,  "tire_pressure_fr": _rd_tire_fr,
                "tire_pressure_rl": _rd_tire_rl,  "tire_pressure_rr": _rd_tire_rr,
                "track_temp_f":     _rd_track_temp, "tire_temp_f":    _rd_tire_temp,
                "launch_rpm":       _rd_launch_rpm,  "shift_point":   _rd_shift_point,
                "main_jet":         _rd_main_jet,    "hs_jet":        _rd_hs_jet,
                "hs_open_psi":      _rd_hs_open_psi,
                "top_pulley":       _rd_top_pulley,  "bottom_pulley": _rd_bot_pulley,
                "overdrive":        _rd_overdrive,
                "wheelie_bar_d":    _rd_wb_d,        "wheelie_bar_p": _rd_wb_p,
                "spark_plug":       _rd_spark_plug,  "plug_gap":      _rd_plug_gap,
                "valve_lash":       _rd_valve_lash,  "notes":         _rd_notes,
            }
            _btn_col1, _btn_col2 = st.columns(2)
            _rd_save        = _btn_col1.form_submit_button("💾 Save Run Details",    use_container_width=True, type="secondary")
            _rd_car_profile = _btn_col2.form_submit_button("🚗 Save as Car Profile",
                                help="Pre-fills Run Details for all future runs",    use_container_width=True, type="secondary")

        # Handlers run outside the form context (after the with block) so they
        # fire only on actual submit clicks — never on field changes.
        if _rd_save:
            run["run_details"] = _rd_payload
            save_run(csv_name, run)
            st.success("Run details saved!")
        if _rd_car_profile:
            run["run_details"] = _rd_payload
            save_run(csv_name, run)
            cfg["car_profile"] = {k: v for k, v in _rd_payload.items() if k != "notes"}
            save_config(cfg)
            st.success("Car Profile updated! Future runs will pre-fill from this.")

# ── Right: Changelog ──────────────────────────────────────────────────────────
with _main_right:
    with st.expander(f"🔄 Run Changelog ({len(_changelog)} changes)", expanded=bool(_changelog)):
        # Existing entries
        if _changelog:
            for _ci, _ce in enumerate(_changelog):
                _del_col, _txt_col = st.columns([1, 8])
                _txt_col.markdown(
                    f"**{_ce['parameter']}**: "
                    f"<span style='color:#ef4444'>{_ce['from_val']}</span> → "
                    f"<span style='color:#22c55e'>{_ce['to_val']}</span>"
                    + (f" — _{_ce['note']}_" if _ce.get("note") else ""),
                    unsafe_allow_html=True,
                )
                if _del_col.button("✕", key=f"del_cl_{_ci}"):
                    _changelog.pop(_ci)
                    run["changelog"] = _changelog
                    save_run(csv_name, run)
                    st.rerun()
            st.divider()

        # Add new entry
        st.caption("**Log a change from last run**")
        _cl_param = st.text_input("What changed", placeholder="e.g. Rear tire pressure", key=f"cl_param_{_rk}")
        _cl_c1, _cl_c2 = st.columns(2)
        _cl_from = _cl_c1.text_input("From", placeholder="6.5 psi", key=f"cl_from_{_rk}")
        _cl_to   = _cl_c2.text_input("To",   placeholder="6.0 psi", key=f"cl_to_{_rk}")
        _cl_note = st.text_input("Optional note", placeholder="e.g. felt loose at launch", key=f"cl_note_{_rk}")
        if st.button("➕ Add", disabled=not (_cl_param and _cl_from and _cl_to)):
            _changelog.append({
                "parameter": _cl_param.strip(),
                "from_val":  _cl_from.strip(),
                "to_val":    _cl_to.strip(),
                "note":      _cl_note.strip(),
            })
            run["changelog"] = _changelog
            save_run(csv_name, run)
            st.rerun()

st.markdown("---")

# ── AI Virtual Tuner ──────────────────────────────────────────────────────────
st.markdown("## 🤖 AI Virtual Tuner")

def _build_ai_payload(csv_name: str, run_rec: dict, df, available_channels: list,
                      all_saved_runs: list, car_cfg: dict) -> str:
    import json as _json

    slip = run_rec.get("timeslip", {})
    wx   = run_rec.get("weather", {})
    rd   = run_rec.get("run_details", {})

    # ── Full channel stats for current run ────────────────────────────────────
    ch_stats = {}
    if df is not None:
        for ch in available_channels:
            s = df[ch].dropna()
            if len(s) > 5:
                ch_stats[ch] = {
                    "min":  round(float(s.min()), 3),
                    "max":  round(float(s.max()), 3),
                    "mean": round(float(s.mean()), 3),
                    "std":  round(float(s.std()), 3),
                }

    # ── Time-series snapshots for key channels (sampled every 0.1s) ──────────
    key_traces = {}
    if df is not None:
        _time_col_ai = next((c for c in df.columns if "time" in c.lower()), None)
        _key_chs = [c for c in [
            "Engine RPM", "Boost Press", "Fuel Press", "Fuel Flow",
            "Accel G", "Oil Press", "Water Temp", "Driveshaft RPM",
            "Avg. EGT", "Cyl #1","Cyl #2","Cyl #3","Cyl #4",
            "Cyl #5","Cyl #6","Cyl #7","Cyl #8",
        ] if c in df.columns]
        if _time_col_ai and _key_chs:
            _df_s = df[[_time_col_ai] + _key_chs].copy()
            _df_s = _df_s[_df_s[_time_col_ai] >= 0]
            # Sample ~every 0.25s to keep payload size manageable
            _df_s = _df_s.iloc[::max(1, len(_df_s)//200)]
            for _c in _key_chs:
                key_traces[_c] = [
                    [round(float(r[_time_col_ai]), 2), round(float(r[_c]), 2)]
                    for _, r in _df_s[[_time_col_ai, _c]].dropna().iterrows()
                ]

    # ── Previous runs: timeslip, weather, channel stats, changelog ────────────
    # Build in chronological order (oldest first) so Run 1 = oldest
    _other_runs = [s for s in reversed(all_saved_runs) if s["filename"] != csv_name]
    prev_runs = []
    for _run_idx, saved in enumerate(_other_runs, start=1):
        if saved["filename"] == csv_name:
            continue
        rec  = saved["record"]
        s    = rec.get("timeslip", {})
        p_wx = rec.get("weather", {})
        p_rd = rec.get("run_details", {})
        # Load that run's CSV for channel stats if available
        p_ch = {}
        _p_csv_bytes = load_run_csv_bytes(saved["filename"])
        if _p_csv_bytes:
            try:
                _p_df = load_racepak_csv(_p_csv_bytes)
                for ch in _p_df.columns:
                    _s = _p_df[ch].dropna()
                    if len(_s) > 5 and _s.std() > 0.001:
                        p_ch[ch] = {
                            "min":  round(float(_s.min()), 3),
                            "max":  round(float(_s.max()), 3),
                            "mean": round(float(_s.mean()), 3),
                        }
            except Exception:
                pass
        # DA: prefer timeslip value, then stored weather value, then compute from raw wx
        _p_da = (s.get("density_alt_ft") or
                 p_wx.get("density_alt_ft") or
                 calc_density_altitude(p_wx.get("temperature_f"),
                                       p_wx.get("humidity_pct"),
                                       p_wx.get("pressure_hpa")))
        if _p_da is not None:
            _p_da = round(_p_da)
        _run_date = s.get("date", "") or ""
        _run_label = f"Run {_run_idx}" + (f" ({_run_date})" if _run_date else "")
        prev_runs.append({
            "label":         _run_label,
            "filename":      saved["filename"],
            "date":          _run_date,
            "track":         s.get("track_name", "") or s.get("track_location", ""),
            "timeslip": {
                "reaction":  s.get("reaction_time"),
                "ft_60":     s.get("ft_60"),
                "ft_330":    s.get("ft_330"),
                "ft_660":    s.get("ft_660"),
                "mph_660":   s.get("mph_660"),
                "ft_1000":   s.get("ft_1000"),
                "et_1320":   s.get("ft_1320"),
                "mph_1320":  s.get("mph_1320"),
            },
            "weather": {
                "temp_f":       p_wx.get("temperature_f"),
                "humidity_pct": p_wx.get("humidity_pct"),
                "baro_inhg":    round(p_wx.get("pressure_hpa", 0) * 0.02953, 2) if p_wx.get("pressure_hpa") else None,
                "density_alt_ft": _p_da,
                "wind":         s.get("wind"),
            },
            "run_details":   p_rd,
            "channel_stats": p_ch,
            "changelog":     rec.get("changelog", []),
            "notes":         p_rd.get("notes", ""),
        })

    # ── Car profile ───────────────────────────────────────────────────────────
    _gr = car_cfg.get("gear_ratios", {})
    _num_g = int(car_cfg.get("num_gears", 2))
    _gear_ratio_list = {
        ["1st","2nd","3rd","4th","5th","6th"][i]: _gr.get(str(i+1)) or "NOT SET"
        for i in range(_num_g)
    }
    # Flag missing specs so the AI can call them out
    def _ms(val, label):
        """Return value or a sentinel that tells the AI the field is missing."""
        return val if val else f"NOT SET — {label} missing, analysis limited"

    car_profile = {
        "sanctioning_body": _ms(car_cfg.get("sanctioning_body", ""), "sanctioning body (NHRA/IHRA/NMCA/etc.) — needed to apply correct rulebook"),
        "class_name":       _ms(car_cfg.get("class_name", ""), "class name — needed to determine index, dial-in rules, and performance limits"),
        "engine":          _ms(car_cfg.get("engine_desc",""), "engine displacement/type"),
        "fuel_type":       _ms(car_cfg.get("fuel_type",""), "fuel type — critical for EGT range, fuel flow, and power interpretation"),
        "carburetor":      _ms(car_cfg.get("carb_desc",""), "carburetor/fuel system"),
        "blower_type":     _ms(car_cfg.get("blower_type",""), "blower type (roots/screw)"),
        "blower_style":    _ms(car_cfg.get("blower_style",""), "blower rotor style"),
        "blower_size":     _ms(car_cfg.get("blower_size",""), "blower case size e.g. 14-71"),
        "converter":       _ms(car_cfg.get("converter_desc",""), "converter stall speed and type"),
        "transmission":    _ms(car_cfg.get("transmission",""), "transmission type"),
        "num_gears":       _num_g,
        "gear_ratios":     _gear_ratio_list,
        "rear_gear_ratio": _ms(car_cfg.get("rear_gear_ratio",""), "rear end ratio — needed to validate DS RPM vs engine RPM"),
        "suspension_type": _ms(car_cfg.get("suspension_type",""), "suspension type (hardtail/shocks) — affects 60ft interpretation"),
        "tire_size":       _ms(car_cfg.get("tire_size",""), "rear tire size — needed to calculate tire RPM and slip"),
        "weight_lbs":      car_cfg.get("car_weight_lbs","") or "NOT SET — needed for G-force and RWHP cross-check",
        "notes":           car_cfg.get("car_notes",""),
    }

    payload = {
        "car_profile": car_profile,
        "current_run": {
            "filename":      csv_name,
            "timeslip": {
                "date":      slip.get("date"),
                "track":     slip.get("track_name") or slip.get("track_location"),
                "reaction":  slip.get("reaction_time"),
                "ft_60":     slip.get("ft_60"),
                "ft_330":    slip.get("ft_330"),
                "ft_660":    slip.get("ft_660"),
                "mph_660":   slip.get("mph_660"),
                "ft_1000":   slip.get("ft_1000"),
                "et_1320":   slip.get("ft_1320"),
                "mph_1320":  slip.get("mph_1320"),
                "issues":    slip.get("issues"),
            },
            "weather": {
                "temp_f":       wx.get("temperature_f"),
                "humidity_pct": wx.get("humidity_pct"),
                "baro_inhg":    round(wx.get("pressure_hpa", 0) * 0.02953, 2) if wx.get("pressure_hpa") else slip.get("baro_inhg"),
                "density_alt_ft": round(
                    slip.get("density_alt_ft") or
                    wx.get("density_alt_ft") or
                    calc_density_altitude(wx.get("temperature_f"), wx.get("humidity_pct"), wx.get("pressure_hpa")) or 0
                ) or None,
                "wind":         slip.get("wind"),
            },
            "run_details":   rd,
            "channel_stats": ch_stats,
            "key_traces":    key_traces,
            "changelog":     run_rec.get("changelog", []),
            "notes":         rd.get("notes", ""),
        },
        "previous_runs": prev_runs,
    }
    return _json.dumps(payload, indent=2, default=str)

_ai_system = """\
IMPORTANT: This analysis is used by real drag racers as a crew chief tool. All facts, labels, and conclusions must be accurate. \
Do not state something is missing when it is present in the data. Do not mislabel calculated values. \
When in doubt, check the payload — the answer is in the data.

You are a seasoned drag racing crew chief with 20+ years running supercharged bracket and heads-up cars. \
You work with whatever data the driver has — timeslip splits, weather, RacePak channel data, changelog, and car specs. \
Not every driver has a data acquisition system. If channel_stats and key_traces are empty, that means no RacePak data is available for this run — \
skip the Channel Analysis section entirely and note at the end that adding a RacePak data logger would enable deeper analysis. \
Never refuse to analyze or say the data is insufficient — work with what is there and deliver maximum value from timeslip splits, weather, and run history. \
Think like a working tuner — specific numbers, direct cause-and-effect. If the data supports a claim, make it. If data is missing, say so and move on.

Respond in these exact sections, in order:

## Run Overview
3–5 sentences: what the car ran, conditions, and the one-line headline (solid pass, soft launch, mechanical flag, etc.).

## Timeslip Breakdown
Walk every split: reaction, 60ft, 330ft, 660ft, 1000ft, ET, MPH. \
Call out strong vs weak splits and what each means mechanically. \
A soft 60ft with a fast mid-track means the car came on strong after the hit. \
An ET gain without a corresponding MPH gain means the improvement came from the launch, not more power. \
Cross-check the ET and MPH against car weight using the Hale formula: RWHP ≈ weight × (mph/234)³. \
This formula produces rear-wheel horsepower (RWHP), not flywheel HP — always label this value as RWHP in your analysis, never as "flywheel HP" or "HP at the flywheel." \
If gear ratios and rear end ratio are provided, validate driveshaft RPM against engine RPM at each gear using: \
  expected_DS_RPM = engine_RPM / (gear_ratio × rear_ratio). Flag if measured DS RPM diverges significantly.

## Cross-Run Comparison
Compare this run against every previous run using raw ET, 60ft, and MPH — do not compute or reference corrected ET. \
Note the DA for each run to give context (lower DA = better air = naturally faster). \
Use the run label field (e.g. "Run 1", "Run 2") to identify each run — never use raw filenames or timestamps. \
If no previous runs exist, say so and move on.

## Changelog Impact
For each changelog entry (parameter changed FROM → TO before this run): \
did it help, hurt, or show no measurable effect? Cite specific split or channel numbers. \
If changelog is empty, say so.

## Channel Analysis
(Skip this section entirely if no RacePak channel data is present — channel_stats and key_traces will be empty.)
Work through the RacePak data systematically:
- **EGTs**: Compute average EGT across all cylinders. Flag any cylinder more than ~75°F above average (lean) or below average (rich/misfire).
- **Boost**: Peak value, time to peak, any top-end falloff. For a roots blower, boost should rise and hold — late falloff can indicate belt slip or insufficient carb capacity. For a screw, expect a sharper initial spike.
- **Fuel pressure**: Stable through the run? Any drop at high RPM signals a supply problem.
- **Driveshaft RPM vs Engine RPM**: Use gear ratios from the car profile to calculate expected DS RPM at each shift point. Flag significant divergence as driveshaft slip or tire spin.
- **G-force (Accel G)**: Peak G, when it occurs, how it trails off. Cross-check 60ft G against what is physically expected for the car weight — a 2,800 lb car can't sustain 2.0 G for 60ft.
- **Oil pressure**: Any dip at the top end signals oil starvation concern. Note peak RPM vs oil pressure minimum.
- Any other channel with a spike, dropout, or unexpected trend.

## Anomalies & Concerns
List anything that looks wrong or needs watching. For each: which channel, when in the run, what value, why it matters. If nothing is anomalous, say "No anomalies detected."

## Missing Specs
Check the car_profile in the data payload for fields whose value contains "NOT SET". List ONLY those fields — do not \
claim a field is missing if it has a value. If every field is populated, write: "All car profile fields are set — full analysis available." \
Never state that a field is missing when its value was provided in the data.

## Next Run Recommendations
Exactly 3 numbered recommendations. Each must state: what to change, the specific target value or direction, and the direct reason from this run's data. No generic advice.

---

ABSOLUTE RULES:

EGT DIRECTION — memorize this and never reverse it:
HIGH EGT (hot cylinder) = LEAN = not enough fuel. LOW EGT (cold cylinder) = RICH or misfiring. \
A cold cylinder is never lean. If you call a cold cylinder lean you are wrong.

FUEL TYPE — adjust all EGT interpretation and fuel flow expectations by fuel type:
- Gasoline: typical EGT range 1,100–1,450°F. Fuel flow readings are moderate. Lean limit is more critical — detonation risk.
- Methanol: typical EGT range 700–1,100°F. EGTs run significantly cooler than gasoline at the same power level. \
  Fuel flow will be roughly 2× gasoline for equivalent power. A methanol car running 1,200°F EGTs is extremely lean. \
  Rich methanol tune is safer; lean is engine-killing.
- Nitromethane: typical EGT range 500–900°F. Carries its own oxygen so it burns very differently. \
  Fuel flow is very high by design. EGT spread interpretation is the same directionally but absolute values are much lower. \
  If fuel_type is NOT SET, note that EGT range interpretation may be off and ask the user to set it.

WEATHER CORRECTION: Density altitude is pre-computed from actual temp/humidity/baro and included in the weather \
block for every run — use it directly, do not say it is missing or estimated. \

BLOWER TYPE CONTEXT:
- Roots blower: boost comes on hard at the hit, then tapers. Boost falloff at top end is normal.
- Screw blower: builds boost more linearly, holds better at high RPM. Expect sharper initial spike on a hi-helix screw.

SUSPENSION CONTEXT:
- Hardtail cars have no rear shock travel — 60ft is entirely dependent on tire prep, tire pressure, and launch RPM. \
  Don't suggest suspension adjustment on a hardtail.
- Cars with shocks: 60ft variability may be shock-related; consider that in your launch analysis.

CLASS & SANCTIONING BODY COACHING:

The car profile includes two fields: sanctioning_body (e.g. NHRA, IHRA, NMCA, PDRA, local track) \
and class_name (e.g. Top Alcohol Dragster, Super Gas, Pro Mod, Bracket).

Use your knowledge of that sanctioning body's rulebook and that class to:
1. Determine whether it is an index class, a dial-in class, or an outright heads-up class.
2. Identify the class index or ET limit if one exists (e.g. NHRA Super Gas = 9.900, Super Comp = 8.900).
3. Identify any known performance limits, weight breaks, power-adder rules, or equipment restrictions.
4. Frame every single recommendation within those class rules.

INDEX / DIAL-IN CLASSES (e.g. Super Gas, Super Comp, Top Dragster, Bracket, Stock, Super Stock): \
Breaking out — running faster than the index or dial-in — is a LOSS. \
NEVER recommend making the car faster if it is already at or under index. \
All coaching must prioritize: (1) hitting the index/dial precisely, (2) run-to-run consistency, \
(3) reaction time. If the car broke out, recommend ways to slow it (pull timing, reduce boost, \
adjust converter stall). If it ran over index, find the lost time in splits and launch.

OUTRIGHT PERFORMANCE CLASSES (e.g. Top Alcohol, Pro Mod, Top Fuel, Funny Car): \
Goal is maximum ET and MPH every pass. Push for more power, better launch, tighter tune. \
Weather-corrected ET improvement is the primary metric.

BRACKET RACING: \
Driver sets their own dial-in. Goal is to run exactly the dial-in with the best possible reaction time. \
Read the dial_in field from the timeslip. Flag any breakout explicitly. \
Pedaling, coasting, or lifting is a valid tuning approach — mention it when relevant.

If sanctioning_body or class_name is NOT SET: \
Default to outright performance coaching, but explicitly tell the user that class-specific coaching \
requires filling in the Sanctioning Body and Class Name fields in the Car Profile sidebar. \
State what specific analysis is being limited by the missing information.

SPECIFICITY: Always quote the actual numbers from the data. \
"Boost peaked at 14.2 psi at 1.8s into the run" not "boost looked good." \
"#3 EGT averaged 180°F below the pack" not "one cylinder ran cold."
"""

_ai_cache_key   = f"ai_response_{csv_name}"
_ai_history_key = f"ai_history_{csv_name}"

# Initialise conversation history if not present
if _ai_history_key not in st.session_state:
    st.session_state[_ai_history_key] = []

_ai_col1, _ai_col2 = st.columns([1, 5])
_run_ai = _ai_col1.button("🤖 Analyze run", key="btn_analyze")
if st.session_state.get(_ai_cache_key):
    _ai_col2.caption("Analysis cached — ask a follow-up below, or click Analyze to re-run")

if _run_ai:
    if not api_key:
        st.warning("⚠️ Add your Anthropic API key in the sidebar to use AI analysis.")
    else:
        with st.spinner("🤖 Analyzing with Claude — comparing all saved runs…"):
            try:
                import anthropic as _anthropic
                _client = _anthropic.Anthropic(api_key=api_key)
                _payload = _build_ai_payload(csv_name, run, df, available_channels, _saved_runs, cfg)
                _first_msg = {"role": "user", "content": f"Here is the run data to analyze:\n\n{_payload}"}
                _msg = _client.messages.create(
                    model="claude-opus-4-8",
                    max_tokens=8192,
                    system=_ai_system,
                    messages=[_first_msg],
                )
                _response_text = _msg.content[0].text
                st.session_state[_ai_cache_key] = _response_text
                # Reset conversation history to just this exchange
                st.session_state[_ai_history_key] = [
                    _first_msg,
                    {"role": "assistant", "content": _response_text},
                ]
            except Exception as _e:
                st.error(f"AI analysis failed: {_e}")

if st.session_state.get(_ai_cache_key):
    with st.container(border=True):
        st.markdown(st.session_state[_ai_cache_key])

    # ── Follow-up conversation ────────────────────────────────────────────────
    _history = st.session_state[_ai_history_key]

    # Render any prior follow-up exchanges (after the initial analysis pair)
    for _turn in _history[2:]:
        _role_label = "🧑 You" if _turn["role"] == "user" else "🤖 Tuner"
        with st.container(border=True):
            st.caption(_role_label)
            st.markdown(_turn["content"])

    # Inline follow-up input
    st.markdown("**💬 Ask a follow-up question**")
    _fu_col1, _fu_col2 = st.columns([5, 1])
    _followup_text = _fu_col1.text_input(
        "follow_up_input",
        label_visibility="collapsed",
        placeholder="e.g. What would you adjust on the fuel curve first?",
        key=f"followup_input_{len(_history)}",
    )
    _send = _fu_col2.button("Send", key=f"followup_send_{len(_history)}")

    if _send and _followup_text.strip():
        if not api_key:
            st.warning("⚠️ API key needed.")
        else:
            with st.spinner("Thinking…"):
                try:
                    import anthropic as _anthropic
                    _client = _anthropic.Anthropic(api_key=api_key)
                    _history.append({"role": "user", "content": _followup_text.strip()})
                    _fmsg = _client.messages.create(
                        model="claude-opus-4-8",
                        max_tokens=2048,
                        system=_ai_system,
                        messages=_history,
                    )
                    _freply = _fmsg.content[0].text
                    _history.append({"role": "assistant", "content": _freply})
                    st.session_state[_ai_history_key] = _history
                    st.rerun()
                except Exception as _e:
                    st.error(f"Follow-up failed: {_e}")

st.markdown("---")

# ── Alerts banner ─────────────────────────────────────────────────────────────
_channel_rules = cfg.get("channel_rules", {})
if _channel_rules and df is not None:
    _alerts = check_alerts(df, time_col, _channel_rules)
    if _alerts:
        _alert_html = ""
        for a in _alerts:
            if a["rule_type"] == "max":
                _icon = "🔴"
                _detail = (
                    f'exceeded maximum of <strong>{a["threshold"]}</strong> — '
                    f'reached <strong>{a["value"]:.1f}</strong> at {a["time_s"]:.2f}s'
                )
            else:
                _icon = "🔵"
                _detail = (
                    f'dropped below minimum of <strong>{a["threshold"]}</strong> — '
                    f'low was <strong>{a["value"]:.1f}</strong> at {a["time_s"]:.2f}s'
                )
            _alert_html += (
                f'<div style="margin:6px 0;font-size:0.95rem;color:#ffffff;">'
                f'{_icon} <strong>{a["channel"]}</strong> {_detail}'
                f'</div>'
            )
        st.markdown(
            f"""<div style="
                background:#cc1111;
                border:2px solid #ff4444;
                border-radius:10px;
                padding:14px 18px;
                margin-bottom:16px;
            ">
            <div style="font-size:1.1rem;font-weight:700;color:#ff9999;margin-bottom:8px;">
                ⚠️ Run Alerts — {len(_alerts)} threshold violation{'s' if len(_alerts)!=1 else ''}
            </div>
            {_alert_html}
            </div>""",
            unsafe_allow_html=True,
        )
        st.markdown("")
    else:
        st.success("✅ All channels within defined limits for this run.", icon="✅")
        st.markdown("")

# Define EGT channels here so they can be excluded from group charts below
_cyl_channels = [ch for ch in ["Cyl #1","Cyl #2","Cyl #3","Cyl #4",
                                "Cyl #5","Cyl #6","Cyl #7","Cyl #8"]
                 if df is not None and ch in df.columns and not df[ch].dropna().empty]
_avg_egt_ch = ("Avg. EGT" if df is not None and "Avg. EGT" in df.columns else None)

# ── Car Profile + Run Details cards ──────────────────────────────────────────
_has_car_profile = any(cfg.get(k) for k in (
    "engine_desc","fuel_type","blower_type","blower_size","carb_desc",
    "converter_desc","transmission","rear_gear_ratio","tire_size",
))
_rd_saved = run.get("run_details", {})
_has_run_details = any(_rd_saved.get(k) for k in ("tire_pressure_fl","track_temp_f",
                                                    "launch_rpm","notes"))

_pc1, _pc2 = st.columns(2)

if _has_car_profile:
        with _pc1:
            _blower_parts = list(filter(None, [
                cfg.get("blower_size", ""),
                cfg.get("blower_type", ""),
                cfg.get("blower_style", ""),
            ]))
            _blower_str = " ".join(_blower_parts) or ""
            _gr = cfg.get("gear_ratios", {})
            _num_g = int(cfg.get("num_gears", 2))
            _ratios_str = "  ".join(
                f"{['1st','2nd','3rd','4th','5th','6th'][i]}: {_gr.get(str(i+1), '?')}"
                for i in range(_num_g) if _gr.get(str(i+1))
            )
            _profile_rows = ""
            for _label, _val in [
                ("Engine",       cfg.get("engine_desc", "")),
                ("Fuel",         cfg.get("fuel_type", "")),
                ("Carb / Fuel",  cfg.get("carb_desc", "")),
                ("Blower",       _blower_str),
                ("Converter",    cfg.get("converter_desc", "")),
                ("Transmission", cfg.get("transmission", "")),
                ("Gear Ratios",  _ratios_str),
                ("Rear Ratio",   cfg.get("rear_gear_ratio", "")),
                ("Suspension",   cfg.get("suspension_type", "")),
                ("Rear Tire",    cfg.get("tire_size", "")),
                ("Weight",       f"{weight_input:,} lbs" if weight_input else ""),
            ]:
                if _val:
                    _profile_rows += (
                        f'<tr><td style="color:#888;padding:3px 8px 3px 0;white-space:nowrap;">{_label}</td>'
                        f'<td style="color:#eee;text-align:right;">{_val}</td></tr>'
                    )
            st.markdown(f"""
<div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;
  background:#0a0a0a;font-family:monospace;">
  <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:10px;
    border-bottom:1px solid #2a0000;padding-bottom:6px;">
    🏎️ Car Profile
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:0.92rem;">{_profile_rows}</table>
</div>""", unsafe_allow_html=True)

with _pc2:
    def _rd_row(label, val, fmt=None, unit="", highlight=False):
        """Return an HTML table row, or '' if val is falsy/zero."""
        if val is None or val == "" or val == 0 or val == 0.0:
            return ""
        display = fmt.format(val) if fmt else str(val)
        if unit:
            display = f"{display} {unit}"
        color = "#cc1111" if highlight else "#eee"
        fw = "font-weight:700;" if highlight else ""
        return (f'<tr><td style="color:#888;padding:2px 8px 2px 0;white-space:nowrap;">{label}</td>'
                f'<td style="color:{color};{fw}text-align:right;">{display}</td></tr>')

    def _rd_section(title, rows_html):
        """Wrap a non-empty group of rows with a section header row."""
        if not rows_html:
            return ""
        return (f'<tr><td colspan="2" style="color:#666;font-size:0.78rem;padding:6px 0 2px;'
                f'letter-spacing:0.05em;text-transform:uppercase;border-top:1px solid #1e1e2a;">'
                f'{title}</td></tr>' + rows_html)

    _r = _rd_saved  # shorthand

    # ── Tires ──
    _tfl = _r.get("tire_pressure_fl", 0) or 0
    _tfr = _r.get("tire_pressure_fr", 0) or 0
    _trl = _r.get("tire_pressure_rl", 0) or 0
    _trr = _r.get("tire_pressure_rr", 0) or 0
    _tire_rows = ""
    if _tfl or _tfr:
        _tire_rows += (f'<tr><td style="color:#888;padding:2px 8px 2px 0;">Tire Press FL / FR</td>'
                       f'<td style="color:#eee;text-align:right;">{_tfl:.1f} / {_tfr:.1f} psi</td></tr>')
    if _trl or _trr:
        _tire_rows += (f'<tr><td style="color:#888;padding:2px 8px 2px 0;">Tire Press RL / RR</td>'
                       f'<td style="color:#eee;text-align:right;">{_trl:.1f} / {_trr:.1f} psi</td></tr>')

    # ── Track / Tire Conditions ──
    _cond_rows  = _rd_row("Track Temp",  _r.get("track_temp_f")  or 0, "{:.0f}", "°F")
    _cond_rows += _rd_row("Tire Temp",   _r.get("tire_temp_f")   or 0, "{:.0f}", "°F")

    # ── RPM ──
    _rpm_rows  = _rd_row("Launch RPM",  _r.get("launch_rpm")   or 0, "{:,}")
    _rpm_rows += _rd_row("Shift Point", _r.get("shift_point")  or 0, "{:,}", "RPM", highlight=True)

    # ── Fuel System ──
    _fuel_rows  = _rd_row("Main Jet",    _r.get("main_jet")     or 0, "{:.3f}")
    _fuel_rows += _rd_row("HS Jet",      _r.get("hs_jet")       or 0, "{:.3f}")
    _fuel_rows += _rd_row("HS Open PSI", _r.get("hs_open_psi")  or 0, "{:.0f}", "psi")

    # ── Blower ──
    _tp  = _r.get("top_pulley",    0) or 0
    _bp  = _r.get("bottom_pulley", 0) or 0
    _od  = _r.get("overdrive",     None)
    if _od is None and _tp:
        _od = (_bp / _tp - 1) if _tp else 0.0
    _blow_rows = ""
    if _tp:
        _blow_rows += (f'<tr><td style="color:#888;padding:2px 8px 2px 0;">Top / Bottom Pulley</td>'
                       f'<td style="color:#eee;text-align:right;">{_tp}" / {_bp}"</td></tr>')
    if _od is not None and _od != 0:
        _blow_rows += (f'<tr><td style="color:#888;padding:2px 8px 2px 0;">Overdrive</td>'
                       f'<td style="color:#eee;text-align:right;">{_od*100:.2f}%</td></tr>')
    _wbd = _r.get("wheelie_bar_d", 0) or 0
    _wbp = _r.get("wheelie_bar_p", 0) or 0
    if _wbd or _wbp:
        _blow_rows += (f'<tr><td style="color:#888;padding:2px 8px 2px 0;">Wheelie Bar D / P</td>'
                       f'<td style="color:#eee;text-align:right;">{_wbd:.3f} / {_wbp:.3f}</td></tr>')

    # ── Ignition ──
    _ign_rows  = _rd_row("Spark Plug", _r.get("spark_plug", ""))
    _ign_rows += _rd_row("Plug Gap",   _r.get("plug_gap",   ""))
    _ign_rows += _rd_row("Lash INT/EXT", _r.get("valve_lash", ""))

    # ── Assemble all sections ──
    _rd_rows = (
        _rd_section("Tires",                  _tire_rows) +
        _rd_section("Track / Tire Conditions", _cond_rows) +
        _rd_section("RPM",                    _rpm_rows)  +
        _rd_section("Fuel System",            _fuel_rows) +
        _rd_section("Blower",                 _blow_rows) +
        _rd_section("Ignition",               _ign_rows)
    )

    if not _rd_rows:
        _rd_rows = ('<tr><td colspan="2" style="color:#555;font-style:italic;padding:4px 0;">'
                    'No details saved yet — open Run Details above to add some.</td></tr>')

    _rnotes = _r.get("notes", "")
    _notes_html = ""
    if _rnotes:
        _notes_html = (f'<div style="margin-top:10px;color:#aaa;font-size:0.82rem;'
                       f'border-top:1px solid #2a0000;padding-top:8px;white-space:pre-wrap;">'
                       f'{_rnotes}</div>')

    st.markdown(f"""
<div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;
  background:#0a0a0a;font-family:monospace;">
  <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:10px;
    border-bottom:1px solid #2a0000;padding-bottom:6px;">
    📋 Run Details
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:0.88rem;">{_rd_rows}</table>
  {_notes_html}
</div>""", unsafe_allow_html=True)

st.markdown("---")

# ── Timeslip + Weather cards ──────────────────────────────────────────────────
slip = run.get("timeslip")
wx = run.get("weather")

if slip or wx:
    left, right = st.columns(2)

    # ── Timeslip card
    if slip:
        with left:
            track = slip.get("track_name") or "—"
            run_date = slip.get("date") or "—"
            run_time = slip.get("time") or "—"

            st.markdown(f"""
<div style="
  border:1px solid #8b0000;
  border-radius:10px;
  padding:16px 20px;
  background:#0a0a0a;
  font-family: monospace;
">
  <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:6px;
    border-bottom:1px solid #2a0000;padding-bottom:6px;">
    🎫 Timeslip — {track}
  </div>
  <div style="color:#666;font-size:0.8rem;margin-bottom:12px;">{run_date} &nbsp;·&nbsp; {run_time}</div>
  <table style="width:100%;border-collapse:collapse;font-size:0.92rem;">
    <tr>
      <td style="color:#888;padding:3px 8px 3px 0;">Reaction</td>
      <td style="color:#eee;text-align:right;">{slip.get('reaction_time') or '—'}</td>
      <td style="width:24px;"></td>
      <td style="color:#888;padding:3px 8px 3px 0;">Lane</td>
      <td style="color:#eee;text-align:right;">{(slip.get('lane') or '—').title()}</td>
    </tr>
    <tr>
      <td style="color:#888;padding:3px 8px 3px 0;">60 ft</td>
      <td style="color:#eee;text-align:right;">{slip.get('ft_60') or '—'}</td>
      <td></td>
      <td style="color:#888;padding:3px 8px 3px 0;">Car #</td>
      <td style="color:#eee;text-align:right;">{slip.get('car_number') or '—'}</td>
    </tr>
    <tr>
      <td style="color:#888;padding:3px 8px 3px 0;">330 ft</td>
      <td style="color:#eee;text-align:right;">{slip.get('ft_330') or '—'}</td>
      <td></td>
      <td style="color:#888;padding:3px 8px 3px 0;">Dial-In</td>
      <td style="color:#eee;text-align:right;">{slip.get('dial_in') or '—'}</td>
    </tr>
    <tr>
      <td style="color:#888;padding:3px 8px 3px 0;">660 ft</td>
      <td style="color:#eee;text-align:right;">{slip.get('ft_660') or '—'}</td>
      <td></td>
      <td style="color:#888;padding:3px 8px 3px 0;">660 MPH</td>
      <td style="color:#eee;text-align:right;">{slip.get('mph_660') or '—'}</td>
    </tr>
    <tr>
      <td style="color:#888;padding:3px 8px 3px 0;">1000 ft</td>
      <td style="color:#eee;text-align:right;">{slip.get('ft_1000') or '—'}</td>
      <td></td>
      <td></td><td></td>
    </tr>
    <tr style="border-top:1px solid #2a0000;">
      <td style="color:#cc1111;font-weight:700;padding:6px 8px 3px 0;">ET</td>
      <td style="color:#cc1111;font-weight:700;font-size:1.2rem;text-align:right;">{slip.get('ft_1320') or '—'}</td>
      <td></td>
      <td style="color:#cc1111;font-weight:700;padding:6px 8px 3px 0;">MPH</td>
      <td style="color:#cc1111;font-weight:700;font-size:1.2rem;text-align:right;">{slip.get('mph_1320') or '—'}</td>
    </tr>
  </table>
</div>
""", unsafe_allow_html=True)

    # ── Weather card
    if wx:
        with right:
            temp = wx.get("temperature_f")
            hum = wx.get("humidity_pct")
            pres = wx.get("pressure_hpa")
            wind = wx.get("windspeed_mph")
            wdir = wind_dir_label(wx.get("wind_dir_deg"))
            wx_date = run.get("weather_date", "")
            wx_loc = run.get("weather_location", "")

            pres_inhg = f"{pres * 0.02953:.2f} inHg" if pres else "—"
            da = calc_density_altitude(temp, hum, pres)
            da_str = f"{da:,.0f} ft" if da is not None else "—"
            # Color: high DA = bad (hot/humid), low DA = good (dense air)
            da_color = "#ff6b6b" if (da or 0) > 2000 else "#60c0f0" if (da or 0) < 500 else "#f0c040"
            da_note = "thin air" if (da or 0) > 2000 else "good air" if (da or 0) < 500 else "average air"

            st.markdown(f"""
<div style="
  border:1px solid #8b0000;
  border-radius:10px;
  padding:16px 20px;
  background:#0a0a0a;
  font-family: monospace;
">
  <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:6px;
    border-bottom:1px solid #2a0000;padding-bottom:6px;">
    🌤️ Weather at Run Time
  </div>
  <div style="color:#666;font-size:0.8rem;margin-bottom:12px;">{wx_date} &nbsp;·&nbsp; {wx_loc}</div>
  <table style="width:100%;border-collapse:collapse;font-size:0.92rem;">
    <tr>
      <td style="color:#888;padding:4px 8px 4px 0;">🌡️ Temperature</td>
      <td style="color:#eee;text-align:right;font-size:1.1rem;font-weight:600;">{f"{temp:.1f} °F" if temp is not None else "—"}</td>
    </tr>
    <tr>
      <td style="color:#888;padding:4px 8px 4px 0;">💧 Humidity</td>
      <td style="color:#eee;text-align:right;font-size:1.1rem;font-weight:600;">{f"{hum:.0f}%" if hum is not None else "—"}</td>
    </tr>
    <tr>
      <td style="color:#888;padding:4px 8px 4px 0;">🔵 Barometric Pressure</td>
      <td style="color:#eee;text-align:right;font-size:1.1rem;font-weight:600;">{pres_inhg}</td>
    </tr>
    <tr>
      <td style="color:#888;padding:4px 8px 4px 0;">💨 Wind</td>
      <td style="color:#eee;text-align:right;font-size:1.1rem;font-weight:600;">{f"{wind:.1f} mph {wdir}" if wind is not None else "—"}</td>
    </tr>
    <tr style="border-top:1px solid #2a0000;">
      <td style="color:{da_color};font-weight:700;padding:6px 8px 3px 0;">📐 Density Altitude</td>
      <td style="color:{da_color};font-weight:700;font-size:1.2rem;text-align:right;">{da_str}</td>
    </tr>
    <tr>
      <td></td>
      <td style="color:#666;font-size:0.78rem;text-align:right;">{da_note}</td>
    </tr>
  </table>
</div>
""", unsafe_allow_html=True)

# ── HP Calculator card ────────────────────────────────────────────────────────
if slip and weight_input:
    et_val = slip.get("ft_1320")
    mph_val = slip.get("mph_1320")
    hp = calc_rwhp(weight_input, et_val, mph_val)

    if hp:
        st.markdown("### ⚡ Estimated Rear-Wheel Horsepower")
        hp_cols = st.columns(len(hp))
        labels = {
            "from_mph": ("From Trap Speed", f"{mph_val} mph", "Most accurate"),
            "from_et":  ("From ET",          f"{et_val}s",    "Good estimate"),
        }
        for col, (key, hp_val) in zip(hp_cols, hp.items()):
            lbl, source, note = labels[key]
            col.markdown(f"""
<div style="
  border:1px solid #8b0000;
  border-radius:10px;
  padding:16px 20px;
  background:#0a0a0a;
  font-family:monospace;
  text-align:center;
">
  <div style="color:#888;font-size:0.8rem;margin-bottom:4px;">{lbl}</div>
  <div style="color:#ffffff;font-size:2rem;font-weight:700;">{hp_val:,.0f}</div>
  <div style="color:#cc1111;font-size:0.9rem;font-weight:600;">RWHP</div>
  <div style="color:#666;font-size:0.75rem;margin-top:6px;">{source} · {note}</div>
  <div style="color:#444;font-size:0.7rem;margin-top:2px;">{weight_input:,} lbs w/driver</div>
</div>
""", unsafe_allow_html=True)

    st.markdown("---")

elif _slip_bytes is None:
    st.info("📎 Upload a timeslip photo in the sidebar to add run data and auto-fetch weather.", icon="🎫")
    st.markdown("---")

# ── Channel charts (one chart per group, all channels overlaid) ───────────────
if not _csv_available:
    st.stop()

# EGT channels are shown in the dedicated EGT panel above — skip here
_egt_group_name = "🌡️ EGT (Exhaust Temps)"
_egt_chs_set = set(_cyl_channels) | ({_avg_egt_ch} if _avg_egt_ch else set())

for grp in selected_groups:
    if grp == _egt_group_name:
        continue  # already rendered in EGT panel
    if grp in CHANNEL_GROUPS:
        grp_channels = [ch for ch in CHANNEL_GROUPS[grp]
                        if ch in available_channels and ch not in _egt_chs_set]
    else:
        grp_channels = [ch for ch in available_channels
                        if channel_to_group.get(ch) == grp and ch not in _egt_chs_set]

    if not grp_channels:
        continue

    fig = make_overlay_chart(grp_channels, grp, time_col, df_view, t_range, mode, chart_height, dark=_dark_mode)
    if fig:
        st.markdown(f"### {grp}")
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("---")

# ── Custom Overlay chart ──────────────────────────────────────────────────────
if custom_channels:
    fig = make_overlay_chart(custom_channels, "Custom Overlay", time_col, df_view, t_range, mode, chart_height, dark=_dark_mode)
    if fig:
        st.markdown("### 🔀 Custom Overlay")
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("---")

# ── EGT Full Panel ────────────────────────────────────────────────────────────
if _cyl_channels:
    st.markdown("### 🌡️ Exhaust Gas Temperatures")

    _egt_max = None
    for _c in _cyl_channels:
        if _c in _channel_rules and "max" in _channel_rules[_c]:
            _v = _channel_rules[_c]["max"]
            _egt_max = _v if _egt_max is None else min(_egt_max, _v)

    _cyl_peaks = {ch: float(df[ch].dropna().max()) for ch in _cyl_channels}
    _cyl_mins  = {ch: float(df[ch].dropna().min()) for ch in _cyl_channels}
    _avg_egt   = sum(_cyl_peaks.values()) / len(_cyl_peaks)
    _overall_max = max(_cyl_peaks.values())
    _overall_min = min(_cyl_peaks.values())
    _spread = _overall_max - _overall_min
    _hottest = max(_cyl_peaks, key=_cyl_peaks.get)
    _coldest = min(_cyl_peaks, key=_cyl_peaks.get)

    EGT_SPREAD_LIMIT = 50

    _es1, _es2, _es3, _es4, _es5 = st.columns(5)
    _es1.metric("Avg Peak EGT", f"{_avg_egt:,.0f} °F")
    _es2.metric("Hottest Cyl", f"{_hottest.replace('Cyl ','')}", f"{_cyl_peaks[_hottest]:,.0f} °F")
    _es3.metric("Coldest Cyl", f"{_coldest.replace('Cyl ','')}", f"{_cyl_peaks[_coldest]:,.0f} °F")
    _spread_flag = "⚠️ " if _spread > EGT_SPREAD_LIMIT else "✅ "
    _es4.metric(f"{_spread_flag}Spread (hot−cold)", f"{_spread:,.0f} °F",
                delta=f"limit ±{EGT_SPREAD_LIMIT}°F",
                delta_color="off" if _spread <= EGT_SPREAD_LIMIT else "inverse")
    if _egt_max:
        _pct_of_limit = (_overall_max / _egt_max) * 100
        _es5.metric("Peak % of Limit", f"{_pct_of_limit:.1f}%",
                    delta=f"limit {_egt_max:,}°F", delta_color="inverse")

    def _egt_color(val):
        if _egt_max:
            ratio = val / _egt_max
            if ratio < 0.85:   return "#4da6ff"
            elif ratio < 0.95: return "#00CC96"
            elif ratio <= 1.0: return "#FFA15A"
            else:              return "#EF553B"
        else:
            ratio = (val - _overall_min) / max((_overall_max - _overall_min), 1)
            if ratio < 0.25:   return "#4da6ff"
            elif ratio < 0.6:  return "#00CC96"
            elif ratio < 0.85: return "#FFA15A"
            else:              return "#EF553B"

    def _egt_rel_color(val):
        d = val - _avg_egt
        if d < -EGT_SPREAD_LIMIT:           return "#4488ff"
        if d < -(EGT_SPREAD_LIMIT / 2):     return "#66aaff"
        if d <= (EGT_SPREAD_LIMIT / 2):     return "#44cc66"
        if d <= EGT_SPREAD_LIMIT:           return "#ffcc00"
        return                                      "#ff4444"

    def _egt_status(val):
        d = val - _avg_egt
        if d < -EGT_SPREAD_LIMIT:           return f"COLD (>{EGT_SPREAD_LIMIT}° below avg)"
        if d < -(EGT_SPREAD_LIMIT / 2):     return "Cool — watch"
        if d <= (EGT_SPREAD_LIMIT / 2):     return "Normal"
        if d <= EGT_SPREAD_LIMIT:           return "Warm — watch"
        return                                      f"HOT (>{EGT_SPREAD_LIMIT}° above avg)"

    _short_names = [ch.replace("Cyl #", "#") for ch in _cyl_channels]
    _colors = [_egt_color(_cyl_peaks[ch]) for ch in _cyl_channels]

    _ecol_bar, _ecol_eng = st.columns([1, 1])

    with _ecol_bar:
        st.caption("**Peak temp per cylinder**")
        _bar_fig = go.Figure()
        _bar_fig.add_trace(go.Bar(
            x=_short_names,
            y=[_cyl_peaks[ch] for ch in _cyl_channels],
            marker_color=_colors,
            text=[f"{_cyl_peaks[ch]:,.0f}°" for ch in _cyl_channels],
            textposition="outside",
            textfont=dict(size=12, color="white"),
            width=0.6,
        ))
        _bar_fig.add_hline(y=_avg_egt, line_dash="dash", line_color="#FECB52", line_width=1.5,
            annotation_text=f"Avg {_avg_egt:,.0f}°", annotation_position="top right",
            annotation_font=dict(color="#FECB52", size=10))
        if _egt_max:
            _bar_fig.add_hline(y=_egt_max, line_dash="dot", line_color="#EF553B", line_width=1.5,
                annotation_text=f"Limit {_egt_max:,}°", annotation_position="top left",
                annotation_font=dict(color="#EF553B", size=10))
        _bar_fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0),
            template="plotly_dark" if _dark_mode else "plotly_white", showlegend=False,
            yaxis=dict(title="Peak EGT (°F)", range=[0, max(_egt_max or 0, _overall_max) * 1.18]),
            xaxis=dict(title="Cylinder"), bargap=0.3)
        st.plotly_chart(_bar_fig, use_container_width=True)

    with _ecol_eng:
        st.caption("**Engine layout — color = relative to avg**")
        _fig_eng = go.Figure()
        for _bank_x, _bank_cyls in [(0.28, ["Cyl #1","Cyl #3","Cyl #5","Cyl #7"]),
                                     (0.72, ["Cyl #2","Cyl #4","Cyl #6","Cyl #8"])]:
            for _row, _ch in enumerate(_bank_cyls):
                _y_pos = 3 - _row
                if _ch not in _cyl_peaks:
                    continue
                _val = _cyl_peaks[_ch]
                _delta = _val - _avg_egt
                _d_str = f"+{_delta:.0f}" if _delta >= 0 else f"{_delta:.0f}"
                _fig_eng.add_trace(go.Scatter(x=[_bank_x], y=[_y_pos], mode="markers",
                    marker=dict(symbol="square", size=62, color=_egt_rel_color(_val),
                                line=dict(color="#222", width=2)),
                    hovertemplate=(f"<b>{_ch}</b><br>Peak: {_val:.0f} °F<br>"
                                   f"Δ avg: {_d_str}°<br>Status: {_egt_status(_val)}<extra></extra>"),
                    showlegend=False))
                _fig_eng.add_annotation(x=_bank_x, y=_y_pos + 0.13,
                    text=f"<b>{_ch.replace('Cyl #','#')}</b>",
                    showarrow=False, font=dict(size=12, color="#111", family="Arial Black"))
                _fig_eng.add_annotation(x=_bank_x, y=_y_pos - 0.14,
                    text=f"{_val:.0f}°", showarrow=False, font=dict(size=10, color="#111"))

        for _lbl, _x in [("Left Bank", 0.28), ("Right Bank", 0.72)]:
            _fig_eng.add_annotation(x=_x, y=3.75, text=_lbl,
                showarrow=False, font=dict(size=11, color="#999"))
        _fig_eng.add_annotation(x=0.5, y=3.75, text="▲ Front",
            showarrow=False, font=dict(size=10, color="#555"))
        _fig_eng.add_annotation(x=0.5, y=-0.45, text="▼ Rear",
            showarrow=False, font=dict(size=10, color="#555"))
        for _lx, (_lc, _ll) in zip([0.03, 0.22, 0.44, 0.65, 0.83],
            [("#4488ff",f"Cold (>{EGT_SPREAD_LIMIT}°)"), ("#66aaff","Cool"),
             ("#44cc66","Normal"), ("#ffcc00","Warm"), ("#ff4444",f"Hot (>{EGT_SPREAD_LIMIT}°)")]):
            _fig_eng.add_trace(go.Scatter(x=[_lx], y=[-0.85], mode="markers",
                marker=dict(symbol="square", size=12, color=_lc),
                showlegend=False, hoverinfo="skip"))
            _fig_eng.add_annotation(x=_lx + 0.06, y=-0.85, text=_ll,
                showarrow=False, font=dict(size=9, color="#aaa"), xanchor="left")
        _fig_eng.update_layout(height=320, template="plotly_dark" if _dark_mode else "plotly_white",
            margin=dict(l=10, r=10, t=20, b=10),
            xaxis=dict(visible=False, range=[0, 1]),
            yaxis=dict(visible=False, range=[-1.1, 4.2]),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(_fig_eng, use_container_width=True)

    st.caption("**EGT over time — all cylinders**")
    _ts_channels = _cyl_channels + ([_avg_egt_ch] if _avg_egt_ch else [])
    _ts_fig = make_overlay_chart(_ts_channels, "EGT", time_col, df_view, t_range, mode, 320, dark=_dark_mode)
    if _ts_fig:
        for trace in _ts_fig.data:
            if trace.name in _cyl_peaks:
                trace.line.color = _egt_color(_cyl_peaks[trace.name])
            elif trace.name == _avg_egt_ch:
                trace.line.color = "#FECB52"
                trace.line.dash = "dash"
        st.plotly_chart(_ts_fig, use_container_width=True)

    _spread_note = (
        f"Engine diagram: 🔵 Cold (>{EGT_SPREAD_LIMIT}° below avg)  &nbsp;·&nbsp; "
        f"🔷 Cool (>{EGT_SPREAD_LIMIT//2}° below)  &nbsp;·&nbsp; "
        f"🟢 Normal (within ±{EGT_SPREAD_LIMIT//2}°)  &nbsp;·&nbsp; "
        f"🟡 Warm (>{EGT_SPREAD_LIMIT//2}° above)  &nbsp;·&nbsp; "
        f"🔴 Hot (>{EGT_SPREAD_LIMIT}° above avg)"
    )
    if _egt_max:
        st.caption(f"Bar/line colors — 🔵 Cold (<85% of limit)  &nbsp;·&nbsp; "
                   f"🟢 Good (85–95%)  &nbsp;·&nbsp; 🟠 Warm (95–100%)  &nbsp;·&nbsp; "
                   f"🔴 Over limit  &nbsp;&nbsp;|&nbsp;&nbsp;  {_spread_note}")
    else:
        st.caption(f"Bar/line colors relative to min/max of this run  &nbsp;&nbsp;|&nbsp;&nbsp;  {_spread_note}  —  "
                   f"Set a max rule for any Cyl channel in Channel Rules to enable absolute threshold coloring")

    st.markdown("---")

# ── Run Summary ──────────────────────────────────────────────────────────────
st.markdown("## 📊 Run Summary")

def _fmt(val, fmt="{}", fallback="—"):
    """Format a value, returning fallback if None/empty/zero."""
    try:
        if val is None or val == "" or val == 0 or val == 0.0:
            return fallback
        return fmt.format(val)
    except Exception:
        return str(val) if val else fallback

_sum_slip = run.get("timeslip", {})
_sum_wx   = run.get("weather", {})
_sum_rd   = run.get("run_details", {})

# ── GENERAL + WEATHER ─────────────────────────────────────────────────────────
_sc1, _sc2 = st.columns(2)

with _sc1:
    st.markdown("##### 📍 General")
    _g_rows = [
        ("Track",  _sum_slip.get("track_name") or _sum_slip.get("track_location") or "—"),
        ("Date",   _sum_slip.get("date", "—")),
        ("Time",   _sum_slip.get("time", "—")),
        ("Lane",   _sum_slip.get("lane", "—")),
    ]
    for _lbl, _val in _g_rows:
        _gc1, _gc2 = st.columns([2, 3])
        _gc1.caption(_lbl)
        _gc2.markdown(f"**{_val}**")

with _sc2:
    st.markdown("##### 🌤️ Weather")
    # Prefer timeslip-extracted values (printed on the slip); fall back to weather API
    _w_temp    = _sum_slip.get("temp_f")       or _sum_wx.get("temperature_f")
    _w_humid   = _sum_slip.get("humidity_pct") or _sum_wx.get("humidity_pct")
    # Baro: timeslip gives inHg directly; API gives hPa → convert
    _w_baro_hpa = _sum_wx.get("pressure_hpa")
    _w_baro    = _sum_slip.get("baro_inhg") or (_w_baro_hpa * 0.02953 if _w_baro_hpa else None)
    # Wind: timeslip gives "14.25 SE" string; API gives speed + direction degrees
    _w_wind_spd = _sum_wx.get("windspeed_mph")
    _w_wind_dir = wind_dir_label(_sum_wx.get("wind_dir_deg"))
    _w_wind    = _sum_slip.get("wind") or (f"{_w_wind_spd:.1f} {_w_wind_dir}" if _w_wind_spd else None)
    # Density alt: timeslip if present, otherwise compute from API values
    _w_da      = _sum_slip.get("density_alt_ft") or calc_density_altitude(_w_temp, _w_humid, _w_baro_hpa)
    _w_rows = [
        ("Temp",         _fmt(_w_temp,  "{:.1f} °F")),
        ("Baro. Press.", _fmt(_w_baro,  "{:.2f} in")),
        ("Humidity",     _fmt(_w_humid, "{:.0f} %")),
        ("Wind",         str(_w_wind) if _w_wind else "—"),
        ("Density Alt.", _fmt(_w_da,    "{:,.0f} ft")),
    ]
    for _lbl, _val in _w_rows:
        _wc1, _wc2 = st.columns([2, 3])
        _wc1.caption(_lbl)
        _wc2.markdown(f"**{_val}**")

st.markdown("---")

# ── RUN RESULTS ───────────────────────────────────────────────────────────────
st.markdown("##### 🏁 Run Results")
_rr_cols = st.columns(9)
_rr_headers = ["R/T", "60'", "330'", "1/8 Mile", "1/8 MPH", "1000'", "1/4 Mile", "1/4 MPH", "Issues"]
_rr_vals = [
    _fmt(_sum_slip.get("reaction_time") or _sum_slip.get("rt"), "{:.3f}"),
    _fmt(_sum_slip.get("ft_60")  or _sum_slip.get("et_60"),   "{:.3f}"),
    _fmt(_sum_slip.get("ft_330") or _sum_slip.get("et_330"),  "{:.3f}"),
    _fmt(_sum_slip.get("ft_660") or _sum_slip.get("et_660") or _sum_slip.get("et_eighth"), "{:.3f}"),
    _fmt(_sum_slip.get("mph_660") or _sum_slip.get("mph_eighth"), "{:.2f}"),
    _fmt(_sum_slip.get("ft_1000") or _sum_slip.get("et_1000"), "{:.3f}"),
    _fmt(_sum_slip.get("ft_1320") or _sum_slip.get("et_quarter") or _sum_slip.get("et"), "{:.3f}"),
    _fmt(_sum_slip.get("mph_1320") or _sum_slip.get("mph_quarter") or _sum_slip.get("mph"), "{:.2f}"),
    _sum_slip.get("issues") or "—",
]
for _col, _hdr, _val in zip(_rr_cols, _rr_headers, _rr_vals):
    _col.caption(_hdr)
    _col.markdown(f"**{_val}**")

# RacePak peak row
st.markdown("##### 📡 RacePak Peaks")
_rp_items = []
for _rp_ch, _rp_lbl in [
    ("Engine RPM", "Peak RPM"), ("Boost Press", "Peak Boost (psi)"),
    ("Fuel Press", "Peak Fuel PSI"), ("Fuel Flow", "Peak Fuel Flow"),
    ("Oil Press", "Min Oil PSI"), ("Trans Temp", "Peak Trans Temp"),
    ("Accel G", "Peak G"),
]:
    if _rp_ch in df.columns:
        _s = df[_rp_ch].dropna()
        if not _s.empty:
            _rp_items.append((_rp_lbl, _s.min() if "Min" in _rp_lbl else _s.max()))

if _rp_items:
    _rp_cols = st.columns(len(_rp_items))
    for _col, (_lbl, _val) in zip(_rp_cols, _rp_items):
        _col.caption(_lbl)
        _col.markdown(f"**{_val:,.1f}**")

st.markdown("---")

# ── TUNING ────────────────────────────────────────────────────────────────────
st.markdown("##### 🔧 Tuning")

_t1, _t2, _t3, _t4 = st.columns(4)

with _t1:
    st.caption("**Fuel System**")
    _rp_fuel_psi  = df["Fuel Press"].dropna().max() if df is not None and "Fuel Press" in df.columns else None
    _rp_fuel_flow = df["Fuel Flow"].dropna().max()  if df is not None and "Fuel Flow"  in df.columns else None
    _tune_fuel = [
        ("Main Jet",      _fmt(_sum_rd.get("main_jet"),  "{:.3f}")),
        ("Max Fuel PSI",  _fmt(_rp_fuel_psi,             "{:.1f}") + " ⚡" if _rp_fuel_psi else "—"),
        ("Max Fuel Flow", _fmt(_rp_fuel_flow,            "{:.3f}") + " ⚡" if _rp_fuel_flow else "—"),
        ("HS Jet",        _fmt(_sum_rd.get("hs_jet"),    "{:.3f}")),
        ("HS Open PSI",   _fmt(_sum_rd.get("hs_open_psi"), "{:.0f}")),
    ]
    for _lbl, _val in _tune_fuel:
        _a, _b = st.columns([3, 2])
        _a.caption(_lbl)
        _b.markdown(f"**{_val}**")

with _t2:
    st.caption("**Blower**")
    _sum_tp = _sum_rd.get("top_pulley", 0)
    _sum_bp = _sum_rd.get("bottom_pulley", 0)
    _sum_od = ((_sum_bp / _sum_tp) - 1) if _sum_tp else _sum_rd.get("overdrive", 0)
    _sum_boost = df["Boost Press"].dropna().max() if df is not None and "Boost Press" in df.columns else None
    _tune_blow = [
        ("Top Pulley",    _fmt(_sum_tp, "{:.0f}")),
        ("Bottom Pulley", _fmt(_sum_bp, "{:.0f}")),
        ("Overdrive",     f"{_sum_od * 100:.2f}%" if _sum_tp else "—"),
        ("Peak Boost",    _fmt(_sum_boost, "{:.1f} psi") + " ⚡" if _sum_boost else "—"),
        ("W/B – D",       _fmt(_sum_rd.get("wheelie_bar_d"), "{:.3f}\"")),
        ("W/B – P",       _fmt(_sum_rd.get("wheelie_bar_p"), "{:.3f}\"")),
    ]
    for _lbl, _val in _tune_blow:
        _a, _b = st.columns([3, 2])
        _a.caption(_lbl)
        _b.markdown(f"**{_val}**")

with _t3:
    st.caption("**Tires & Track**")
    _tune_tire = [
        ("Front PSI",    _fmt(_sum_rd.get("tire_pressure_fl") or _sum_rd.get("tire_pressure_fr"),
                              "{:.1f}")),
        ("Rear PSI",     _fmt(_sum_rd.get("tire_pressure_rl") or _sum_rd.get("tire_pressure_rr"),
                              "{:.1f}")),
        ("Track Temp",   _fmt(_sum_rd.get("track_temp_f"),  "{:.0f} °F")),
        ("Tire Temp",    _fmt(_sum_rd.get("tire_temp_f"),   "{:.0f} °F")),
    ]
    for _lbl, _val in _tune_tire:
        _a, _b = st.columns([3, 2])
        _a.caption(_lbl)
        _b.markdown(f"**{_val}**")

with _t4:
    st.caption("**Ignition & RPM**")
    _tune_ign = [
        ("Launch RPM",   _fmt(_sum_rd.get("launch_rpm"),   "{:,}")),
        ("Shift Point",  _fmt(_sum_rd.get("shift_point"),  "{:,}")),
        ("Spark Plug",   _sum_rd.get("spark_plug")  or "—"),
        ("Plug Gap",     _sum_rd.get("plug_gap")    or "—"),
        ("Valve Lash",   _sum_rd.get("valve_lash")  or "—"),
    ]
    for _lbl, _val in _tune_ign:
        _a, _b = st.columns([3, 2])
        _a.caption(_lbl)
        _b.markdown(f"**{_val}**")

# Notes row
if _sum_rd.get("notes"):
    st.caption(f"📝 Notes: {_sum_rd['notes']}")

# ── Export all runs ───────────────────────────────────────────────────────────
st.markdown("---")

def _build_export_row(filename: str, rec: dict) -> dict:
    """Build one summary row for a saved run, pulling from its JSON record."""
    import re as _re
    _slip = rec.get("timeslip", {})
    _wx   = rec.get("weather",  {})
    _rd   = rec.get("run_details", {})
    _cl   = rec.get("changelog", [])

    # Density altitude: prefer pre-computed value, fall back to calculation
    _da = (
        _slip.get("density_alt_ft")
        or _wx.get("density_alt_ft")
        or calc_density_altitude(_wx.get("temperature_f"), _wx.get("humidity_pct"), _wx.get("pressure_hpa"))
    )
    if _da is not None:
        _da = round(_da)

    # Run label (date · track · ET)
    _label = _run_label(filename, rec)

    # Changelog: flatten to "param: X→Y; ..." string
    _cl_str = "; ".join(
        f"{e.get('parameter','?')}: {e.get('from_val','')}→{e.get('to_val','')}"
        + (f" ({e['note']})" if e.get("note") else "")
        for e in _cl
    )

    return {
        "run_label":      _label,
        "csv_file":       filename,
        "date":           _slip.get("date", ""),
        "time":           _slip.get("time", ""),
        "track":          _slip.get("track_name") or _slip.get("track_location", ""),
        "lane":           _slip.get("lane", ""),
        # Weather
        "temp_f":         _wx.get("temperature_f", ""),
        "baro_inhg":      _wx.get("pressure_hpa", ""),
        "humidity_pct":   _wx.get("humidity_pct", ""),
        "wind":           _wx.get("wind_mph", ""),
        "density_alt_ft": _da if _da is not None else "",
        # Timeslip splits
        "reaction_time":  _slip.get("reaction_time") or _slip.get("rt", ""),
        "et_60":          _slip.get("ft_60")   or _slip.get("et_60",   ""),
        "et_330":         _slip.get("ft_330")  or _slip.get("et_330",  ""),
        "et_660":         _slip.get("ft_660")  or _slip.get("et_eighth",""),
        "mph_660":        _slip.get("mph_660") or _slip.get("mph_eighth",""),
        "et_1000":        _slip.get("ft_1000") or _slip.get("et_1000", ""),
        "et_1320":        _slip.get("ft_1320") or _slip.get("et_quarter",""),
        "mph_1320":       _slip.get("mph_1320") or _slip.get("mph_quarter",""),
        "issues":         _slip.get("issues", ""),
        # Tuning details
        "main_jet":       _rd.get("main_jet",       ""),
        "hs_jet":         _rd.get("hs_jet",          ""),
        "hs_open_psi":    _rd.get("hs_open_psi",     ""),
        "top_pulley":     _rd.get("top_pulley",      ""),
        "bottom_pulley":  _rd.get("bottom_pulley",   ""),
        "overdrive":      _rd.get("overdrive",       ""),
        "wheelie_bar_d":  _rd.get("wheelie_bar_d",   ""),
        "wheelie_bar_p":  _rd.get("wheelie_bar_p",   ""),
        "front_tire_psi": _rd.get("tire_pressure_fl",""),
        "rear_tire_psi":  _rd.get("tire_pressure_rl",""),
        "track_temp_f":   _rd.get("track_temp_f",    ""),
        "tire_temp_f":    _rd.get("tire_temp_f",     ""),
        "launch_rpm":     _rd.get("launch_rpm",      ""),
        "shift_point":    _rd.get("shift_point",     ""),
        "spark_plug":     _rd.get("spark_plug",      ""),
        "plug_gap":       _rd.get("plug_gap",        ""),
        "valve_lash":     _rd.get("valve_lash",      ""),
        "notes":          _rd.get("notes",           ""),
        # Changelog
        "changelog":      _cl_str,
    }

_export_cols = st.columns([1, 5])
if _export_cols[0].button("⬇️ Export all runs to CSV"):
    import io, csv as _csv
    _all_runs = list_saved_runs()
    _rows = []
    for _r in _all_runs:
        _rec = _r["record"] or load_run(_r["filename"])
        _rows.append(_build_export_row(_r["filename"], _rec))

    if _rows:
        _fields = list(_rows[0].keys())
        _buf = io.StringIO()
        _writer = _csv.DictWriter(_buf, fieldnames=_fields)
        _writer.writeheader()
        _writer.writerows(_rows)
        from datetime import date as _date
        _fname = f"racefusion_all_runs_{_date.today().isoformat()}.csv"
        st.download_button(
            f"💾 Download ({len(_rows)} runs)",
            data=_buf.getvalue().encode("utf-8"),
            file_name=_fname,
            mime="text/csv",
            key="dl_all_runs_csv",
        )
    else:
        st.info("No saved runs to export.")

st.markdown("---")

# ── Raw data ──────────────────────────────────────────────────────────────────
with st.expander("📋 Raw Data Table"):
    st.dataframe(df_view, use_container_width=True, height=400)
    st.download_button(
        "⬇️ Download filtered CSV",
        data=df_view.to_csv(index=False).encode("utf-8"),
        file_name="racefusion_filtered.csv",
        mime="text/csv",
    )

with st.expander("📡 All Channels"):
    info_rows = []
    for ch in available_channels:
        s = df[ch].dropna()
        info_rows.append({
            "Channel": ch,
            "Group": channel_to_group.get(ch, "Other"),
            "Min": round(s.min(), 3) if not s.empty else "—",
            "Max": round(s.max(), 3) if not s.empty else "—",
            "Mean": round(s.mean(), 3) if not s.empty else "—",
            "Non-null pts": len(s),
        })
    st.dataframe(pd.DataFrame(info_rows), use_container_width=True)

with st.expander("🗂️ Run Record (JSON)"):
    st.json(run)
