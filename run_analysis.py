"""
run_analysis.py — RaceFusion Run Analysis (dashboard) page.

Module-level helpers (moved from app.py):
  check_alerts, load_racepak_csv, get_time_col, detect_shift_points,
  calc_rwhp, _build_ai_payload, _fmt, _build_export_row
"""
import hashlib
import io
import json
import math
import os
import re
import base64
import requests
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
import streamlit.components.v1 as components

from styles import PLOTLY_DARK
from database import (
    _sb, _get_secret, load_run, save_run, save_run_csv, load_run_csv_bytes,
    _run_label, list_saved_runs, get_run_videos, add_run_video,
    delete_run_video, get_user_cars, create_car, _get_slip_storage_key,
    extract_youtube_id, _delete_run_files, _delete_slip_from_storage,
    check_file_hash_duplicate, save_file_hash,
)
from config import load_config, save_config
from weather import (
    fetch_weather, fetch_metar, lookup_track, geocode,
    calc_density_altitude, sea_level_to_station_pressure, wind_dir_label,
    _TRACK_OVERRIDES, _track_key,
)
from charts import make_overlay_chart, TRACE_COLORS, RPM_CHANNEL_NAMES
from timeslip import correct_image_orientation, scan_timeslip, _normalize_slip_result


# ── Module-level helpers (moved from app.py) ────────────────────────────────
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


# ── CSV parser ────────────────────────────────────────────────────────────────

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


# ── (timeslip + geocoding + weather functions extracted to timeslip.py / weather.py) ─
# ── (_rdp_load_run_history extracted to database.py) ────────────────────────────────


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


