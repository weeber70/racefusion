"""
Race Day Predictor — Page 2 of RaceFusion
Uses historical run data + current weather to predict today's ET and suggest a dial-in.
"""

import os
import math
import requests
import streamlit as st
from datetime import datetime, timezone

# ── Supabase ──────────────────────────────────────────────────────────────────
try:
    from supabase import create_client as _sb_create_client
except ImportError:
    _sb_create_client = None  # type: ignore

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
_sb = (
    _sb_create_client(_SUPABASE_URL, _SUPABASE_KEY)
    if (_sb_create_client and _SUPABASE_URL and _SUPABASE_KEY)
    else None
)

# ── Auth gate ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Race Day Predictor · RaceFusion", page_icon="🏁", layout="wide")

if not st.session_state.get("rf_user"):
    st.warning("🔒 Please log in from the main RaceFusion page first.")
    st.stop()

_user = st.session_state["rf_user"]

# ── Dark theme CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
.stApp, [data-testid="stAppViewContainer"] {
    background-color: #0b0b12 !important;
}
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
    background-color: #0f0f18 !important;
    border-right: 1px solid #1e1e2a !important;
}
h1, h2, h3 { color: #cc1111 !important; }
[data-testid="stMetricLabel"] { color: #999 !important; text-align: center !important; }
[data-testid="stMetricValue"] { color: #e8e8e8 !important; text-align: center !important; }
[data-testid="stMetricValue"] > div {
    white-space: normal !important; overflow: visible !important;
    text-overflow: unset !important; font-size: clamp(1rem, 1.8vw, 2rem) !important;
}
button[kind="primary"], [data-testid="baseButton-primary"] {
    background-color: #cc1111 !important; color: #ffffff !important;
    border: none !important; font-weight: 700 !important;
}
button[kind="secondary"], [data-testid="baseButton-secondary"] {
    background-color: #1a1a24 !important; color: #e8e8e8 !important;
    border: 1px solid #3a2a2a !important;
}
div[data-testid="stDataFrame"] { background: #0f0f18; }
</style>
""", unsafe_allow_html=True)

# ── Helper functions ──────────────────────────────────────────────────────────

def calc_density_altitude(temp_f, humidity_pct, pressure_hpa):
    if any(v is None for v in [temp_f, humidity_pct, pressure_hpa]):
        return None
    T_c   = (temp_f - 32) * 5 / 9
    T_k   = T_c + 273.15
    P_pa  = pressure_hpa * 100.0
    RH    = humidity_pct / 100.0
    e_s   = 610.78 * math.exp(17.27 * T_c / (T_c + 237.3))
    e_pa  = RH * e_s
    P_d   = P_pa - e_pa
    rho   = (P_d / (287.058 * T_k)) + (e_pa / (461.495 * T_k))
    return 145442.16 * (1 - (rho / 1.225) ** 0.234969)


@st.cache_data(show_spinner="Geocoding location…")
def geocode(location: str):
    location = location.strip()
    import re
    m = re.match(r"^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$", location)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return lat, lon, f"{lat:.4f}, {lon:.4f}"
    candidates = [location]
    if "," in location:
        candidates.append(location.replace(", ", " ").replace(",", " "))
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


def fetch_current_weather(lat: float, lon: float) -> dict:
    """Fetch current conditions from Open-Meteo forecast API."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":         lat,
        "longitude":        lon,
        "current":          "temperature_2m,relativehumidity_2m,surface_pressure,windspeed_10m",
        "temperature_unit": "fahrenheit",
        "windspeed_unit":   "mph",
        "timezone":         "auto",
    }
    r    = requests.get(url, params=params, timeout=15)
    data = r.json()
    cur  = data.get("current", {})
    return {
        "temperature_f": cur.get("temperature_2m"),
        "humidity_pct":  cur.get("relativehumidity_2m"),
        "pressure_hpa":  cur.get("surface_pressure"),
        "windspeed_mph": cur.get("windspeed_10m"),
    }


def load_config() -> dict:
    if not _sb:
        return {}
    try:
        rows = _sb.table("user_configs").select("config").eq("username", _user).execute().data
        if rows:
            d = rows[0]["config"] or {}
            d.pop("anthropic_api_key", None)
            return d
    except Exception:
        pass
    return {}


def load_run_history() -> list[dict]:
    """Return all runs for this user that have a valid ET and a DA value."""
    if not _sb:
        return []
    try:
        rows = _sb.table("runs").select("run_data,created_at").eq("username", _user).execute().data
    except Exception:
        return []

    results = []
    for row in rows:
        rec  = row.get("run_data") or {}
        slip = rec.get("timeslip", {}) or {}
        wx   = rec.get("weather",  {}) or {}

        et_raw = slip.get("ft_1320")
        try:
            et = float(et_raw)
        except (TypeError, ValueError):
            continue
        if et <= 0:
            continue

        da = (
            slip.get("density_alt_ft")
            or wx.get("density_alt_ft")
        )
        if da is None:
            da = calc_density_altitude(
                wx.get("temperature_f"),
                wx.get("humidity_pct"),
                wx.get("pressure_hpa"),
            )
        if da is None:
            continue

        results.append({
            "date":  slip.get("date") or row.get("created_at", "")[:10],
            "track": slip.get("track_name") or slip.get("track_location") or "—",
            "et":    et,
            "da":    float(da),
        })

    return results


def linear_regression(xs: list, ys: list):
    """Return (slope, intercept) for a simple least-squares fit."""
    n = len(xs)
    if n < 2:
        return None, None
    sx  = sum(xs)
    sy  = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None, None
    slope     = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def r_squared(xs, ys, slope, intercept):
    mean_y  = sum(ys) / len(ys)
    ss_tot  = sum((y - mean_y) ** 2 for y in ys)
    ss_res  = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return 1 - ss_res / ss_tot if ss_tot > 0 else 0.0


# ── Load config ───────────────────────────────────────────────────────────────
cfg            = load_config()
_loc_name      = cfg.get("location_name", "")
_loc_label     = cfg.get("location_label", "")
_cfg_lat       = cfg.get("lat")
_cfg_lon       = cfg.get("lon")

# ── Page header ───────────────────────────────────────────────────────────────
st.markdown("# 🏁 Race Day Predictor")
st.markdown(
    "<p style='color:#888;margin-top:-12px;'>Predicted ET and suggested dial based on your car's history + today's air.</p>",
    unsafe_allow_html=True,
)
st.markdown("---")

# ── SECTION 1: Current Conditions ─────────────────────────────────────────────
st.markdown("## 🌤️ Current Conditions")

if not _cfg_lat or not _cfg_lon:
    st.warning("No track location set. Go to the main page → Track Location in the sidebar and save your location.")
    st.stop()

st.caption(f"📍 {_loc_label or _loc_name}")

# Session-state cache for weather so Refresh button works without page reload
if "rdp_weather" not in st.session_state:
    st.session_state["rdp_weather"] = None

if st.button("🔄 Refresh Weather", type="secondary"):
    st.session_state["rdp_weather"] = None

if st.session_state["rdp_weather"] is None:
    with st.spinner("Fetching current conditions…"):
        try:
            _wx = fetch_current_weather(float(_cfg_lat), float(_cfg_lon))
            st.session_state["rdp_weather"] = _wx
        except Exception as e:
            st.error(f"Weather fetch failed: {e}")
            st.stop()

_wx = st.session_state["rdp_weather"]
if _wx is None:
    st.stop()

_da_now = calc_density_altitude(_wx.get("temperature_f"), _wx.get("humidity_pct"), _wx.get("pressure_hpa"))

_c1, _c2, _c3, _c4 = st.columns(4)
_c1.metric("🌡️ Temperature",   f"{_wx['temperature_f']:.1f} °F"  if _wx.get("temperature_f") is not None else "—")
_c2.metric("💧 Humidity",       f"{_wx['humidity_pct']:.0f}%"     if _wx.get("humidity_pct")  is not None else "—")
_c3.metric("📊 Baro Pressure",  f"{_wx['pressure_hpa'] * 0.02953:.2f} inHg" if _wx.get("pressure_hpa") is not None else "—")
_c4.metric("📐 Density Alt",    f"{_da_now:,.0f} ft" if _da_now is not None else "—")

st.markdown("---")

# ── SECTION 2: ET Prediction ──────────────────────────────────────────────────
st.markdown("## 🎯 ET Prediction")

if _da_now is None:
    st.warning("Cannot compute DA from current weather — check that pressure and temperature are available.")
    st.stop()

_history = load_run_history()

if not _history:
    st.info("No historical runs with both ET and DA found. Log runs with timeslips on the main page to enable predictions.")
    st.stop()

# ── Outlier detection (±2 SD on ET) ──────────────────────────────────────────
_all_ets  = [r["et"] for r in _history]
_mean_et  = sum(_all_ets) / len(_all_ets)
_n        = len(_all_ets)
_sd_et    = math.sqrt(sum((e - _mean_et) ** 2 for e in _all_ets) / _n) if _n > 1 else 0.0
_threshold = 2.0 * _sd_et

included = []
excluded = []
for r in _history:
    if _sd_et > 0 and abs(r["et"] - _mean_et) > _threshold:
        excluded.append({**r, "status": "excluded — likely aborted or anomalous run"})
    else:
        included.append({**r, "status": "included"})

# ── Linear regression on included runs ───────────────────────────────────────
_n_incl = len(included)

if _n_incl < 2:
    st.warning("Not enough clean runs for regression (need at least 2 after outlier removal). Log more runs to enable predictions.")
else:
    _xs = [r["da"] for r in included]
    _ys = [r["et"] for r in included]

    _slope, _intercept = linear_regression(_xs, _ys)

    if _slope is None:
        st.error("Regression failed — all runs may have identical DA values.")
    else:
        _r2          = r_squared(_xs, _ys, _slope, _intercept)
        _pred_et     = _slope * _da_now + _intercept
        _dial        = _pred_et + 0.02

        # Confidence indicator
        if _n_incl < 5:
            _conf_label = "⚠️ Low confidence"
            _conf_detail = "— log more runs for accurate predictions"
            _conf_color  = "#cc8800"
        elif _n_incl < 15:
            _conf_label  = "🟡 Moderate confidence"
            _conf_detail = f"— based on {_n_incl} runs"
            _conf_color  = "#ccaa00"
        else:
            _conf_label  = "🟢 High confidence"
            _conf_detail = f"— based on {_n_incl} runs"
            _conf_color  = "#22aa55"

        # Results display
        _p1, _p2, _p3 = st.columns(3)
        _p1.metric("Predicted ET",     f"{_pred_et:.3f} s")
        _p2.metric("Suggested Dial",   f"{_dial:.3f} s", help="+0.02 s buffer to help avoid breakout")
        _p3.metric("Today's DA",       f"{_da_now:,.0f} ft")

        st.markdown(
            f"<div style='margin-top:4px;font-size:0.9rem;'>"
            f"<span style='color:{_conf_color};font-weight:700;'>{_conf_label}</span>"
            f"<span style='color:#888;'> {_conf_detail} &nbsp;·&nbsp; R² = {_r2:.3f}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if excluded:
            st.markdown(
                f"<p style='color:#888;font-size:0.82rem;margin-top:12px;'>"
                f"⚠️ {len(excluded)} run(s) excluded as outliers (ET more than 2 SD from mean).</p>",
                unsafe_allow_html=True,
            )

st.markdown("---")

# ── SECTION 3: Run History Table ─────────────────────────────────────────────
st.markdown("## 📋 Run History Used in Prediction")

if not _history:
    st.info("No qualifying runs yet.")
else:
    _all_display = []
    for r in included:
        _all_display.append({
            "Date":     r["date"],
            "Track":    r["track"],
            "ET (s)":   f"{r['et']:.3f}",
            "DA (ft)":  f"{r['da']:,.0f}",
            "Status":   "✅ Included",
        })
    for r in excluded:
        _all_display.append({
            "Date":     r["date"],
            "Track":    r["track"],
            "ET (s)":   f"{r['et']:.3f}",
            "DA (ft)":  f"{r['da']:,.0f}",
            "Status":   f"❌ {r['status']}",
        })

    # Sort by date descending
    _all_display.sort(key=lambda x: x["Date"], reverse=True)

    # Render as HTML table for dark-theme styling
    _rows_html = ""
    for row in _all_display:
        _is_excl  = row["Status"].startswith("❌")
        _row_color = "#555" if _is_excl else "#ddd"
        _rows_html += (
            f"<tr style='opacity:{'0.55' if _is_excl else '1'};'>"
            f"<td style='padding:5px 10px 5px 0;color:{_row_color};'>{row['Date']}</td>"
            f"<td style='padding:5px 10px;color:{_row_color};'>{row['Track']}</td>"
            f"<td style='padding:5px 10px;color:{_row_color};text-align:right;'>{row['ET (s)']}</td>"
            f"<td style='padding:5px 10px;color:{_row_color};text-align:right;'>{row['DA (ft)']}</td>"
            f"<td style='padding:5px 0;color:{'#888' if _is_excl else '#4caf50'};font-size:0.85rem;'>{row['Status']}</td>"
            f"</tr>"
        )

    st.markdown(f"""
<div style="border:1px solid #1e1e2a;border-radius:10px;padding:16px 20px;background:#0a0a14;overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:0.9rem;font-family:monospace;">
<thead>
  <tr style="border-bottom:1px solid #2a2a3a;">
    <th style="color:#666;text-align:left;padding:4px 10px 8px 0;">Date</th>
    <th style="color:#666;text-align:left;padding:4px 10px 8px;">Track</th>
    <th style="color:#666;text-align:right;padding:4px 10px 8px;">ET (s)</th>
    <th style="color:#666;text-align:right;padding:4px 10px 8px;">DA (ft)</th>
    <th style="color:#666;text-align:left;padding:4px 0 8px;">Status</th>
  </tr>
</thead>
<tbody>{_rows_html}</tbody>
</table>
</div>
""", unsafe_allow_html=True)

    st.markdown(
        f"<p style='color:#555;font-size:0.8rem;margin-top:8px;'>"
        f"{len(included)} runs included · {len(excluded)} excluded · "
        f"Mean ET {_mean_et:.3f}s · ±2 SD threshold {_threshold:.3f}s</p>",
        unsafe_allow_html=True,
    )
