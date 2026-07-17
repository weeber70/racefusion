"""
race_day_predictor.py — RaceFusion Race Day Predictor page.
"""
import requests
import streamlit as st
from database import _sb, load_run, _rdp_load_run_history
from weather import (
    calc_density_altitude, sea_level_to_station_pressure, wind_dir_label,
    _TRACK_OVERRIDES, _track_key, lookup_track, fetch_weather_rdp, fetch_metar,
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
    _rdp_cfg        = cfg   # cfg already loaded above
    _rdp_lat        = _rdp_cfg.get("lat")
    _rdp_lon        = _rdp_cfg.get("lon")
    _rdp_elev_ft    = float(_rdp_cfg.get("elev_ft") or 0)
    _rdp_loc_label      = _rdp_cfg.get("location_label", "") or _rdp_cfg.get("location_name", "")
    _rdp_fallback_label = ""   # set here so it's always defined

    # If a run is currently active, derive track location from that run directly
    # at render time — no cfg caching or session state syncing needed.
    # If the run has a track name but lookup fails, show a warning and stop rather
    # than silently falling through to a completely different track's coordinates.
    _rdp_active_id          = st.session_state.get("active_run_id")
    _rdp_active_has_track   = False  # run has a track name we can try to resolve
    _rdp_active_lookup_ok   = False  # lookup_track() succeeded for that track
    if _rdp_active_id:
        _rdp_active_run   = load_run(_rdp_active_id)
        _rdp_active_slip  = (_rdp_active_run or {}).get("timeslip", {})
        _rdp_active_tname = (_rdp_active_slip.get("track_name") or "").strip()
        _rdp_active_tloc  = (_rdp_active_slip.get("track_location") or "").strip()
        if _rdp_active_tname or _rdp_active_tloc:
            _rdp_active_has_track = True
            _rdp_active_tk = lookup_track(_rdp_active_tname, _rdp_active_tloc)
            if _rdp_active_tk:
                _rdp_active_lookup_ok = True
                _rdp_lat       = _rdp_active_tk["lat"]
                _rdp_lon       = _rdp_active_tk["lon"]
                _rdp_elev_ft   = float(_rdp_active_tk.get("elev_ft") or 0)
                _rdp_loc_label = _rdp_active_tk["display_name"]

    # Track name known but unresolvable → warn and stop; never fall through to
    # a different track's coordinates from cfg.
    if _rdp_active_has_track and not _rdp_active_lookup_ok:
        _rdp_unresolved = (_rdp_active_tname if _rdp_active_id else None) or (_rdp_active_tloc if _rdp_active_id else None) or "this track"
        st.warning(
            f"⚠️ Track '{_rdp_unresolved}' could not be found automatically. "
            "Update the Track Location in the sidebar to get accurate weather for this track."
        )
        st.stop()

    # cfg override and recent-runs fallback only run when the active-run block
    # did NOT resolve coordinates — prevents saved cfg from clobbering the
    # active run's track after the active-run block sets all four variables.
    _rdp_ov = None  # initialised here so line 3399 can reference it unconditionally
    if not _rdp_active_lookup_ok:
        # Override: if stored location name matches a hardcoded override, use those
        # exact coordinates — bypasses any wrong lat/lon previously saved to cfg.
        _rdp_loc_name_raw = _rdp_cfg.get("location_name", "") or _rdp_cfg.get("location_label", "")
        _rdp_ov = _TRACK_OVERRIDES.get(_track_key(_rdp_loc_name_raw))
        if _rdp_ov:
            print(f"[RDP] Override applied for '{_rdp_loc_name_raw}' → "
                  f"lat={_rdp_ov['lat']}, lon={_rdp_ov['lon']}, elev_ft={_rdp_ov.get('elev_ft')}")
            _rdp_lat     = _rdp_ov["lat"]
            _rdp_lon     = _rdp_ov["lon"]
            _rdp_elev_ft = float(_rdp_ov.get("elev_ft") or 0)

        if not _rdp_lat or not _rdp_lon:
            # Fall back to most recent run's track via lookup_track()
            if _sb:
                try:
                    _rdp_recent = (_sb.table("runs")
                                   .select("run_data")
                                   .eq("username", current_user)
                                   .order("created_at", desc=True)
                                   .limit(20)
                                   .execute().data or [])
                    for _rdp_rr in _rdp_recent:
                        _rdp_rr_slip = (_rdp_rr.get("run_data") or {}).get("timeslip") or {}
                        _rdp_rr_tname = _rdp_rr_slip.get("track_name", "")
                        _rdp_rr_tloc  = _rdp_rr_slip.get("track_location", "")
                        if _rdp_rr_tname or _rdp_rr_tloc:
                            _rdp_tk = lookup_track(_rdp_rr_tname, _rdp_rr_tloc)
                            if _rdp_tk:
                                _rdp_lat         = _rdp_tk["lat"]
                                _rdp_lon         = _rdp_tk["lon"]
                                _rdp_elev_ft     = float(_rdp_tk.get("elev_ft") or 0)
                                _rdp_fallback_label = _rdp_tk["display_name"]
                                break
                except Exception:
                    pass

    if not _rdp_lat or not _rdp_lon:
        st.warning("No track location set. Go to Track Location in the sidebar and save your location.")
    else:
        # Fetch and cache track elevation if not yet stored — needed for altimeter conversion
        if _rdp_elev_ft == 0 and cfg.get("elev_ft") is None:
            try:
                _ev_r = requests.get(
                    "https://api.open-meteo.com/v1/elevation",
                    params={"latitude": float(_rdp_lat), "longitude": float(_rdp_lon)},
                    timeout=5,
                )
                _ev_m = (_ev_r.json().get("elevation") or [None])[0]
                if _ev_m is not None:
                    _rdp_elev_ft = float(_ev_m) / 0.3048
                    cfg["elev_ft"] = _rdp_elev_ft
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
            # DA formula uses station pressure; convert altimeter setting → station if needed
            if _mwx_is_altim:
                _mwx_station_inhg = sea_level_to_station_pressure(_mwx_baro_raw, _rdp_elev_ft)
            else:
                _mwx_station_inhg = _mwx_baro_raw  # already station pressure
            _mwx_p_hpa = _mwx_station_inhg * 33.8639  # inHg → hPa
            _rdp_manual_da = calc_density_altitude(_mwx_temp_f, _mwx_p_hpa)
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
            _mwx_baro_type_sel = st.radio(
                "Pressure reading type",
                ["Uncorrected — station pressure (Kestrel / handheld barometer)",
                 "Corrected — altimeter setting (weather app / airport report)"],
                index=0,
                horizontal=True,
                key="rdp_mwx_baro_type",
            )
            _mwx_entry_is_altim = _mwx_baro_type_sel.startswith("Corrected")

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

            # Live DA preview (uses values currently in the inputs)
            if _mwx_baro > 0 and _mwx_rh > 0:
                if _mwx_entry_is_altim:
                    _preview_stn_inhg = sea_level_to_station_pressure(_mwx_baro, _rdp_elev_ft)
                else:
                    _preview_stn_inhg = _mwx_baro  # already station pressure
                _preview_da = calc_density_altitude(_mwx_temp, _preview_stn_inhg * 33.8639)
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
                    _rdp_uov = _rdp_r.get("predictor_exclude")   # None / True / False
                    if _rdp_uov is True:
                        _rdp_excluded.append({**_rdp_r, "status": "excluded — manual override"})
                    elif _rdp_uov is False:
                        _rdp_included.append({**_rdp_r, "status": "included (manual override)"})
                    elif _rdp_r["et"] < _rdp_lo or _rdp_r["et"] > _rdp_hi:
                        _rdp_excluded.append({**_rdp_r, "status": "excluded — outlier (IQR)"})
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
                    "Non-Full Pass runs and IQR outliers are excluded automatically."
                )
                _rdp_display = []
                for _rdp_r in _rdp_included:
                    _rdp_display.append({**_rdp_r, "status": "included"})
                for _rdp_r in _rdp_excluded:
                    _rdp_display.append(_rdp_r)
                _rdp_display.sort(key=lambda x: x["date"], reverse=True)

                _rdp_col_w = [1, 2, 3, 2, 2, 3]

                # Header row
                _rdp_hdr = st.columns(_rdp_col_w)
                for _hcol, _hlbl in zip(
                    _rdp_hdr,
                    ["Include", "Date", "Track", "ET", "DA ft", "Status"],
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

                _rdp_changes = {}   # {run_id: new_include_bool} for any toggled rows
                for _rdp_row in _rdp_display:
                    _run_id = _rdp_row.get("run_id", "")
                    _is_inc = not _rdp_row["status"].startswith("excluded")
                    _status = _rdp_row["status"]

                    if _status == "included":
                        _status_html = "<span style='color:#4CAF50'>included</span>"
                    elif "manual override" in _status:
                        _status_html = "<span style='color:#888'>excluded — manual override</span>"
                    else:
                        _status_html = "<span style='color:#FFA500'>excluded — outlier (IQR)</span>"

                    _rdp_cols = st.columns(_rdp_col_w)
                    _new_inc = _rdp_cols[0].checkbox(
                        "", value=_is_inc,
                        key=f"run_include_{_run_id}",
                        label_visibility="collapsed",
                    )
                    _rdp_cols[1].write(_rdp_row["date"] or "—")
                    _rdp_cols[2].write(_rdp_row.get("track") or "—")
                    _rdp_cols[3].write(f"{_rdp_row['et']:.3f}")
                    _rdp_cols[4].write(f"{int(round(_rdp_row['da'])):,}")
                    _rdp_cols[5].markdown(_status_html, unsafe_allow_html=True)
                    st.markdown(
                        "<hr style='margin:2px 0;border-color:#222'>",
                        unsafe_allow_html=True,
                    )

                    if _new_inc != _is_inc:
                        _rdp_changes[_run_id] = _new_inc

                st.caption(
                    f"{_rdp_n_incl} included · {len(_rdp_excluded)} excluded · "
                    f"IQR fences {_rdp_lo:.3f}–{_rdp_hi:.3f}s · "
                    f"Q1 {_rdp_q1:.3f}s · Q3 {_rdp_q3:.3f}s"
                )

                # Persist overrides to Supabase when a checkbox is toggled
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
                    st.rerun()

    st.stop()  # Don't render the dashboard when on predictor page