# ── Page function ────────────────────────────────────────────────────────────
def show_run_analysis(
    saved_runs: list,
    cfg: dict,
    sel_idx_raw: int,
    logo_src: "str | None",
    access_granted: bool,
    current_user: str,
    has_feature,
    channel_groups: dict,
    all_grouped: list,
    _scan_status_area,
    _racepak_controls_slot,
):
    """Render the Create-New-Run form and the Run Analysis dashboard."""
    # ── Values that were global in app.py before the Phase 2 split ───────────────
    api_key = _get_secret("ANTHROPIC_API_KEY")
    car_number_input = cfg.get("car_number", "")
    weight_input = int(cfg.get("car_weight_lbs", 2500))

    # ── Main area ─────────────────────────────────────────────────────────────────

    if st.session_state.get("active_run_id") is None:
        # ── Create New Run form ───────────────────────────────────────────────────
        # Set _was_on_new_run immediately — before rendering any widget — so that
        # reruns triggered from inside this section (e.g. Enter in car number field)
        # see the flag in session state when the sync-code and pre-render guards run
        # at the top of the script on the next render.
        st.session_state["_was_on_new_run"] = True

        _fg = "#888"

        # Gate: trial expired and no active subscription
        if not access_granted:
            if logo_src:
                st.markdown(
                    f'<div style="text-align:center;padding:32px 20px 8px;">'
                    f'<img src="{logo_src}" style="max-width:600px;width:80%;"></div>',
                    unsafe_allow_html=True,
                )
            st.markdown(
                """<div style="text-align:center;padding:24px 20px 12px;">
                <div style="font-size:3rem;margin-bottom:8px;">🔒</div>
                <h3 style="color:#cc1111;">Trial Expired</h3>
                <p style="color:#888;max-width:440px;margin:0 auto 20px;">
                Your 30-day free trial has ended. Upgrade to keep adding runs,
                uploading CSVs, and using all RaceFusion features.
                </p>
                </div>""",
                unsafe_allow_html=True,
            )
            if st.button("⬆️ View Upgrade Options", key="new_run_upgrade_btn", type="primary"):
                st.session_state["current_page"] = "upgrade"
                st.query_params["p"] = "upgrade"
                st.rerun()
            st.markdown(
                "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
                "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
                "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
                "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
                "text-decoration:none;'>Contact Us</a></div>",
                unsafe_allow_html=True,
            )
            st.stop()

        if logo_src:
            st.markdown(
                f'<div style="text-align:center;padding:32px 20px 8px;">'
                f'<img src="{logo_src}" style="max-width:600px;width:80%;"></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown("<h2 style='text-align:center'>🏁 RaceFusion</h2>", unsafe_allow_html=True)

        st.markdown("### Create New Run")
        st.caption("Upload what you have — all fields are optional. Click **Create Run** when ready.")

        # ── Pending-state callbacks ────────────────────────────────────────────────
        # Store uploaded bytes in neutral session state keys so that the upload
        # rerun never touches active_run_id / run_selector / _run_selector_idx.
        # Bytes are consumed (and popped) only when the user clicks "Create Run".
        def _on_csv_upload():
            _inst = st.session_state.get("_create_run_instance_key", 0)
            _f    = st.session_state.get(f"csv_uploader_{_inst}")
            if _f is not None:
                st.session_state["_pending_csv"] = {"bytes": _f.read(), "name": _f.name}
            else:
                st.session_state.pop("_pending_csv", None)

        def _on_slip_upload():
            _inst = st.session_state.get("_create_run_instance_key", 0)
            _f    = st.session_state.get(f"slip_uploader_{_inst}")
            if _f is not None:
                st.session_state["_pending_timeslip"] = {"bytes": _f.read(), "name": _f.name}
            else:
                st.session_state.pop("_pending_timeslip", None)

        _form_csv_col, _form_slip_col = st.columns(2)
        with _form_csv_col:
            st.markdown("**📂 Run Data CSV**")
            if has_feature("csv_upload"):
                _form_csv_file = st.file_uploader(
                    "Run Data CSV", type=["csv"],
                    help="Export from RacePak DataLink or V-Net",
                    label_visibility="collapsed",
                    key=f"csv_uploader_{st.session_state['_create_run_instance_key']}",
                    on_change=_on_csv_upload,
                )
            else:
                _form_csv_file = None
                st.session_state.pop("_pending_csv", None)
                st.info("📊 CSV upload available on Pro.")
                if st.button("⬆️ Upgrade to Pro", key="csv_gate_upgrade_btn"):
                    _sv = st.query_params.get("session", "")
                    st.query_params.clear()
                    if _sv:
                        st.query_params["session"] = _sv
                    st.query_params["p"] = "upgrade"
                    st.session_state["current_page"] = "upgrade"
                    st.rerun()
        with _form_slip_col:
            st.markdown("**🎫 Timeslip Photo**")
            _form_slip_file = st.file_uploader(
                "Timeslip photo", type=["jpg", "jpeg", "png", "webp"],
                help="Clear photo of your printed timeslip",
                label_visibility="collapsed",
                key=f"slip_uploader_{st.session_state['_create_run_instance_key']}",
                on_change=_on_slip_upload,
            )

        # ── Car selection / creation ───────────────────────────────────────────────
        _user_cars = get_user_cars(current_user)
        _form_selected_car: dict | None = None   # populated below
        _form_new_car_name: str = ""             # used when creating a new car

        if len(_user_cars) == 0:
            st.markdown("**Car Name**")
            _form_new_car_name = st.text_input(
                "Car name",
                placeholder='e.g. "2023 Camaro", "Top Dragster"',
                help="Give your car a name — it will be saved for future runs.",
                label_visibility="collapsed",
                key=f"create_car_name_{st.session_state['upload_gen']}",
            )
        elif len(_user_cars) == 1:
            _form_selected_car = _user_cars[0]
            st.markdown(f"**{_form_selected_car['car_name']}**")
            with st.expander("Rename car"):
                _form_rename_input = st.text_input(
                    "New name",
                    value=_form_selected_car["car_name"],
                    key=f"create_car_rename_{st.session_state['upload_gen']}",
                )
                if _form_rename_input.strip() and _form_rename_input.strip() != _form_selected_car["car_name"]:
                    _form_new_car_name = _form_rename_input.strip()   # applied on submit
        else:
            _car_options = {c["car_name"]: c for c in _user_cars}
            _sel_car_name = st.selectbox(
                "Car",
                options=list(_car_options.keys()),
                key=f"create_car_sel_{st.session_state['upload_gen']}",
            )
            _form_selected_car = _car_options[_sel_car_name]

        # Pre-fill car number from the most recent run for this car.
        # Fallback chain: most-recent-run car_number → car's default_car_number → cfg car_number → "".
        # Fallback only fires when the query returns zero rows OR run_data["car_number"] is absent/empty.
        # Query runs once per form generation (first render, before the widget key is in session state).
        # On subsequent renders the text_input owns its value via session state; value= is ignored.
        _cn_widget_key = f"create_car_num_{st.session_state['upload_gen']}"
        if _cn_widget_key not in st.session_state:
            if _form_selected_car:
                _cn_car_id  = _form_selected_car.get("car_id")
                # Fallback 2: car's profile default (set when the car was created / edited)
                _cn_profile = _form_selected_car.get("default_car_number", "").strip() or cfg.get("car_number", "")
                # Fallback 1: most recent run for this car that has car_number saved
                _cn_recent  = ""
                if _cn_car_id and _sb:
                    try:
                        _cn_rows = (
                            _sb.table("runs")
                            .select("run_data")
                            .eq("username", st.session_state.get("rf_user", ""))
                            .eq("car_id", _cn_car_id)
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                            .data
                        )
                        # _cn_rows is [] when no runs exist for this car → fallback fires
                        # run_data may be None (bare insert) or missing car_number → fallback fires
                        _cn_recent = (
                            ((_cn_rows[0]["run_data"] or {}).get("car_number") or "").strip()
                            if _cn_rows else ""
                        )
                    except Exception:
                        pass   # network/schema error → fall through to profile default
                # Use the most recent run's car number; fall back to car profile default
                _car_num_default = _cn_recent or _cn_profile
            else:
                _car_num_default = cfg.get("car_number", "")
        else:
            _car_num_default = ""   # widget already in session state; value= is ignored
        _form_car_number = st.text_input(
            "Car number",
            value=_car_num_default,
            placeholder="e.g. 1234",
            help="If the slip shows multiple cars, Claude will extract only yours",
            key=_cn_widget_key,
        )
        # ── Optional pre-run videos ────────────────────────────────────────────────
        _gen = st.session_state["upload_gen"]
        _form_videos: list[dict] = []   # [{url, label}] collected before submit
        st.markdown('<p style="font-size:0.875rem;margin-bottom:0.25rem;font-weight:400">Run videos (optional)</p>', unsafe_allow_html=True)
        with st.expander("🎥 Add YouTube link(s)"):
            _vid_row_count = st.session_state.setdefault("_create_video_row_count", 3)
            for _vi in range(_vid_row_count):
                _vc1, _vc2 = st.columns([3, 2])
                _fv_url   = _vc1.text_input("YouTube URL",   placeholder="https://youtu.be/...",      key=f"video_url_{_vi}", label_visibility="collapsed" if _vi else "visible")
                _fv_label = _vc2.text_input("Label",         placeholder=f"Video {_vi+1}",             key=f"video_label_{_vi}", label_visibility="collapsed" if _vi else "visible")
                if _fv_url.strip():
                    _form_videos.append({"url": _fv_url.strip(), "label": _fv_label.strip()})
            if st.button("➕ Add another video", key=f"add_video_btn_{_gen}"):
                st.session_state["_create_video_row_count"] = _vid_row_count + 1
                st.rerun()

        _form_submitted = st.button(
            "🏁 Create Run", type="primary", use_container_width=True,
            key=f"create_run_btn_{st.session_state['upload_gen']}",
        )

        # ── Run creation logic ────────────────────────────────────────────────────
        _pending_csv  = None
        _pending_slip = None
        _csv_hsave    = None
        _slp_hsave    = None
        _do_create    = False

        if st.session_state.get("slip_dup_override"):
            # User confirmed "Upload Anyway" — restore held file bytes and proceed
            _pending_csv  = st.session_state.pop("_dup_held_csv", None)
            _pending_slip = st.session_state.pop("_dup_held_slip", None)
            _csv_hsave    = st.session_state.pop("_dup_held_csv_hash", None)
            _slp_hsave    = st.session_state.pop("_dup_held_slip_hash", None)
            st.session_state.pop("slip_dup_override", None)
            if _pending_csv is not None or _pending_slip is not None:
                _do_create = True

        elif _form_submitted:
            # Consume pending file bytes (set by on_change callbacks; safe to pop here).
            # We pop before any processing so the keys are dead for the rest of this render.
            _pending_csv  = st.session_state.pop("_pending_csv", None)
            _pending_slip = st.session_state.pop("_pending_timeslip", None)
            if _pending_csv is None and _pending_slip is None:
                st.error("Upload at least a Run Data CSV or a timeslip photo.")
                # Belt-and-suspenders: ensure pending keys are gone even if a callback
                # re-set them during this render (e.g. file-uploader widget re-evaluation).
                st.session_state.pop("_pending_csv", None)
                st.session_state.pop("_pending_timeslip", None)
            else:
                # ── Hash-based duplicate detection ────────────────────────────────
                _csv_hash = hashlib.sha256(_pending_csv["bytes"]).hexdigest() if _pending_csv else None
                _slp_hash = hashlib.sha256(_pending_slip["bytes"]).hexdigest() if _pending_slip else None
                _csv_dup  = check_file_hash_duplicate(current_user, _csv_hash, "csv_file_hash") if _csv_hash else None
                _slp_dup  = check_file_hash_duplicate(current_user, _slp_hash, "slip_file_hash") if _slp_hash else None

                if _csv_dup or _slp_dup:
                    # Hold file bytes so the override rerun can restore them
                    st.session_state["_dup_held_csv"]       = _pending_csv
                    st.session_state["_dup_held_slip"]       = _pending_slip
                    st.session_state["_dup_held_csv_hash"]  = _csv_hash
                    st.session_state["_dup_held_slip_hash"] = _slp_hash

                    # Show inline warnings
                    if _csv_dup:
                        _cd = _csv_dup
                        _cd_date  = (_cd.get("created_at") or "")[:10]
                        _cd_track = _cd.get("track") or "unknown track"
                        _cd_et    = _cd.get("et")
                        _cd_et_s  = f"{float(_cd_et):.3f}" if _cd_et else "?"
                        st.warning(
                            f"⚠️ This CSV matches an existing run from {_cd_date} "
                            f"at {_cd_track} (ET: {_cd_et_s}s). Upload anyway?"
                        )
                    if _slp_dup:
                        _sd = _slp_dup
                        _sd_date  = (_sd.get("created_at") or "")[:10]
                        _sd_track = _sd.get("track") or "unknown track"
                        _sd_et    = _sd.get("et")
                        _sd_et_s  = f"{float(_sd_et):.3f}" if _sd_et else "?"
                        st.warning(
                            f"⚠️ This timeslip matches an existing run from {_sd_date} "
                            f"at {_sd_track} (ET: {_sd_et_s}s). Upload anyway?"
                        )

                    _dc1, _dc2 = st.columns(2)
                    with _dc1:
                        if st.button("Upload Anyway", type="primary", key="slip_dup_confirm"):
                            st.session_state["slip_dup_override"] = True
                            st.rerun()
                    with _dc2:
                        if st.button("Cancel", key="slip_dup_cancel"):
                            for _dk in ("_dup_held_csv", "_dup_held_slip",
                                        "_dup_held_csv_hash", "_dup_held_slip_hash"):
                                st.session_state.pop(_dk, None)
                            st.rerun()
                    st.stop()  # prevent run creation from executing
                else:
                    _csv_hsave = _csv_hash
                    _slp_hsave = _slp_hash
                    _do_create = True

        if _do_create:
            # ── Resolve car_id ────────────────────────────────────────────────
            _submit_car_id: str | None = None
            if _form_selected_car is not None:
                _submit_car_id = _form_selected_car["car_id"]
                # Apply rename if the user typed a new name in the expander
                if _form_new_car_name and _form_new_car_name != _form_selected_car["car_name"] and _sb:
                    try:
                        _sb.table("cars").update({"car_name": _form_new_car_name}) \
                           .eq("car_id", _submit_car_id).execute()
                    except Exception:
                        pass
            elif _form_new_car_name.strip():
                # No cars yet — create one now
                _submit_car_id = create_car(
                    current_user,
                    _form_new_car_name.strip(),
                    _form_car_number.strip(),
                )

            # ── Determine run filename ────────────────────────────────────────
            if _pending_csv is not None:
                _new_run_id    = _pending_csv["name"]
                _new_csv_bytes = _pending_csv["bytes"]
            else:
                from datetime import datetime as _dt_form
                _new_run_id    = f"slip_{_dt_form.now().strftime('%Y%m%d_%H%M%S')}.run"
                _new_csv_bytes = None

            _new_run_rec = {}
            # Persist the typed car number so the next Create New Run form can
            # pre-fill from this run instead of the (possibly stale) car profile default.
            if _form_car_number.strip():
                _new_run_rec["car_number"] = _form_car_number.strip()

            # Set run identity BEFORE the status block so it survives any
            # intermediate rerun that st.status or its children might trigger.
            # st.rerun() is also called after the block — this is belt-and-braces.
            st.session_state["active_run_id"] = _new_run_id
            st.query_params["run"] = _new_run_id
            st.session_state["_newly_created_run"] = {
                "id": _new_run_id,
                "label": _run_label(_new_run_id, {}),
                "record": {},
                "has_csv": _new_csv_bytes is not None,
            }

            with st.status("Creating run…", expanded=True) as _create_status:

                # ── Save CSV ──────────────────────────────────────────────────
                if _new_csv_bytes is not None:
                    _create_status.write("💾 Saving CSV data…")
                    _stale_key = _get_slip_storage_key(_new_run_id)
                    if _stale_key:
                        _delete_slip_from_storage(_stale_key)
                    save_run_csv(_new_run_id, _new_csv_bytes)

                save_run(_new_run_id, _new_run_rec, car_id=_submit_car_id)

                # ── Upload + scan timeslip ────────────────────────────────────
                if _pending_slip is not None:
                    _create_status.write("📤 Uploading timeslip…")
                    _sl_bytes = _pending_slip["bytes"]
                    _sl_ext   = _pending_slip["name"].rsplit(".", 1)[-1].lower()
                    _sl_stem  = re.sub(r"[^\w\-]", "_", Path(_new_run_id).stem)
                    _sl_s_key = f"{current_user}/{_sl_stem}.{_sl_ext}"
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

                    if not _form_car_number.strip():
                        _create_status.write(
                            "ℹ️ Enter your car number above to scan timeslips. "
                            "RaceFusion needs your car number to identify your lane on the timeslip."
                        )
                    elif api_key:
                        _create_status.write("🎫 Scanning timeslip…")
                        try:
                            # _sl_bytes comes from _pending_slip["bytes"], stored by the
                            # _on_slip_upload on_change callback only when the uploaded
                            # file object is not None — so bytes are guaranteed non-empty here.
                            _scan_result = scan_timeslip(_sl_bytes, _sl_mime, api_key, _form_car_number)
                            # Track which car number was used so the run-view rescan
                            # guard can tell whether it needs to retry or not.
                            _scan_result["_scanned_with"] = _form_car_number.strip()

                            if _scan_result.get("car_found") is False:
                                _new_run_rec["timeslip"] = _scan_result
                                _create_status.write(
                                    f"ℹ️ Car number **{_form_car_number.strip()}** wasn't found on "
                                    "this timeslip — the track may have printed a different number. "
                                    "You can re-scan from the run view after confirming your car number."
                                )
                            else:
                                _new_run_rec["timeslip"] = _scan_result

                                # Auto-populate result from scanned slip (only if not manually set)
                                _scanned_result = _normalize_slip_result(_scan_result.get("result"))
                                if _scanned_result:
                                    _rd_auto = _new_run_rec.get("run_details") or {}
                                    if not _rd_auto.get("result"):
                                        _rd_auto["result"] = _scanned_result
                                        _new_run_rec["run_details"] = _rd_auto

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
                                    _tname = _scan_result.get("track_name", "")
                                    _tloc  = _scan_result.get("track_location", "")
                                    if _tname or _tloc:
                                        _create_status.write(f"📍 Looking up {_tname or _tloc}…")
                                        _tk = lookup_track(_tname, _tloc)
                                        if _tk:
                                            _wx_lat, _wx_lon, _wx_label = _tk["lat"], _tk["lon"], _tk["display_name"]
                                            # Auto-save track location to user config
                                            cfg["location_name"]  = _tname or _tloc
                                            cfg["location_label"] = _tk["display_name"]
                                            cfg["lat"] = _tk["lat"]
                                            cfg["lon"] = _tk["lon"]
                                            cfg["elev_ft"] = _tk.get("elev_ft")
                                            save_config(cfg)
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

                # ── Save pre-filled videos ────────────────────────────────────
                for _vi, _fv in enumerate(_form_videos):
                    if extract_youtube_id(_fv["url"]):
                        add_run_video(_new_run_id, current_user, _fv["url"], _fv["label"],
                                      display_order=_vi + 1)

                # ── Persist file hashes ───────────────────────────────────────
                if _csv_hsave:
                    save_file_hash(_new_run_id, "csv_file_hash", _csv_hsave)
                if _slp_hsave:
                    save_file_hash(_new_run_id, "slip_file_hash", _slp_hsave)

                _create_status.update(label="✅ Run created!", state="complete")

            # Update cache with full record now that the status block has finished
            # (timeslip scan, weather fetch, etc. have populated _new_run_rec).
            st.session_state["_newly_created_run"]["record"] = _new_run_rec
            st.session_state["_newly_created_run"]["label"]  = _run_label(_new_run_id, _new_run_rec)
            # active_run_id and query_params["run"] were already set before the status block.
            # Increment key gen AND explicitly purge all old form widget data from session state
            _old_gen  = st.session_state["upload_gen"]
            _old_inst = st.session_state.get("_create_run_instance_key", 0)
            st.session_state["upload_gen"] = _old_gen + 1
            _stale_keys = [
                f"csv_uploader_{_old_inst}",   # file-uploader uses _create_run_instance_key
                f"slip_uploader_{_old_inst}",  # ditto
                f"create_car_num_{_old_gen}",
                f"create_run_type_{_old_gen}",
                f"create_note_{_old_gen}",
                f"create_run_btn_{_old_gen}",
                "_last_uploaded_csv",
                "_pending_csv",
                "_pending_timeslip",
            ]
            # Clear video URL/label fields and reset the row counter
            _old_vid_count = st.session_state.get("_create_video_row_count", 3)
            st.session_state["_create_video_row_count"] = 3
            for _vi in range(_old_vid_count):
                _stale_keys += [f"video_url_{_vi}", f"video_label_{_vi}"]
            _stale_keys.append(f"add_video_btn_{_old_gen}")
            for _stale_key in _stale_keys:
                st.session_state.pop(_stale_key, None)
            # Belt-and-suspenders: explicitly clear pending keys after stale-key
            # sweep in case a callback re-set them during this render cycle.
            st.session_state.pop("_pending_csv", None)
            st.session_state.pop("_pending_timeslip", None)
            st.rerun()

        st.markdown(
            "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
            "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
            "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
            "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
            "text-decoration:none;'>Contact Us</a></div>",
            unsafe_allow_html=True,
        )
        st.stop()

    # ── Load RacePak data (may be None for closed runs) ───────────────────────────
    csv_name = st.session_state.get("active_run_id")
    if csv_name is None and sel_idx_raw > 0 and sel_idx_raw <= len(saved_runs):
        csv_name = saved_runs[sel_idx_raw - 1]["filename"]
        st.session_state["active_run_id"] = csv_name
        st.query_params["run"] = csv_name
    # Reset Run Details expander state when the user opens a different run
    if csv_name != st.session_state.get("_last_opened_run_id"):
        st.session_state["run_details_expanded"] = False
        st.session_state["_last_opened_run_id"] = csv_name
    # Load CSV bytes now — deferred from the sidebar so uploads are fully processed first
    _run_meta_now     = next((r for r in saved_runs if r["filename"] == csv_name), None)
    _active_csv_bytes = load_run_csv_bytes(csv_name) if (_run_meta_now and _run_meta_now["has_csv"]) else None
    _csv_available    = _active_csv_bytes is not None

    _ch_prefs = cfg.get("channel_prefs", {})
    if _csv_available:
        df = load_racepak_csv(_active_csv_bytes)
        time_col = get_time_col(df)
        available_channels = [c for c in df.columns if c != time_col]
        channel_to_group: dict[str, str] = {}
        for grp, chs in channel_groups.items():
            for ch in chs:
                if ch in available_channels:
                    channel_to_group[ch] = grp
        for ch in available_channels:
            if ch not in channel_to_group:
                channel_to_group[ch] = "📦 Other"
        # Apply user's group overrides from the All Channels table
        for ch in available_channels:
            if ch in _ch_prefs and _ch_prefs[ch].get("group"):
                channel_to_group[ch] = _ch_prefs[ch]["group"]
        # Save full channel list before show-filtering (used by All Channels table)
        _all_channels_full = list(available_channels)
        # Compute default show value: hide channels where all data is exactly 0
        _ch_defaults = {}
        for _ch0 in _all_channels_full:
            _s0 = df[_ch0].dropna()
            _ch_defaults[_ch0] = not (
                not _s0.empty
                and float(_s0.min()) == 0.0
                and float(_s0.max()) == 0.0
            )
        # Apply show/hide preferences (user-saved or computed defaults)
        available_channels = [
            ch for ch in available_channels
            if _ch_prefs.get(ch, {}).get("show", _ch_defaults.get(ch, True))
        ]
        groups_present = list(dict.fromkeys(
            [channel_to_group[ch] for ch in all_grouped if ch in available_channels]
            + [channel_to_group[ch] for ch in available_channels
               if channel_to_group[ch] not in
               [channel_to_group[c] for c in all_grouped if c in available_channels]]
        ))
        # ── Global RacePak scale (computed from full run, not just the visible window) ─
        _rpm_chs_in_df = [ch for ch in _all_channels_full if ch in RPM_CHANNEL_NAMES]
        if _rpm_chs_in_df:
            _global_rpm_max = max(float(df[ch].dropna().max()) for ch in _rpm_chs_in_df)
        else:
            _global_rpm_max = 8000.0   # fallback when no RPM channel present
        # _global_rpm_max drives the dashed reference line; y_min/y_max come from the
        # RPM Range slider (_chart_rpm_max) and are hardcoded at the call sites.
    else:
        df = None
        time_col = None
        available_channels = []
        channel_to_group = {}
        groups_present = []
        _all_channels_full = []
        _ch_defaults = {}
        _global_rpm_max = 8000.0

    # ── Sidebar: RacePak Controls (rendered into slot between Run Manager and RacePak Data) ──
    with _racepak_controls_slot:
        st.markdown("### 📊 Run Data Controls")
        if _csv_available:
            with st.expander("Graph Controls", expanded=False):
                # 1. Smoothing
                st.markdown("**Smoothing**")
                smooth_points = st.slider(
                    "Points", min_value=1, max_value=50, value=25, step=1,
                    key="chart_smooth_points",
                )

                # 2. RPM Range
                st.markdown("**RPM Range**")
                _chart_rpm_max = st.slider(
                    "Y-axis ceiling (RPM)", min_value=-10, max_value=15000,
                    value=10000, step=500, key="chart_rpm_max",
                )

                # 3. Time Range
                st.markdown("**Time Range**")
                t_min = float(df[time_col].min())
                t_max = float(df[time_col].max())
                t_range = st.slider(
                    "Seconds", min_value=t_min, max_value=max(t_max, 20.0),
                    value=(t_min, min(t_max, 10.0)), step=0.02,
                    key=f"t_range_{csv_name}",
                )
                df_view = df[(df[time_col] >= t_range[0]) & (df[time_col] <= t_range[1])]

                # 4. Chart Style
                st.markdown("**Chart Style**")
                chart_height = st.slider("Chart height (px)", 200, 600, 320, 50,
                                         key=f"chart_h_{csv_name}")
                show_markers = st.checkbox("Show data points", value=False,
                                           key=f"show_markers_{csv_name}")
                mode = "lines+markers" if show_markers else "lines"

                # 5. Groups to Show
                st.markdown("**Groups to Show**")
                selected_groups = st.multiselect(
                    "Channel groups", options=groups_present, default=groups_present[:4],
                    help="Each group shows all its channels overlaid on one chart",
                    key=f"sel_groups_{csv_name}",
                )

                # 6. Custom Overlay
                st.markdown("**Custom Overlay**")
                custom_channels = st.multiselect(
                    "Pick any channels to compare",
                    options=available_channels,
                    default=[],
                    help="Select two or more channels to plot together on a single chart",
                    key=f"custom_ch_{csv_name}",
                )

                # 7. Hidden Channels
                st.markdown("**Hidden Channels**")
                _flat_channels = [
                    ch for ch in available_channels
                    if df[ch].dropna().nunique() <= 1
                ]
                _saved_hidden = cfg.get("hidden_channels", [])
                _saved_hidden = [ch for ch in _saved_hidden if ch in available_channels]
                hidden_channels = st.multiselect(
                    "Channels to hide",
                    options=available_channels,
                    default=_saved_hidden,
                    help="These channels are removed from all charts. Flat/no-data channels are good candidates.",
                    key=f"hidden_ch_{csv_name}",
                )
                if _flat_channels:
                    _flat_not_hidden = [ch for ch in _flat_channels if ch not in hidden_channels]
                    if _flat_not_hidden:
                        st.caption(f"💡 Flat (no variation): {', '.join(_flat_not_hidden)}")
                if hidden_channels != _saved_hidden:
                    cfg["hidden_channels"] = hidden_channels
                    save_config(cfg)
                available_channels = [ch for ch in available_channels if ch not in hidden_channels]

        else:
            df_view = None
            selected_groups = []
            hidden_channels = []
            custom_channels = []
            chart_height = 320
            mode = "lines"
            _chart_rpm_max = 10000
            smooth_points = 1

        # ── Channel Rules ─────────────────────────────────────────────────────────
        _rules = cfg.get("channel_rules", {})

        with st.expander("Channel Rules", expanded=False):
            with st.expander("Add / Edit Rule", expanded=False):
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
                            st.session_state["active_run_id"] = csv_name
                            st.query_params["run"] = csv_name
                            st.rerun()

            # List existing rules with remove buttons
            if _rules:
                for _ch, _rule in list(_rules.items()):
                    _parts = []
                    if "min" in _rule:
                        _parts.append(f"min {_rule['min']}")
                    if "max" in _rule:
                        _parts.append(f"max {_rule['max']}")
                    _rcol1, _rcol2 = st.columns([3, 1])
                    _rcol1.caption(f"**{_ch}**: {' · '.join(_parts)}")
                    if _rcol2.button("✕", key=f"del_rule_{_ch}"):
                        del _rules[_ch]
                        cfg["channel_rules"] = _rules
                        save_config(cfg)
                        st.session_state["active_run_id"] = csv_name
                        st.query_params["run"] = csv_name
                        st.rerun()
            else:
                st.caption("No rules set yet.")

        st.markdown("---")

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
    # Effective car number: prefer the run's own saved car_number over the global
    # config so that a different global config can't trigger a rescan with the wrong
    # number (e.g. car profile "327K" vs run's typed "327X").
    _effective_car_num = run.get("car_number", "").strip() or car_number_input.strip()
    # _scanned_with tracks which number was used for the last scan. Re-scan only
    # when that number has changed — prevents infinite rescans on persistent car_not_found.
    _last_scan_car_num = run.get("timeslip", {}).get("_scanned_with", "")

    # Re-scan if: no timeslip data at all, OR previous scan returned car_not_found
    # AND the effective car number has changed since the last scan attempt.
    _needs_scan = _slip_bytes is not None and (
        "timeslip" not in run
        or (
            _effective_car_num
            and run.get("timeslip", {}).get("car_found") is False
            and _effective_car_num != _last_scan_car_num
        )
    )
    if _needs_scan:
        if not api_key:
            _scan_status_area.warning("⚠️ ANTHROPIC_API_KEY not set — timeslip scanning unavailable.")
        elif not _effective_car_num:
            pass  # message handled in dashboard area below
        else:
            with _scan_status_area.status("🎫 Scanning timeslip…", expanded=False) as _scan_status:
                try:
                    slip_data = scan_timeslip(_slip_bytes, _slip_media, api_key, _effective_car_num)
                    slip_data["_scanned_with"] = _effective_car_num  # guard against rescanning same number
                    if slip_data.get("car_found") is False:
                        # Save sentinel so we don't retry until the car number changes.
                        run["timeslip"] = slip_data
                        run["csv_name"] = csv_name
                        save_run(csv_name, run)
                        _scan_status.update(
                            label=f"ℹ️ Car #{_effective_car_num} not found on timeslip",
                            state="warning", expanded=False,
                        )
                    else:
                        run["timeslip"] = slip_data
                        # Auto-populate result from scanned slip (only if not manually set)
                        _scanned_result = _normalize_slip_result(slip_data.get("result"))
                        if _scanned_result:
                            _rd_auto = run.get("run_details") or {}
                            if not _rd_auto.get("result"):
                                _rd_auto["result"] = _scanned_result
                                run["run_details"] = _rd_auto
                        run["csv_name"] = csv_name
                        save_run(csv_name, run)
                        _scan_status.update(label="✅ Timeslip scanned!", state="complete", expanded=False)
                        st.session_state["active_run_id"] = csv_name
                        st.query_params["run"] = csv_name
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

        # Resolve lat/lon: look up track by name first, then fall back to manual config
        wx_lat, wx_lon, wx_label = None, None, ""

        _track_name_ws = slip.get("track_name", "")
        _track_loc_ws  = slip.get("track_location", "")
        _track_label_ws = _track_name_ws or _track_loc_ws
        if _track_label_ws:
            with st.sidebar.status(f"📍 Looking up {_track_label_ws}…", expanded=False) as _geo_status:
                _tk_ws = lookup_track(_track_name_ws, _track_loc_ws)
                if _tk_ws:
                    wx_lat, wx_lon, wx_label = _tk_ws["lat"], _tk_ws["lon"], _tk_ws["display_name"]
                    _geo_status.update(label=f"📍 {wx_label}", state="complete", expanded=False)
                    # Auto-save track location to user config
                    cfg["location_name"]  = _track_name_ws or _track_loc_ws
                    cfg["location_label"] = _tk_ws["display_name"]
                    cfg["lat"] = _tk_ws["lat"]
                    cfg["lon"] = _tk_ws["lon"]
                    cfg["elev_ft"] = _tk_ws.get("elev_ft")
                    save_config(cfg)
                else:
                    _geo_status.update(label=f"📍 Couldn't locate '{_track_label_ws}'", state="error", expanded=False)

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
                        wx.get("temperature_f"), wx.get("pressure_hpa")
                    )
                    if _fetched_da is not None:
                        wx["density_alt_ft"] = round(_fetched_da)
                    run["weather"] = wx
                    run["weather_date"] = date_str
                    run["weather_location"] = wx_label
                    save_run(csv_name, run)
                    _wx_status.update(label="✅ Weather fetched!", state="complete", expanded=False)
                    st.session_state["active_run_id"] = csv_name
                    st.query_params["run"] = csv_name
                    st.rerun()
                except Exception as e:
                    _wx_status.update(label="❌ Weather fetch failed", state="error", expanded=True)
                    st.sidebar.warning(f"Weather fetch failed: {e}")
        else:
            st.sidebar.info("📍 No track location found. Enter one in Track Location below to fetch weather.")

    # _rd and _changelog loaded here so they're available throughout the dashboard
    # Merge car_profile defaults with whatever is already saved in run_details so that:
    #  • new runs with no saved details show car_profile values
    #  • new runs with a partial run_details (e.g. just "result" from timeslip scan)
    #    still show car_profile values for all other fields
    #  • existing runs with fully-saved details show their own saved values
    _rd        = {**cfg.get("car_profile", {}), **(run.get("run_details") or {})}
    _changelog = run.get("changelog", [])



    # ── (make_overlay_chart extracted to charts.py) ─────────────────────────────

    # ═════════════════════════════════════════════════════════════════════════════
    # DASHBOARD
    # ═════════════════════════════════════════════════════════════════════════════
    if logo_src:
        st.markdown(
            f'<img src="{logo_src}" style="max-width:520px;width:60%;'
            f'margin:0 auto 4px auto;display:block;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🏁 RaceFusion")

    _run_display_name = _run_label(csv_name, run) if csv_name.endswith(".run") else csv_name
    _slip_status_label = "· 📸 Timeslip attached" if _slip_storage_key else "· 📸 No timeslip"
    st.caption(f"Run: **{_run_display_name}** {_slip_status_label}")

    if not _csv_available:
        _has_timeslip_data = bool(run.get("timeslip"))
        if _has_timeslip_data:
            st.caption("🎫 Timeslip-only run")
        else:
            st.caption("⬆️ Upload a timeslip photo or CSV in the sidebar to get started.")

    # Look up car name for the active run
    _run_car_name = ""
    if _sb and csv_name:
        try:
            _car_id_row = _sb.table("runs").select("car_id").eq("username", current_user).eq("csv_filename", csv_name).execute().data
            _run_car_id = (_car_id_row[0].get("car_id") or "") if _car_id_row else ""
            if _run_car_id:
                _car_name_row = _sb.table("cars").select("car_name").eq("car_id", _run_car_id).execute().data
                _run_car_name = (_car_name_row[0].get("car_name") or "") if _car_name_row else ""
        except Exception:
            pass

    # ── Run header: car name (left) + Save & Close (right) ──────────────────────
    st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)
    _hdr_col1, _hdr_col2 = st.columns([5, 2], vertical_alignment="center")
    if _run_car_name:
        _hdr_col1.markdown(f"## **{_run_car_name}**")
    _hdr_col2a, _hdr_col2b = _hdr_col2.columns(2)
    if _hdr_col2a.button("✅ Save & Close Run", use_container_width=True, type="primary",
                         key="save_close_btn",
                         help="Saves all run data and returns to upload screen for the next run"):
        # Just reset the UI — keep CSV, timeslip image, and JSON all intact
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
    if _hdr_col2b.button("🗑 Discard Run", use_container_width=True, type="secondary",
                         key="discard_close_btn",
                         help="Permanently delete this run and return to Run Manager"):
        _delete_run_files(csv_name)
        st.session_state["active_run_id"] = None
        st.session_state["current_page"] = "run_manager"
        st.query_params["p"] = "run_manager"
        st.query_params.pop("run", None)
        st.session_state["_reset_selector"] = True
        st.rerun()

    # ── Summary row ───────────────────────────────────────────────────────────────
    # Pull timeslip values for ET / MPH / RWHP when available; fall back to CSV
    # ── Run Videos ───────────────────────────────────────────────────────────────
    import streamlit.components.v1 as _stc  # import once, outside the loop
    _run_videos = get_run_videos(csv_name)
    if _run_videos:
        # Pre-extract YouTube IDs before building tabs — avoids any loop-variable
        # late-binding issue with Streamlit's tab rendering context.
        _vid_yt_ids = [extract_youtube_id(v.get("youtube_url", "")) for v in _run_videos]

        # Build deduplicated tab labels: only number when a label genuinely repeats.
        # First occurrence keeps its name; 2nd gets " 2", 3rd " 3", etc.
        _raw_labels = [v.get("video_label") or f"Video {i+1}" for i, v in enumerate(_run_videos)]
        _label_count: dict[str, int] = {}
        for _l in _raw_labels:
            _label_count[_l] = _label_count.get(_l, 0) + 1
        _label_seen: dict[str, int] = {}
        _vid_tab_labels = []
        for _l in _raw_labels:
            if _label_count[_l] > 1:
                _label_seen[_l] = _label_seen.get(_l, 0) + 1
                # First occurrence: keep as-is; subsequent ones get " 2", " 3", …
                _vid_tab_labels.append(_l if _label_seen[_l] == 1 else f"{_l} {_label_seen[_l]}")
            else:
                _vid_tab_labels.append(_l)

        _vid_tabs = st.tabs(_vid_tab_labels)
        for _ti in range(len(_run_videos)):
            with _vid_tabs[_ti]:
                _yt_id_i = _vid_yt_ids[_ti]
                if _yt_id_i:
                    _stc.html(
                        f'<iframe width="100%" height="450" '
                        f'src="https://www.youtube.com/embed/{_yt_id_i}" '
                        f'frameborder="0" allowfullscreen></iframe>',
                        height=460,
                    )
                else:
                    st.warning(f"Could not parse YouTube URL: {_run_videos[_ti].get('youtube_url', '')}")
                if st.button("🗑️ Delete video", key=f"del_vid_{_run_videos[_ti]['video_id']}"):
                    delete_run_video(_run_videos[_ti]["video_id"])
                    # Explicitly preserve active run so the page doesn't reset to New Run
                    st.session_state["active_run_id"] = csv_name
                    st.query_params["run"] = csv_name
                    st.rerun()

    with st.expander("➕ Add video"):
        _add_vid_key = st.session_state.setdefault(f"_add_vid_key_{csv_name}", 0)
        _add_vid_url   = st.text_input("YouTube URL", placeholder="https://youtu.be/...",
                                       key=f"add_vid_url_{csv_name}_{_add_vid_key}")
        _add_vid_label = st.text_input("Label (optional)", placeholder="Qualifying pass, burnout…",
                                       key=f"add_vid_label_{csv_name}_{_add_vid_key}")
        if st.button("Add", key=f"add_vid_btn_{csv_name}_{_add_vid_key}", type="primary"):
            if not _add_vid_url.strip():
                st.warning("Paste a YouTube URL first.")
            elif not extract_youtube_id(_add_vid_url):
                st.error("Couldn't recognise that as a YouTube URL. Try youtube.com/watch?v=... or youtu.be/...")
            else:
                add_run_video(csv_name, current_user, _add_vid_url, _add_vid_label)
                st.session_state[f"_add_vid_key_{csv_name}"] += 1
                st.session_state["active_run_id"] = csv_name
                st.query_params["run"] = csv_name
                st.rerun()

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
        _et_src  = "Run Data"
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
        _mph_src  = "Run Data"
    else:
        _mph_val, _mph_str, _mph_src = None, "—", ""

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    if df is not None and "Engine RPM" in df.columns:
        c1.metric("Peak Engine RPM", f"{df['Engine RPM'].max():,.0f}", help="From Run Data")
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

    # ── Left: Run Details ─────────────────────────────────────────────────────────
    with _main_left:
        _rd_saved_msg = st.session_state.pop("run_details_saved_msg", False)
        with st.expander(
            "📋 Run Details",
            expanded=st.session_state.get("run_details_expanded", False),
            key=f"run_details_{st.session_state.get('run_details_key', 0)}",
        ):
            _rk = csv_name  # widget key shorthand — scoped to run so values reset on switch
            st.divider()
            with st.form(f"rd_form_{_rk}"):

                # ── Result & Run Type (top) ───────────────────────────────────────
                _rd_result_opts = ["", "Win", "Loss", "Bye"]
                _rd_result_val  = _rd.get("result") or ""
                _rd_result_idx  = _rd_result_opts.index(_rd_result_val) if _rd_result_val in _rd_result_opts else 0
                _rd_result = st.selectbox("Result", options=_rd_result_opts, index=_rd_result_idx,
                                key=f"rd_result_{_rk}",
                                help="Did this run end in a win, loss, or bye run?")

                _rd_rt_opts    = ["Full Pass", "Half-Track Pass", "Tire Shake / Aborted Run", "Tune-Up Pass", "Other"]
                _rd_rt_current = run.get("run_type") or "Full Pass"
                _rd_rt_idx     = _rd_rt_opts.index(_rd_rt_current) if _rd_rt_current in _rd_rt_opts else 0
                _rd_run_type = st.selectbox(
                    "Run Type",
                    options=_rd_rt_opts,
                    index=_rd_rt_idx,
                    key=f"rd_run_type_{_rk}",
                )

                # ── Tire Pressures ────────────────────────────────────────────────
                st.caption("**Tire Pressures (psi)**")
                _rd_col1, _rd_col2 = st.columns(2)
                _rd_tire_fl = _rd_col1.number_input("FL", min_value=0.0, max_value=60.0,
                                value=float(_rd.get("tire_pressure_fl") or 0.0), step=0.5, format="%.1f", key=f"rd_fl_{_rk}")
                _rd_tire_fr = _rd_col2.number_input("FR", min_value=0.0, max_value=60.0,
                                value=float(_rd.get("tire_pressure_fr") or 0.0), step=0.5, format="%.1f", key=f"rd_fr_{_rk}")
                _rd_tire_rl = _rd_col1.number_input("RL", min_value=0.0, max_value=60.0,
                                value=float(_rd.get("tire_pressure_rl") or 0.0), step=0.5, format="%.1f", key=f"rd_rl_{_rk}")
                _rd_tire_rr = _rd_col2.number_input("RR", min_value=0.0, max_value=60.0,
                                value=float(_rd.get("tire_pressure_rr") or 0.0), step=0.5, format="%.1f", key=f"rd_rr_{_rk}")

                # ── Track / Tire Conditions ───────────────────────────────────────
                st.caption("**Track / Tire Conditions**")
                _rd_col3, _rd_col4 = st.columns(2)
                _rd_track_temp = _rd_col3.number_input("Track Temp (°F)", min_value=-20.0, max_value=200.0,
                                value=float(_rd.get("track_temp_f") or 0.0), step=1.0, format="%.0f", key=f"rd_track_temp_{_rk}")
                _rd_tire_temp = _rd_col4.number_input("Tire Temp (°F)", min_value=0.0, max_value=300.0,
                                value=float(_rd.get("tire_temp_f") or 0.0), step=1.0, format="%.0f", key=f"rd_tire_temp_{_rk}")

                # ── RPM ───────────────────────────────────────────────────────────
                st.caption("**RPM**")
                _rd_col5, _rd_col6 = st.columns(2)
                _rd_launch_rpm  = _rd_col5.number_input("Launch RPM", min_value=0, max_value=15000,
                                value=int(float(_rd.get("launch_rpm") or 0)), step=100, key=f"rd_launch_rpm_{_rk}")
                _rd_shift_point = _rd_col6.number_input("Shift Point", min_value=0, max_value=15000,
                                value=int(float(_rd.get("shift_point") or 0)), step=100, key=f"rd_shift_{_rk}")

                # ── Fuel System ───────────────────────────────────────────────────
                st.caption("**Fuel System**")
                _rd_col7, _rd_col8 = st.columns(2)
                _rd_main_jet    = _rd_col7.number_input("Main Jet", min_value=0.0, max_value=999.0,
                                value=float(_rd.get("main_jet") or 0.0), step=0.001, format="%.3f", key=f"rd_main_jet_{_rk}")
                _rd_hs_jet      = _rd_col8.number_input("HS Jet", min_value=0.0, max_value=999.0,
                                value=float(_rd.get("hs_jet") or 0.0), step=0.001, format="%.3f", key=f"rd_hs_jet_{_rk}")
                _rd_hs_open_psi = _rd_col7.number_input("HS Open PSI", min_value=0.0, max_value=500.0,
                                value=float(_rd.get("hs_open_psi") or 0.0), step=1.0, format="%.0f", key=f"rd_hs_psi_{_rk}")

                # ── Blower ────────────────────────────────────────────────────────
                st.caption("**Blower**")
                _rd_col9, _rd_col10 = st.columns(2)
                _rd_top_pulley  = _rd_col9.number_input("Top Pulley", min_value=0, max_value=100,
                                value=int(float(_rd.get("top_pulley") or 0)), step=1, key=f"rd_top_pulley_{_rk}")
                _rd_bot_pulley  = _rd_col10.number_input("Bottom Pulley", min_value=0, max_value=100,
                                value=int(float(_rd.get("bottom_pulley") or 0)), step=1, key=f"rd_bot_pulley_{_rk}")
                _rd_overdrive   = ((_rd_bot_pulley / _rd_top_pulley) - 1) if _rd_top_pulley else 0.0
                _rd_col9.caption(f"Overdrive: **{_rd_overdrive * 100:.2f}%**")

                # ── Wheelie Bar ───────────────────────────────────────────────────
                st.caption("**Wheelie Bar**")
                _rd_col11, _rd_col12 = st.columns(2)
                _rd_wb_d = _rd_col11.number_input("Wheelie Bar – D", min_value=0.0, max_value=10.0,
                                value=float(_rd.get("wheelie_bar_d") or 0.0), step=0.001, format="%.3f", key=f"rd_wb_d_{_rk}")
                _rd_wb_p = _rd_col12.number_input("Wheelie Bar – P", min_value=0.0, max_value=10.0,
                                value=float(_rd.get("wheelie_bar_p") or 0.0), step=0.001, format="%.3f", key=f"rd_wb_p_{_rk}")

                # ── Ignition ──────────────────────────────────────────────────────
                st.caption("**Ignition**")
                _rd_spark_plug = st.text_input("Spark Plug", value=_rd.get("spark_plug") or "",
                                placeholder="e.g. NGK-R-5671-11", key=f"rd_spark_plug_{_rk}")
                _rd_col13, _rd_col14 = st.columns(2)
                _rd_plug_gap   = _rd_col13.text_input("Plug Gap", value=_rd.get("plug_gap") or "",
                                placeholder='0.016"', key=f"rd_plug_gap_{_rk}")
                _rd_valve_lash = _rd_col14.text_input("Lash INT/EXT", value=_rd.get("valve_lash") or "",
                                placeholder='0.016"/0.016"', key=f"rd_valve_lash_{_rk}")

                # ── Run notes (bottom) ────────────────────────────────────────────
                _rd_notes = st.text_area("Run notes", value=_rd.get("notes") or "",
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
                    "result":           _rd_result,
                }
                _rd_update_profile = st.checkbox(
                    "Save this setup as my new default",
                    value=False,
                    key=f"rd_update_profile_{_rk}",
                )
                _rd_submitted = st.form_submit_button("💾 Save Run Details", use_container_width=True, type="primary")
                if _rd_submitted:
                    # Capture active_run_id before any Supabase round-trip.
                    _save_active_run_id = st.session_state.get("active_run_id")
                    run["run_type"] = _rd_run_type
                    run["run_details"] = _rd_payload
                    save_run(csv_name, run)
                    if _rd_update_profile:
                        cfg["car_profile"] = {k: v for k, v in _rd_payload.items() if k != "notes"}
                        save_config(cfg)
                        st.success("Run saved and default setup updated! ✅")
                    else:
                        st.success("Run saved! ✅")
                    # Restore run identity. run_selector cannot be written here (widget
                    # already instantiated) — the pre-render guard handles it next render.
                    if _save_active_run_id:
                        st.session_state["active_run_id"] = _save_active_run_id
                        st.query_params["run"] = _save_active_run_id
                        st.session_state["_run_selector_idx"] = next(
                            (i + 1 for i, r in enumerate(saved_runs) if r["filename"] == _save_active_run_id),
                            st.session_state.get("_run_selector_idx", 0),
                        )
                    st.session_state["run_details_key"] = st.session_state.get("run_details_key", 0) + 1
                    st.session_state["run_details_expanded"] = False
                    st.session_state["run_details_saved_msg"] = True
                    st.rerun()

        if _rd_saved_msg:
            st.success("Run details saved.")

    # ── Right: Changes from last run (auto-diff) ──────────────────────────────────
    with _main_right:
        with st.expander("🔄 Changes from last run", expanded=False):
            # Find the most recent earlier run for the same user+car (or just user if no car).
            # Uses created_at ordering — same as listsaved_runs().
            _diff_prev_rd: dict = {}
            _is_first_run = False
            if _sb:
                try:
                    _cur_ts_rows = _sb.table("runs").select("created_at") \
                        .eq("username", current_user).eq("csv_filename", csv_name).execute().data
                    _cur_ts = _cur_ts_rows[0]["created_at"] if _cur_ts_rows else None
                    if _cur_ts:
                        if _run_car_id:
                            _prev_rows = _sb.table("runs").select("run_data") \
                                .eq("username", current_user) \
                                .eq("car_id", _run_car_id) \
                                .lt("created_at", _cur_ts) \
                                .order("created_at", desc=True).limit(1).execute().data
                        else:
                            _prev_rows = _sb.table("runs").select("run_data") \
                                .eq("username", current_user) \
                                .lt("created_at", _cur_ts) \
                                .order("created_at", desc=True).limit(1).execute().data
                        if _prev_rows:
                            _diff_prev_rd = (_prev_rows[0].get("run_data") or {}).get("run_details") or {}
                        else:
                            _is_first_run = True
                except Exception:
                    pass

            # Field definitions: (run_details key, display label, printf format or None for strings)
            _DIFF_FIELDS = [
                ("tire_pressure_fl", "Tire Pressure FL", "%.1f"),
                ("tire_pressure_fr", "Tire Pressure FR", "%.1f"),
                ("tire_pressure_rl", "Tire Pressure RL", "%.1f"),
                ("tire_pressure_rr", "Tire Pressure RR", "%.1f"),
                ("track_temp_f",     "Track Temp (°F)",  "%.0f"),
                ("tire_temp_f",      "Tire Temp (°F)",   "%.0f"),
                ("launch_rpm",       "Launch RPM",        "%.0f"),
                ("shift_point",      "Shift Point",       "%.0f"),
                ("main_jet",         "Main Jet",          "%.3f"),
                ("hs_jet",           "HS Jet",            "%.3f"),
                ("hs_open_psi",      "HS Open PSI",       "%.0f"),
                ("top_pulley",       "Top Pulley",        "%.0f"),
                ("bottom_pulley",    "Bottom Pulley",     "%.0f"),
                ("wheelie_bar_d",    "Wheelie Bar D",     "%.3f"),
                ("wheelie_bar_p",    "Wheelie Bar P",     "%.3f"),
                ("spark_plug",       "Spark Plug",        None),
                ("plug_gap",         "Plug Gap",          None),
                ("valve_lash",       "Valve Lash",        None),
            ]

            def _diff_fmt(v, fmt: str | None) -> str:
                if fmt is None:
                    return str(v).strip() if v else ""
                try:
                    f = float(v)
                    return fmt % f if f != 0.0 else ""
                except (TypeError, ValueError):
                    return str(v).strip() if v else ""

            _cur_rd_for_diff = run.get("run_details") or {}

            if _is_first_run:
                st.caption("Baseline run — no previous run to compare.")
            elif not _diff_prev_rd:
                st.caption("No previous run found to compare.")
            else:
                _diffs = []
                for _fk, _flabel, _ffmt in _DIFF_FIELDS:
                    _cur_s = _diff_fmt(_cur_rd_for_diff.get(_fk), _ffmt)
                    _prv_s = _diff_fmt(_diff_prev_rd.get(_fk), _ffmt)
                    if _cur_s == _prv_s or (not _cur_s and not _prv_s):
                        continue
                    _diffs.append((_flabel, _prv_s or "—", _cur_s or "—"))
                if _diffs:
                    for _dlabel, _d_from, _d_to in _diffs:
                        st.markdown(
                            f"**{_dlabel}:** "
                            f"<span style='color:#ef4444'>{_d_from}</span> → "
                            f"<span style='color:#22c55e'>{_d_to}</span>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("No setup changes from previous run.")

    st.markdown("---")

    # ── AI Virtual Tuner ──────────────────────────────────────────────────────────
    st.markdown("## 🤖 AI Virtual Tuner")
    if not has_feature("ai_tuner"):
        st.info("🤖 AI Virtual Tuner available on Pro.")
        if st.button("⬆️ Upgrade to Pro", key="ai_tuner_upgrade_btn"):
            _sv = st.query_params.get("session", "")
            st.query_params.clear()
            if _sv:
                st.query_params["session"] = _sv
            st.query_params["p"] = "upgrade"
            st.session_state["current_page"] = "upgrade"
            st.rerun()
        st.markdown(
            "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
            "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
            "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
            "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
            "text-decoration:none;'>Contact Us</a></div>",
            unsafe_allow_html=True,
        )
        st.stop()

    def _build_ai_payload(csv_name: str, run_rec: dict, df, available_channels: list,
                          allsaved_runs: list, car_cfg: dict) -> str:
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
        _other_runs = [s for s in reversed(allsaved_runs) if s["filename"] != csv_name]
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
                        calc_density_altitude(wx.get("temperature_f"), wx.get("pressure_hpa")) or 0
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
                    _payload = _build_ai_payload(csv_name, run, df, available_channels, saved_runs, cfg)
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
                        st.session_state["active_run_id"] = csv_name
                        st.query_params["run"] = csv_name
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

    st.markdown("""
    <style>
    div[data-testid="stMarkdownContainer"] table,
    div[data-testid="stMarkdownContainer"] tr,
    div[data-testid="stMarkdownContainer"] td {
        border: none !important;
        border-bottom: none !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── Car Profile + Run Details cards ──────────────────────────────────────────
    _has_car_profile = any(cfg.get(k) for k in (
        "engine_desc","fuel_type","blower_type","blower_size","carb_desc",
        "converter_desc","transmission","rear_gear_ratio","tire_size",
    ))
    _rd_saved = run.get("run_details", {})
    _has_run_details = any(_rd_saved.get(k) for k in ("tire_pressure_fl","track_temp_f",
                                                        "launch_rpm","notes","result"))

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
    <div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;background:#0a0a0a;font-family:monospace;">
      <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:10px;">
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

        # ── Result (Win / Loss / Bye) ──
        _result_val = _r.get("result", "")
        _result_rows = ""
        if _result_val:
            _result_icon  = {"Win": "🏆", "Loss": "❌", "Bye": "🚗"}.get(_result_val, "")
            _result_color = {"Win": "#2ecc71", "Loss": "#e74c3c", "Bye": "#f0a500"}.get(_result_val, "#eee")
            _result_rows  = (f'<tr><td style="color:#888;padding:2px 8px 2px 0;white-space:nowrap;">Result</td>'
                             f'<td style="color:{_result_color};font-weight:700;text-align:right;">'
                             f'{_result_icon} {_result_val}</td></tr>')

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
            _result_rows +
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
    <div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;background:#0a0a0a;font-family:monospace;">
      <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:10px;">
        📋 Run Details
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:0.88rem;">{_rd_rows}</table>
      {_notes_html}
    </div>""", unsafe_allow_html=True)

    st.markdown("---")

    # ── Timeslip + Weather cards ──────────────────────────────────────────────────
    slip = run.get("timeslip")
    # Detect car-not-found sentinel (scan ran but user's car wasn't on the slip).
    # We keep slip intact so the card still renders with whatever data was extracted
    # (track name, date, etc.); a soft caption is shown inside the card instead.
    _slip_car_not_found = bool(slip and slip.get("car_found") is False)
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
    <div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;background:#0a0a0a;font-family:monospace;">
      <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:6px;">
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
        <tr>
          <td style="color:#cc1111;font-weight:700;padding:6px 8px 3px 0;">ET</td>
          <td style="color:#cc1111;font-weight:700;font-size:1.2rem;text-align:right;">{slip.get('ft_1320') or '—'}</td>
          <td></td>
          <td style="color:#cc1111;font-weight:700;padding:6px 8px 3px 0;">MPH</td>
          <td style="color:#cc1111;font-weight:700;font-size:1.2rem;text-align:right;">{slip.get('mph_1320') or '—'}</td>
        </tr>
      </table>
    </div>
    """, unsafe_allow_html=True)
                if _slip_car_not_found:
                    # ── Inline car-number fix ──────────────────────────────────────
                    st.warning(
                        "Car number **" + (_effective_car_num or "—") + "** wasn't found on "
                        "this timeslip — performance stats unavailable. "
                        "The track may have printed a different number.",
                        icon="⚠️",
                    )
                    st.markdown("**Correct car number and re-scan:**")
                    _fix_input_col, _fix_btn_col = st.columns([3, 2])
                    _inline_fix_num = _fix_input_col.text_input(
                        "Correct car number on timeslip",
                        value=_effective_car_num,
                        key="inline_carfix_car_num",
                        label_visibility="collapsed",
                        placeholder="Car # as printed on timeslip",
                    )
                    _fix_new_slip = st.file_uploader(
                        "Or upload a different timeslip photo",
                        type=["jpg", "jpeg", "png", "webp"],
                        key="inline_carfix_photo",
                        help="Use this if the wrong photo was uploaded originally",
                    )
                    if _fix_btn_col.button(
                        "🔄 Re-scan timeslip", key="inline_carfix_rescan_btn",
                        use_container_width=True, type="primary",
                        help="Scan the timeslip with the corrected car number",
                    ):
                        _fix_num = _inline_fix_num.strip()
                        # Resolve which bytes/media to scan: new upload beats stored image
                        _fix_use_new = _fix_new_slip is not None
                        if _fix_use_new:
                            _fix_scan_bytes = _fix_new_slip.read()
                            _fix_scan_ext   = _fix_new_slip.name.rsplit(".", 1)[-1].lower()
                            _fix_scan_media = _SLIP_MIME.get(_fix_scan_ext, "image/jpeg")
                        else:
                            _fix_scan_bytes = _slip_bytes
                            _fix_scan_media = _slip_media
                        if not _fix_num:
                            st.error("Enter the car number first.")
                        elif not api_key:
                            st.error("ANTHROPIC_API_KEY is not set.")
                        elif not _fix_scan_bytes:
                            st.error("No timeslip image available — upload one above.")
                        else:
                            with st.spinner("🎫 Scanning with corrected car number…"):
                                try:
                                    # If a new photo was supplied, replace the stored timeslip
                                    if _fix_use_new and _sb:
                                        _fix_stem  = re.sub(r"[^\w\-]", "_", Path(csv_name).stem)
                                        _fix_s_key = f"{current_user}/{_fix_stem}.{_fix_scan_ext}"
                                        try:
                                            _sb.storage.from_("timeslips").upload(
                                                path=_fix_s_key, file=_fix_scan_bytes,
                                                file_options={"upsert": "true",
                                                              "content-type": _fix_scan_media},
                                            )
                                            run["timeslip_storage_key"] = _fix_s_key
                                        except Exception as _fix_up_err:
                                            st.warning(f"Photo upload failed: {_fix_up_err}")
                                    _fix_scan = scan_timeslip(
                                        _fix_scan_bytes, _fix_scan_media, api_key, _fix_num
                                    )
                                    _fix_scan["_scanned_with"] = _fix_num
                                    run["timeslip"] = _fix_scan
                                    # Update the run's car number so future rescans use
                                    # the corrected value and the run details card shows it.
                                    run["car_number"] = _fix_num
                                    if _fix_scan.get("car_found") is not False:
                                        # Auto-populate W/L result from scan if not already set
                                        _fix_result = _normalize_slip_result(_fix_scan.get("result"))
                                        if _fix_result:
                                            _fix_rd = run.get("run_details") or {}
                                            if not _fix_rd.get("result"):
                                                _fix_rd["result"] = _fix_result
                                                run["run_details"] = _fix_rd
                                    run["csv_name"] = csv_name
                                    save_run(csv_name, run)
                                    st.session_state["active_run_id"] = csv_name
                                    st.query_params["run"] = csv_name
                                    st.rerun()
                                except Exception as _fix_err:
                                    st.error(f"Scan failed: {_fix_err}")

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

                _wx_elev  = float(cfg.get("elev_ft") or 0)
                pres_inhg = pres * 0.02953 if pres else None
                da        = calc_density_altitude(temp, pres, hum, _wx_elev)
                da_str    = f"{da:,.0f} ft" if da is not None else "—"
                da_color  = "#ff6b6b" if (da or 0) > 2000 else "#60c0f0" if (da or 0) < 500 else "#f0c040"
                da_note   = "thin air"   if (da or 0) > 2000 else "good air" if (da or 0) < 500 else "average air"

                _v_temp = f"{temp:.1f} °F"         if temp      is not None else "—"
                _v_hum  = f"{hum:.0f}%"            if hum       is not None else "—"
                _v_pres = f"{pres_inhg:.2f} inHg"  if pres_inhg is not None else "—"
                _v_wind = f"💨 {wind:.1f} mph {wdir}" if wind  is not None else "—"

                st.markdown(f"""
    <div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;background:#0a0a0a;font-family:monospace;">
      <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:4px;">🌤️ Weather at Run Time</div>
      <div style="color:#666;font-size:0.8rem;margin-bottom:12px;">{wx_date} &nbsp;·&nbsp; {wx_loc}</div>
      <table style="width:100%;border-collapse:collapse;font-size:0.92rem;">
        <tr><td style="color:#888;padding:3px 0;">Temperature</td><td style="color:#eee;text-align:right;">{_v_temp}</td></tr>
        <tr><td style="color:#888;padding:3px 0;">Humidity</td><td style="color:#eee;text-align:right;">{_v_hum}</td></tr>
        <tr><td style="color:#888;padding:3px 0;">Barometric Pressure</td><td style="color:#eee;text-align:right;">{_v_pres}</td></tr>
        <tr><td style="color:#888;padding:3px 0;">Wind</td><td style="color:#eee;text-align:right;">{_v_wind}</td></tr>
        <tr><td style="color:#cc1111;font-weight:700;padding:6px 0 3px;">Density Altitude</td><td style="color:{da_color};font-weight:700;font-size:1.1rem;text-align:right;">{da_str}</td></tr>
      </table>
      <div style="color:#666;font-size:0.75rem;margin-top:8px;">{da_note}</div>
    </div>
    """, unsafe_allow_html=True)

            # Attribution in a new row below the card pair — right column only
            _wx_source = wx.get("_source", "")
            if _wx_source:
                _, _weather_caption_col = st.columns([1, 1])
                with _weather_caption_col:
                    if _wx_source == "weatherkit":
                        st.caption("✅ Weather data: Apple WeatherKit")
                    elif _wx_source == "open-meteo":
                        st.caption("ℹ️ Weather data sourced from Open-Meteo historical archive. Runs within 10 days use Apple WeatherKit for precise conditions — older runs use regional model estimates which may vary from exact track conditions.")

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

    elif _slip_storage_key and not car_number_input.strip():
        st.info(
            "Enter your car number in the **Create New Run** form to scan timeslips. "
            "RaceFusion needs your car number to identify your lane on the timeslip.",
            icon="ℹ️",
        )
        st.markdown("---")
    elif not slip and _slip_bytes is not None:
        # Image is stored but scan returned no data (empty dict) or failed silently.
        st.info(
            "ℹ️ Timeslip uploaded but data could not be read. "
            "Check your car number is correct, then click Re-scan to try again.",
            icon="🎫",
        )
        if st.button("🔄 Re-scan timeslip", key="inline_rescan_btn"):
            run.pop("timeslip", None)
            run.pop("weather", None)
            save_run(csv_name, run)
            st.session_state["active_run_id"] = csv_name
            st.query_params["run"] = csv_name
            st.rerun()
        st.markdown("---")
    elif _slip_bytes is None:
        st.info("📎 Upload a timeslip photo in the sidebar to add run data and auto-fetch weather.", icon="🎫")
        st.markdown("---")

    # Always show the raw timeslip photo when the image is in storage,
    # regardless of whether OCR scan data is available.
    if _slip_bytes is not None:
        # Expand by default when there's no scan data to show (helps the user
        # see what was uploaded and decide whether to re-scan).
        _photo_expanded = not bool(slip)
        with st.expander("📷 Timeslip photo", expanded=_photo_expanded):
            st.image(correct_image_orientation(_slip_bytes), use_container_width=True)

    # ── Channel charts (one chart per group, all channels overlaid) ───────────────
    if not _csv_available:
        st.markdown(
            "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
            "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
            "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
            "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
            "text-decoration:none;'>Contact Us</a></div>",
            unsafe_allow_html=True,
        )
        st.stop()

    if not has_feature("channel_charts"):
        st.markdown("---")
        st.markdown(
            """<div style="text-align:center;padding:32px 20px;border:1px solid #2a0000;
            border-radius:10px;background:#0a0a0a;">
            <div style="font-size:2.5rem;margin-bottom:8px;">🔒</div>
            <h3 style="color:#cc1111;">Channel Charts — Pro Feature</h3>
            <p style="color:#888;">Upgrade to Pro or Crew Chief to unlock interactive channel charts.</p>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("⬆️ Upgrade to Pro", key="charts_upgrade_btn", type="primary"):
            st.session_state["current_page"] = "upgrade"
            st.query_params["p"] = "upgrade"
            st.rerun()
        st.markdown(
            "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
            "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
            "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
            "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
            "text-decoration:none;'>Contact Us</a></div>",
            unsafe_allow_html=True,
        )
        st.stop()

    # EGT channels are shown in the dedicated EGT panel above — skip here
    _egt_group_name = "🌡️ EGT (Exhaust Temps)"
    _egt_chs_set = set(_cyl_channels) | ({_avg_egt_ch} if _avg_egt_ch else set())

    st.caption("*Click a legend item to toggle that channel on or off.*")
    for grp in selected_groups:
        if grp == _egt_group_name:
            continue  # already rendered in EGT panel
        if grp in channel_groups:
            grp_channels = [ch for ch in channel_groups[grp]
                            if ch in available_channels and ch not in _egt_chs_set]
        else:
            grp_channels = [ch for ch in available_channels
                            if channel_to_group.get(ch) == grp and ch not in _egt_chs_set]

        if not grp_channels:
            continue

        fig = make_overlay_chart(grp_channels, grp, time_col, df_view, t_range, mode, chart_height,
                                 dark=True, global_rpm_max=_global_rpm_max,
                                 y_min=-10, y_max=_chart_rpm_max, smooth_points=smooth_points)
        if fig:
            st.markdown(f"### {grp}")
            st.plotly_chart(fig, use_container_width=True)
            st.markdown("---")

    # ── Custom Overlay chart ──────────────────────────────────────────────────────
    if custom_channels:
        fig = make_overlay_chart(custom_channels, "Custom Overlay", time_col, df_view, t_range, mode, chart_height,
                                 dark=True, global_rpm_max=_global_rpm_max,
                                 y_min=-10, y_max=_chart_rpm_max, smooth_points=smooth_points)
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
                template="plotly_dark", showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
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
            _fig_eng.update_layout(height=320, template="plotly_dark",
                margin=dict(l=10, r=10, t=20, b=10),
                xaxis=dict(visible=False, range=[0, 1]),
                yaxis=dict(visible=False, range=[-1.1, 4.2]),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(_fig_eng, use_container_width=True)

        st.caption("**EGT over time — all cylinders**")
        _ts_channels = _cyl_channels + ([_avg_egt_ch] if _avg_egt_ch else [])
        _ts_fig = make_overlay_chart(_ts_channels, "EGT", time_col, df_view, t_range, mode, 320,
                                     dark=True, global_rpm_max=_global_rpm_max,
                                     y_min=-10, y_max=_chart_rpm_max, smooth_points=smooth_points)
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
        _w_da      = _sum_slip.get("density_alt_ft") or calc_density_altitude(_w_temp, _w_baro_hpa)
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
    st.divider()
    st.markdown("##### 📡 Channel Peaks")
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

    st.caption("📡 **All Channels** — Groups and visibility are editable.")
    with st.expander("📡 All Channels"):
        st.caption("Uncheck channels to hide them from all graphs. Use the Group ✏️ dropdown to move a channel to a different chart.")
        _UNGROUPED_LABEL = "— Ungrouped —"
        _group_options   = list(channel_groups.keys()) + ["📦 Other", _UNGROUPED_LABEL]
        _col_w = [1, 3, 3, 2, 2, 2, 2]

        # Header row
        _hdr = st.columns(_col_w)
        for _hi, _hl in enumerate(["Show", "Channel", "Group ✏️", "Min", "Max", "Mean", "Pts"]):
            _hdr[_hi].markdown(
                f"<span style='font-size:0.78rem;font-weight:600;color:#888;'>{_hl}</span>",
                unsafe_allow_html=True,
            )
        st.markdown(
            "<hr style='margin:2px 0 6px 0;border:none;border-top:1px solid rgba(128,128,128,0.35);'>",
            unsafe_allow_html=True,
        )

        _changes_made = False
        _new_ch_prefs  = dict(_ch_prefs)

        for ch in _all_channels_full:
            s      = df[ch].dropna()
            _cmin  = round(float(s.min()),  3) if not s.empty else 0.0
            _cmax  = round(float(s.max()),  3) if not s.empty else 0.0
            _cmean = round(float(s.mean()), 3) if not s.empty else 0.0
            _cpts  = int(len(s))

            _stored  = _ch_prefs.get(ch, {})
            _show    = bool(_stored.get("show", _ch_defaults.get(ch, True)))
            _raw_grp = channel_to_group.get(ch) or ""
            _grp     = _raw_grp if _raw_grp in _group_options else _UNGROUPED_LABEL
            _grp_idx = _group_options.index(_grp) if _grp in _group_options else len(_group_options) - 1

            _row = st.columns(_col_w)
            _show_val = _row[0].checkbox(
                "", value=_show,
                key=f"ach_show_{csv_name}_{ch}",
                label_visibility="collapsed",
            )
            _row[1].markdown(ch)
            _grp_val = _row[2].selectbox(
                "", options=_group_options, index=_grp_idx,
                key=f"ach_grp_{csv_name}_{ch}",
                label_visibility="collapsed",
            )
            _row[3].markdown(f"{_cmin:.3f}")
            _row[4].markdown(f"{_cmax:.3f}")
            _row[5].markdown(f"{_cmean:.3f}")
            _row[6].markdown(str(_cpts))
            st.markdown(
                "<hr style='margin:0;border:none;border-top:1px solid rgba(128,128,128,0.12);'>",
                unsafe_allow_html=True,
            )

            # Accumulate any changes
            _save_grp = "📦 Other" if _grp_val == _UNGROUPED_LABEL else _grp_val
            _old_grp  = "📦 Other" if _grp    == _UNGROUPED_LABEL else _grp
            if _show_val != _show or _save_grp != _old_grp:
                _new_ch_prefs[ch] = {"show": _show_val, "group": _save_grp}
                _changes_made = True

        if _changes_made:
            cfg["channel_prefs"] = _new_ch_prefs
            save_config(cfg)
            st.session_state["active_run_id"] = csv_name
            st.query_params["run"] = csv_name
            st.rerun()

    # ── Raw data ──────────────────────────────────────────────────────────────────
    with st.expander("📋 Raw Data Table"):
        st.dataframe(df_view, use_container_width=True, height=400)
        st.download_button(
            "⬇️ Download filtered CSV",
            data=df_view.to_csv(index=False).encode("utf-8"),
            file_name="racefusion_filtered.csv",
            mime="text/csv",
        )

    if current_user == "weeber70":
        with st.expander("🗂️ Run Record (JSON)", expanded=False):
            st.json(run)

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
            or calc_density_altitude(_wx.get("temperature_f"), _wx.get("pressure_hpa"))
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
        _all_runs = listsaved_runs()
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
    st.markdown(
        "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
        "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
        "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
        "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
        "text-decoration:none;'>Contact Us</a></div>",
        unsafe_allow_html=True,
    )

