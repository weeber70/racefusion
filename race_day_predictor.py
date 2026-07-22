"""
race_day_predictor.py — RaceFusion Race Day Predictor page.
"""
import requests
import streamlit as st
from database import _sb, _rdp_load_run_history
from config import save_config
from weather import (
    calc_density_altitude, sea_level_to_station_pressure,
    station_to_sea_level_pressure, wind_dir_label,
    _TRACK_OVERRIDES, _track_key, lookup_track, geocode, fetch_weather_rdp, fetch_metar,
)


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




def show_race_day_predictor(cfg: dict, current_user: str, access_granted: bool, logo_src: "str | None" = None):
    """Render the Race Day Predictor page."""
    import urllib.parse as _rdp_urlparse

    if logo_src:
        st.markdown(
            f'<img src="{logo_src}" style="max-width:520px;width:60%;'
            f'margin:0 auto 4px auto;display:block;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🏁 RaceFusion")
    st.markdown("# 🏁 Race Day Predictor")
    st.markdown(
        "<p style='color:#888;margin-top:-12px;'>Predicted ET and suggested dial based on your car's history + today's air.</p>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    if not access_granted:
        st.markdown(
            """<div style="text-align:center;padding:40px 20px;">
            <div style="font-size:2.5rem;margin-bottom:12px;">🔒</div>
            <h3 style="color:#cc1111;">Upgrade to continue</h3>
            <p style="color:#888;">Race Day Predictor requires an active subscription.</p>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("⬆️ View Upgrade Options", key="rdp_upgrade_btn", type="primary"):
            st.session_state["current_page"] = "upgrade"
            st.query_params["p"] = "upgrade"
            st.rerun()
        st.stop()

    # ── Current Conditions ────────────────────────────────────────────────────
    st.markdown("## 🌤️ Current Conditions")
    _rdp_cfg = cfg   # cfg already loaded above

    # ── Page-local track field: "what track am I at today?" ──────────────────
    # Stored under predictor-scoped cfg keys (rdp_*). Nothing else in the app
    # reads or writes these — completely independent of the run-upload /
    # weather-lookup pipeline. No fallback to runs or any global setting.
    _rdp_in_c1, _rdp_in_c2 = st.columns([3, 1], vertical_alignment="bottom")
    _rdp_track_input = _rdp_in_c1.text_input(
        "📍 Track you're at today",
        value=_rdp_cfg.get("rdp_location_name", ""),
        placeholder="e.g. Gainesville Raceway — or Gainesville, FL",
        key="rdp_track_input",
        help="Live weather for the ET prediction is pulled for this location. "
             "This field is local to the Predictor and doesn't affect any run's data.",
    )
    if _rdp_in_c2.button("Set track", key="rdp_set_track_btn", use_container_width=True):
        _rdp_in = _rdp_track_input.strip()
        if not _rdp_in:
            st.error("Enter a track or city first.")
        else:
            with st.spinner(f"📍 Locating {_rdp_in}…"):
                _rdp_set_lat, _rdp_set_lon, _rdp_set_label = None, None, ""
                _rdp_set_elev = None
                _rdp_set_tk = lookup_track(_rdp_in)
                if _rdp_set_tk:
                    _rdp_set_lat   = _rdp_set_tk["lat"]
                    _rdp_set_lon   = _rdp_set_tk["lon"]
                    _rdp_set_label = _rdp_set_tk["display_name"]
                    _rdp_set_elev  = _rdp_set_tk.get("elev_ft")
                else:
                    _rdp_set_lat, _rdp_set_lon, _rdp_set_label = geocode(_rdp_in)
            if _rdp_set_lat is None:
                st.error(f"Couldn't locate \"{_rdp_in}\" — try a nearby city and state.")
            else:
                cfg["rdp_location_name"]  = _rdp_in
                cfg["rdp_location_label"] = _rdp_set_label or _rdp_in
                cfg["rdp_lat"]     = _rdp_set_lat
                cfg["rdp_lon"]     = _rdp_set_lon
                cfg["rdp_elev_ft"] = _rdp_set_elev
                save_config(cfg)
                st.session_state["rdp_weather"] = None  # re-fetch at new coords
                st.rerun()

    _rdp_lat        = _rdp_cfg.get("rdp_lat")
    _rdp_lon        = _rdp_cfg.get("rdp_lon")
    _rdp_elev_ft    = float(_rdp_cfg.get("rdp_elev_ft") or 0)
    _rdp_loc_label  = _rdp_cfg.get("rdp_location_label", "") or _rdp_cfg.get("rdp_location_name", "")
    _rdp_fallback_label = ""   # retained for downstream display logic

    # Hardcoded drag-strip override: exact coordinates for known tracks beat
    # whatever geocoding stored.
    _rdp_ov = _TRACK_OVERRIDES.get(
        _track_key(_rdp_cfg.get("rdp_location_name") or _rdp_loc_label)
    )
    if _rdp_ov:
        _rdp_lat     = _rdp_ov["lat"]
        _rdp_lon     = _rdp_ov["lon"]
        _rdp_elev_ft = float(_rdp_ov.get("elev_ft") or 0)

    if not _rdp_lat or not _rdp_lon:
        st.info("📍 Set the track you're at today (above) to fetch live weather.")
    else:
        # Fetch and cache track elevation if not yet stored — needed for altimeter conversion
        if not _rdp_elev_ft:
            try:
                _ev_r = requests.get(
                    "https://api.open-meteo.com/v1/elevation",
                    params={"latitude": float(_rdp_lat), "longitude": float(_rdp_lon)},
                    timeout=5,
                )
                _ev_m = (_ev_r.json().get("elevation") or [None])[0]
                if _ev_m is not None:
                    _rdp_elev_ft = float(_ev_m) / 0.3048
                    cfg["rdp_elev_ft"] = _rdp_elev_ft
                    save_config(cfg)
            except Exception:
                pass

        _rdp_display_label = _rdp_fallback_label or _rdp_loc_label
        st.caption(f"📍 {_rdp_display_label}"
                   + (" *(from recent run)*" if _rdp_fallback_label else ""))

        # Auto-fetch on first load OR when coordinates change (e.g. override applied)
        _rdp_coord_sig = f"{_rdp_lat},{_rdp_lon}"
        if ("rdp_weather" not in st.session_state
                or st.session_state.get("rdp_weather_coord_sig") != _rdp_coord_sig):
            st.session_state["rdp_weather"] = None
            st.session_state["rdp_weather_coord_sig"] = _rdp_coord_sig

        if st.button("🔄 Refresh Weather", type="secondary", key="rdp_refresh"):
            st.session_state["rdp_weather"] = None

        if st.session_state["rdp_weather"] is None:
            with st.spinner("Fetching current conditions…"):
                try:
                    from datetime import datetime as _rdp_dt
                    st.session_state["rdp_weather"] = fetch_weather_rdp(
                        float(_rdp_lat), float(_rdp_lon), _rdp_elev_ft
                    )
                    st.session_state["rdp_weather_fetched_at"] = _rdp_dt.now().strftime("%-I:%M %p")
                    st.session_state["rdp_weather_coords"]     = (_rdp_lat, _rdp_lon)
                    st.session_state["rdp_weather_elev_ft"]    = _rdp_elev_ft
                except Exception as _rdp_wx_err:
                    st.error(f"Weather fetch failed: {_rdp_wx_err}")
                    st.session_state["rdp_weather"] = {}

        # Location + timestamp display
        _rdp_fetched_at   = st.session_state.get("rdp_weather_fetched_at", "")
        _rdp_used_coords  = st.session_state.get("rdp_weather_coords")
        _rdp_used_elev_ft = st.session_state.get("rdp_weather_elev_ft") or _rdp_elev_ft
        # Build location name: prefer override display_name, fall back to cfg label
        _rdp_display_name = (_rdp_ov.get("display_name") if _rdp_ov else None) or _rdp_loc_label or _rdp_display_label
        if _rdp_used_coords:
            _rdp_coord_str = f"{_rdp_used_coords[0]:.4f}, {_rdp_used_coords[1]:.4f}"
            _rdp_elev_str  = f"Elevation: {_rdp_used_elev_ft:.1f} ft" if _rdp_used_elev_ft else ""
            _rdp_loc_parts = [p for p in [_rdp_display_name, _rdp_coord_str, _rdp_elev_str] if p]
            st.caption(f"📍 {' · '.join(_rdp_loc_parts)}")
        if _rdp_fetched_at:
            st.caption(f"🕐 Updated {_rdp_fetched_at}")

        _rdp_wx  = st.session_state["rdp_weather"] or {}
        _rdp_da  = calc_density_altitude(_rdp_wx.get("temperature_f"), _rdp_wx.get("pressure_hpa"), _rdp_wx.get("humidity_pct"), _rdp_elev_ft)

        # ── Manual station override (computed later, used for prediction) ─────
        _rdp_mwx = st.session_state.get("rdp_manual_wx") or {}
        _rdp_manual_active = bool(_rdp_mwx.get("baro_inhg", 0) > 0 and _rdp_mwx.get("rh_pct", 0) > 0)
        if _rdp_manual_active:
            _mwx_baro_raw = _rdp_mwx.get("baro_inhg", 0)
            _mwx_temp_f   = _rdp_mwx.get("temp_f", 0)
            _mwx_rh_pct   = _rdp_mwx.get("rh_pct", 0)
            _mwx_is_altim = _rdp_mwx.get("baro_type", "station") == "altimeter"
            # calc_density_altitude is fed WeatherKit's SEA-LEVEL-referenced
            # pressure everywhere in the app (see _rdp_da above). To match that
            # convention: altimeter setting passes straight through; a station
            # (absolute) reading is converted UP to its sea-level equivalent.
            # Humidity must be included — same as the WeatherKit path.
            if _mwx_is_altim:
                _mwx_slp_inhg = _mwx_baro_raw  # already sea-level referenced
            else:
                _mwx_slp_inhg = station_to_sea_level_pressure(_mwx_baro_raw, _rdp_elev_ft)
            _mwx_p_hpa = _mwx_slp_inhg * 33.8639  # inHg → hPa
            _rdp_manual_da = calc_density_altitude(_mwx_temp_f, _mwx_p_hpa, _mwx_rh_pct)
        else:
            _rdp_manual_da = None
        _rdp_pred_da = _rdp_manual_da if _rdp_manual_active else _rdp_da

        _rv_temp = f"{_rdp_wx['temperature_f']:.1f} °F"            if _rdp_wx.get("temperature_f") is not None else "—"
        _rv_hum  = f"{_rdp_wx['humidity_pct']:.0f}%"               if _rdp_wx.get("humidity_pct")  is not None else "—"
        _rv_pres = f"{_rdp_wx['pressure_hpa'] * 0.02953:.2f} inHg" if _rdp_wx.get("pressure_hpa")  is not None else "—"
        _rv_da   = f"{_rdp_da:,.0f} ft"                             if _rdp_da is not None else "—"
        _rdp_wc1, _rdp_wc2, _rdp_wc3, _rdp_wc4 = st.columns(4)
        _rdp_wc1.metric("🌡️ Temp",          _rv_temp)
        _rdp_wc2.metric("💧 Humidity",       _rv_hum)
        _rdp_wc3.metric("🔵 Pressure",       _rv_pres)
        _rdp_wc4.metric("📐 Density Alt",    _rv_da)

        # ── Data source attribution ────────────────────────────────────────────
        if _rdp_wx.get("_source") == "weatherkit":
            st.caption("<span style='color:#666;font-size:0.75rem;'>📡 Weather data: Apple WeatherKit</span>", unsafe_allow_html=True)
        elif _rdp_wx.get("_source") == "open-meteo":
            st.caption("<span style='color:#666;font-size:0.75rem;'>📡 Weather data: Open-Meteo forecast</span>", unsafe_allow_html=True)

        # ── Manual weather station expander ───────────────────────────────────
        with st.expander("🌡️ Enter your own weather station readings", expanded=_rdp_manual_active):
            st.caption(
                "Use readings from a handheld weather station for maximum accuracy. "
                "These override the auto-fetched weather for ET prediction."
            )
            st.markdown("Select where your barometric pressure reading comes from:")
            _mwx_baro_type_sel = st.radio(
                "Pressure reading type",
                ["📱 Phone weather app, WeatherKit, or airport report",
                 "📟 Kestrel or handheld barometer"],
                index=0,
                horizontal=True,
                label_visibility="collapsed",
                key="rdp_mwx_baro_type",
            )
            _mwx_entry_is_altim = _mwx_baro_type_sel.startswith("📱")

            _mwx_c1, _mwx_c2, _mwx_c3 = st.columns(3)
            _mwx_temp = _mwx_c1.number_input(
                "Temperature (°F)",
                min_value=-60.0, max_value=150.0, step=0.01, format="%.2f",
                value=float(_rdp_mwx.get("temp_f", 0.0)),
                key="rdp_mwx_temp",
            )
            _mwx_baro = _mwx_c2.number_input(
                "Barometric Pressure (inHg)",
                min_value=0.0, max_value=35.0, step=0.01, format="%.2f",
                value=float(_rdp_mwx.get("baro_inhg", 0.0)),
                help="Station pressure from a physical barometer/Kestrel, or altimeter setting from a weather app — select the type above",
                key="rdp_mwx_baro",
            )
            _mwx_rh = _mwx_c3.number_input(
                "Relative Humidity (%)",
                min_value=0.0, max_value=100.0, step=0.01, format="%.2f",
                value=float(_rdp_mwx.get("rh_pct", 0.0)),
                key="rdp_mwx_rh",
            )
            _mwx_apply_col, _mwx_clear_col, _ = st.columns([1, 1, 2])
            if _mwx_apply_col.button("✅ Use these values", key="rdp_mwx_apply", type="primary"):
                if _mwx_baro > 0 and _mwx_rh > 0:
                    st.session_state["rdp_manual_wx"] = {
                        "baro_inhg": _mwx_baro,
                        "temp_f":    _mwx_temp,
                        "rh_pct":    _mwx_rh,
                        "baro_type": "altimeter" if _mwx_entry_is_altim else "station",
                    }
                    st.rerun()
            if _mwx_clear_col.button("🗑️ Clear", key="rdp_mwx_clear"):
                st.session_state.pop("rdp_manual_wx", None)
                for _k in ("rdp_mwx_baro", "rdp_mwx_temp", "rdp_mwx_rh", "rdp_mwx_baro_type"):
                    st.session_state.pop(_k, None)
                st.rerun()

            # Live DA preview (uses values currently in the inputs).
            # Same convention as the apply path: sea-level-referenced pressure
            # + humidity, matching the WeatherKit computation exactly.
            if _mwx_baro > 0 and _mwx_rh > 0:
                if _mwx_entry_is_altim:
                    _preview_slp_inhg = _mwx_baro  # already sea-level referenced
                else:
                    _preview_slp_inhg = station_to_sea_level_pressure(_mwx_baro, _rdp_elev_ft)
                _preview_da = calc_density_altitude(_mwx_temp, _preview_slp_inhg * 33.8639, _mwx_rh)
                if _preview_da is not None:
                    _active_badge = (
                        "<span style='background:#1a3a1a;color:#2ecc71;font-size:0.7rem;"
                        "padding:2px 8px;border-radius:4px;margin-left:10px;'>● ACTIVE — overrides METAR</span>"
                        if _rdp_manual_active else ""
                    )
                    st.markdown(
                        f"<div style='background:#0a1a0a;border:1px solid #2ecc71;border-radius:8px;"
                        f"padding:14px 18px;margin-top:10px;'>"
                        f"<div style='color:#888;font-size:0.78rem;margin-bottom:4px;'>"
                        f"📡 DA FROM YOUR WEATHER STATION{_active_badge}</div>"
                        f"<div style='color:#2ecc71;font-size:1.8rem;font-weight:700;'>{_preview_da:,.0f} ft</div>"
                        f"<div style='color:#666;font-size:0.75rem;margin-top:4px;'>"
                        f"{_mwx_temp:.2f}°F · {_mwx_rh:.2f}% RH · {_mwx_baro:.2f} inHg "
                        f"{'altimeter' if _mwx_entry_is_altim else 'station'}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

        st.markdown("---")

        # ── ET Prediction ─────────────────────────────────────────────────────
        st.markdown("## 🎯 ET Prediction")

        if _rdp_pred_da is None:
            st.warning("Cannot compute DA — check that weather data loaded correctly." +
                       (" Enter station pressure and humidity above." if not _rdp_manual_active else ""))
        else:
            _rdp_history = _rdp_load_run_history(current_user)

            # Supplement history with run_type, mph, and run time.
            # Key by csv_filename (plain text) to avoid UUID matching issues with .in_("id", ...).
            _rdp_extra: dict = {}
            if _sb and _rdp_history:
                try:
                    _rdp_filenames = [r["csv_filename"] for r in _rdp_history if r.get("csv_filename")]
                    _rdp_extra_rows = (
                        _sb.table("runs")
                        .select("csv_filename,run_data")
                        .in_("csv_filename", _rdp_filenames)
                        .execute().data or []
                    )
                    print(f"[RDP extra fetch] requested {len(_rdp_filenames)} filenames, got {len(_rdp_extra_rows)} rows back")
                    for _rex in _rdp_extra_rows:
                        _rex_rd   = _rex.get("run_data") or {}
                        # run_type is top-level in run_data, NOT inside run_details
                        _rex_slip = _rex_rd.get("timeslip") or {}
                        _rex_fn   = _rex.get("csv_filename", "")
                        _rex_rt   = _rex_rd.get("run_type") or ""
                        print(f"[RDP extra] {_rex_fn!r} → run_type={_rex_rt!r}")
                        _rdp_extra[_rex_fn] = {
                            "run_type": _rex_rt,
                            "mph":      _rex_slip.get("mph_1320"),
                            "time":     _rex_slip.get("time") or "",
                        }
                except Exception as _rdp_extra_err:
                    print(f"[RDP extra fetch] FAILED: {_rdp_extra_err}")
            for _r in _rdp_history:
                _rx = _rdp_extra.get(_r.get("csv_filename", ""), {})
                _r["run_type"] = _rx.get("run_type", "")
                _r["mph"]      = _rx.get("mph")
                _r["time"]     = _rx.get("time", "")

            if not _rdp_history:
                st.info("No historical runs with both ET and DA found. Log runs with timeslips to enable predictions.")
            else:
                # ── IQR fences (computed from all run ETs before any exclusion) ──────
                _rdp_all_ets = sorted(r["et"] for r in _rdp_history)
                _rdp_n       = len(_rdp_all_ets)
                _rdp_q1      = _rdp_percentile(_rdp_all_ets, 25)
                _rdp_q3      = _rdp_percentile(_rdp_all_ets, 75)
                _rdp_iqr     = _rdp_q3 - _rdp_q1
                _rdp_lo      = _rdp_q1 - 1.5 * _rdp_iqr
                _rdp_hi      = _rdp_q3 + 1.5 * _rdp_iqr
                _rdp_mean_et = sum(_rdp_all_ets) / _rdp_n

                # ── Initialize checkbox session state once per run ────────────────
                # Priority: saved DB preference → auto-default (run_type + IQR).
                # Guard ensures in-session user choices are never overwritten.
                for _rdp_r in _rdp_history:
                    _init_key = f"run_include_{_rdp_r['run_id']}"
                    if _init_key not in st.session_state:
                        _init_rtype    = _rdp_r.get("run_type", "")
                        _init_rtype_ok = (not _init_rtype) or (_init_rtype in ("Full Pass", "Bye"))
                        _init_iqr_ok   = not (_rdp_r["et"] < _rdp_lo or _rdp_r["et"] > _rdp_hi)
                        _init_db_excl  = _rdp_r.get("predictor_exclude")
                        if _init_db_excl is True:
                            st.session_state[_init_key] = False
                        elif _init_db_excl is False:
                            st.session_state[_init_key] = True
                        else:
                            # No saved preference — auto-default based on run_type + IQR
                            st.session_state[_init_key] = _init_rtype_ok and _init_iqr_ok

                # ── Build included/excluded from checkbox session state ────────────
                # The checkbox value is the single source of truth for regression input.
                _rdp_included = []
                _rdp_excluded = []
                for _rdp_r in _rdp_history:
                    _rdp_chk_key  = f"run_include_{_rdp_r['run_id']}"
                    _rdp_checked  = st.session_state.get(_rdp_chk_key, False)
                    _rdp_rtype    = _rdp_r.get("run_type", "")
                    _rdp_rtype_ok = (not _rdp_rtype) or (_rdp_rtype in ("Full Pass", "Bye"))
                    _rdp_iqr_out  = _rdp_r["et"] < _rdp_lo or _rdp_r["et"] > _rdp_hi
                    if _rdp_checked:
                        _rdp_status = "included" if _rdp_rtype_ok else "force-included (override)"
                        _rdp_included.append({**_rdp_r, "status": _rdp_status})
                    else:
                        if not _rdp_rtype_ok:
                            _rdp_status = "excluded — non-qualifying run type"
                        elif _rdp_iqr_out:
                            _rdp_status = "excluded — outlier (IQR)"
                        else:
                            _rdp_status = "excluded — manual"
                        _rdp_excluded.append({**_rdp_r, "status": _rdp_status})

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
                        _rdp_pred_et  = _rdp_slope * _rdp_pred_da + _rdp_intercept
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
                        _rp1.metric("Predicted ET",   f"{_rdp_pred_et:.3f} s")
                        _rp2.metric("Suggested Dial", f"{_rdp_dial:.3f} s", help="+0.02 s buffer to help avoid breakout")
                        _rp3.metric(
                            "DA Used",
                            f"{_rdp_pred_da:,.0f} ft",
                            help="From your weather station" if _rdp_manual_active else "From METAR / forecast",
                        )

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
                                f"⚠️ {len(_rdp_excluded)} run(s) excluded "
                                f"(IQR fences: {_rdp_lo:.3f}–{_rdp_hi:.3f}s)"
                                f" — see table below to override.</p>",
                                unsafe_allow_html=True,
                            )

                st.markdown("---")

                # ── Run History Table ─────────────────────────────────────────
                st.markdown("## 📋 Run History Used in Prediction")
                st.caption(
                    "Check **Include** to force-include a run; uncheck to force-exclude. "
                    "Non-Full Pass/Bye runs and IQR outliers are excluded automatically."
                )
                _rdp_display = []
                for _rdp_r in _rdp_included:
                    _rdp_display.append({**_rdp_r, "status": "included"})
                for _rdp_r in _rdp_excluded:
                    _rdp_display.append(_rdp_r)
                _rdp_display.sort(key=lambda x: (x.get("date") or "", x.get("time") or ""), reverse=True)

                _rdp_col_w = [1, 2, 3, 2, 2, 2, 2, 3]

                def _rdp_render_header():
                    _rdp_hdr = st.columns(_rdp_col_w)
                    for _hcol, _hlbl in zip(
                        _rdp_hdr,
                        ["Include", "Date", "Track", "Run Type", "ET", "MPH", "DA ft", "Status"],
                    ):
                        _hcol.markdown(
                            f"<span style='font-size:0.75em;color:#888;"
                            f"text-transform:uppercase;letter-spacing:0.05em'>{_hlbl}</span>",
                            unsafe_allow_html=True,
                        )
                    st.markdown(
                        "<hr style='margin:2px 0 6px 0;border-color:#333'>",
                        unsafe_allow_html=True,
                    )

                # ── Group rows by year, newest year first ─────────────────────
                _rdp_by_year: dict = {}
                for _rdp_row in _rdp_display:
                    _yr = str(_rdp_row.get("date") or "")[:4]
                    _yr = _yr if _yr.isdigit() else "Undated"
                    _rdp_by_year.setdefault(_yr, []).append(_rdp_row)
                _rdp_year_order = sorted(
                    _rdp_by_year.keys(),
                    key=lambda _y: (_y == "Undated", -int(_y) if _y.isdigit() else 0),
                )

                _rdp_changes = {}   # {run_id: new_include_bool} for runs that drifted from DB
                for _rdp_yr in _rdp_year_order:
                    _rdp_yr_rows = _rdp_by_year[_rdp_yr]
                    _rdp_yr_lbl = (
                        f"📅 {_rdp_yr} · {len(_rdp_yr_rows)} run"
                        f"{'s' if len(_rdp_yr_rows) != 1 else ''}"
                    )
                    with st.expander(_rdp_yr_lbl, expanded=(_rdp_yr == _rdp_year_order[0])):
                        _rdp_render_header()
                        for _rdp_row in _rdp_yr_rows:
                            _run_id  = _rdp_row.get("run_id", "")
                            _chk_key = f"run_include_{_run_id}"
                            _status  = _rdp_row["status"]

                            if _status == "included":
                                _status_html = "<span style='color:#4CAF50'>included</span>"
                            elif _status == "force-included (override)":
                                _status_html = "<span style='color:#2ecc71'>force-included (override)</span>"
                            elif _status == "excluded — non-qualifying run type":
                                _status_html = "<span style='color:#cc8800'>excluded — non-qualifying run type</span>"
                            elif _status == "excluded — outlier (IQR)":
                                _status_html = "<span style='color:#FFA500'>excluded — outlier (IQR)</span>"
                            else:
                                _status_html = "<span style='color:#888'>excluded — manual</span>"

                            # Date + time combined (same logic as Season Summary)
                            _rdp_date_str = _rdp_row.get("date") or ""
                            _rdp_time_str = _rdp_row.get("time") or ""
                            if _rdp_time_str:
                                try:
                                    from datetime import datetime as _rdp_dtparse
                                    _rdp_t = _rdp_dtparse.strptime(_rdp_time_str.strip(), "%H:%M")
                                    _rdp_time_disp = _rdp_t.strftime("%-I:%M %p")
                                except Exception:
                                    _rdp_time_disp = _rdp_time_str
                                _rdp_date_disp = f"{_rdp_date_str} {_rdp_time_disp}" if _rdp_date_str else _rdp_time_disp
                            else:
                                _rdp_date_disp = _rdp_date_str or "—"

                            _rdp_cols = st.columns(_rdp_col_w)
                            # No value= — session state (set during initialization above) is the source of truth
                            _new_inc = _rdp_cols[0].checkbox(
                                "",
                                key=_chk_key,
                                label_visibility="collapsed",
                            )
                            _rdp_cols[1].write(_rdp_date_disp)
                            _rdp_cols[2].write(_rdp_row.get("track") or "—")
                            _rdp_cols[3].write(_rdp_row.get("run_type") or "—")
                            _rdp_cols[4].write(f"{_rdp_row['et']:.3f}")
                            _rdp_mph = _rdp_row.get("mph")
                            _rdp_cols[5].write(f"{float(_rdp_mph):.2f}" if _rdp_mph else "—")
                            _rdp_cols[6].write(f"{int(round(_rdp_row['da'])):,}")
                            _rdp_cols[7].markdown(_status_html, unsafe_allow_html=True)
                            st.markdown(
                                "<hr style='margin:2px 0;border-color:#222'>",
                                unsafe_allow_html=True,
                            )

                            # Detect drift from DB for persistence (compare to predictor_exclude in DB)
                            _db_excl = _rdp_row.get("predictor_exclude")
                            _row_rtype    = _rdp_row.get("run_type", "")
                            _row_rtype_ok = (not _row_rtype) or (_row_rtype in ("Full Pass", "Bye"))
                            _row_iqr_ok   = not (_rdp_row["et"] < _rdp_lo or _rdp_row["et"] > _rdp_hi)
                            _auto_default = _row_rtype_ok and _row_iqr_ok
                            if _db_excl is True and _new_inc:
                                _rdp_changes[_run_id] = True   # user re-included a DB-excluded run
                            elif _db_excl is False and not _new_inc:
                                _rdp_changes[_run_id] = False  # user re-excluded a DB-included run
                            elif _db_excl is None and _new_inc != _auto_default:
                                _rdp_changes[_run_id] = _new_inc  # user overrode the auto-default

                st.caption(
                    f"{_rdp_n_incl} included · {len(_rdp_excluded)} excluded · "
                    f"IQR fences {_rdp_lo:.3f}–{_rdp_hi:.3f}s · "
                    f"Q1 {_rdp_q1:.3f}s · Q3 {_rdp_q3:.3f}s"
                )

                # Persist overrides to Supabase when a checkbox drifted from DB state.
                # No explicit st.rerun() — the checkbox interaction already triggers one.
                if _sb and _rdp_changes:
                    for _crid, _cinc in _rdp_changes.items():
                        try:
                            _crow = _sb.table("runs").select("run_data").eq("id", _crid).execute().data
                            if _crow:
                                _crd = (_crow[0].get("run_data") or {})
                                _crd["predictor_exclude"] = not _cinc
                                _sb.table("runs").update({"run_data": _crd}).eq("id", _crid).execute()
                        except Exception as _ce:
                            st.error(f"Failed to save run override: {_ce}")

    st.markdown(
        "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
        "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
        "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
        "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
        "text-decoration:none;'>Contact Us</a></div>",
        unsafe_allow_html=True,
    )
    st.stop()  # Don't render the dashboard when on predictor page
