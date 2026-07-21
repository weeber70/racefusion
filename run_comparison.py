"""
run_comparison.py — RaceFusion Run Comparison page.

Shows 2–3 selected runs side-by-side across Identity, Timeslip,
Weather, Run Details, Channel Peaks, and overlaid CSV charts.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from database import (
    load_run, load_run_csv_bytes, get_user_cars, load_channel_ranges, _get_secret,
)
from run_analysis import load_racepak_csv, get_time_col
from weather import calc_density_altitude
from config import load_config
from charts import make_overlay_chart

# ── Channel groups (mirrors CHANNEL_GROUPS in app.py) ────────────────────────
_CMP_GROUPS = {
    "🔥 Engine": [
        "Engine RPM", "DS RPM", "MSD Engine RPM", "Conv % Slip",
        "Engine/DS Ratio", "MSD Engine Timing", "MSD RevLim RPM",
    ],
    "⚡ Performance": [
        "Accel G", "Lateral G", "G-Meter MPH", "G-Meter Distance",
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
    ],
}

# Default Y-axis primary channel per group (mirrors Run Analysis)
_CMP_PRIMARY_DEFAULTS = {
    "🔥 Engine":        "Engine RPM",
    "⚡ Performance":   "Accel G",
    "🌡️ Temperatures": "Trans Temp",
}

DASH_STYLES = ["solid", "dash", "dot"]

# Prefix shown in legendgrouptitle for each dash style
_LEGEND_GROUP_PREFIX = {
    "solid": "─── ",
    "dash":  "╌╌╌ ",
    "dot":   "·⋯· ",
}

# ── Colour constants ──────────────────────────────────────────────────────────
_C_BEST  = "#00CC66"
_C_WORST = "#EF553B"
_C_DIFF  = "#FFA500"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(val, fmt="", fallback="—"):
    """Format a numeric value, returning fallback for None / empty / zero."""
    try:
        if val is None or val == "" or val == 0 or val == 0.0:
            return fallback
        return fmt.format(float(val)) if fmt else str(val)
    except Exception:
        return str(val) if val else fallback



def _best_worst_colors(values, lower_better: bool = True):
    """Per-value color: best → green, worst → red, ties/non-numeric → None."""
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            nums.append(None)
    valid = [n for n in nums if n is not None]
    if len(valid) < 2:
        return [None] * len(values)
    best  = min(valid) if lower_better else max(valid)
    worst = max(valid) if lower_better else min(valid)
    colors = []
    for n in nums:
        if n is None:
            colors.append(None)
        elif n == best and n != worst:
            colors.append(_C_BEST)
        elif n == worst and n != best:
            colors.append(_C_WORST)
        else:
            colors.append(None)
    return colors


def _differ_colors(values):
    """Amber for all values if they differ across runs, else None."""
    strs = [str(v) for v in values]
    if len(set(strs)) > 1:
        return [_C_DIFF] * len(values)
    return [None] * len(values)


def _cmp_table(rows, run_labels):
    """
    Render a comparison table as HTML.

    rows: list of (row_label, [val_str, ...], [color_or_None, ...])
    """
    _th = ("padding:6px 10px;text-align:center;color:#888;font-size:0.82rem;"
           "border-bottom:2px solid #333;white-space:nowrap;")
    _td_lbl = ("padding:5px 10px;color:#aaa;font-size:0.82rem;"
               "border-bottom:1px solid #1a1a1a;white-space:nowrap;")
    _td_val = ("padding:5px 10px;text-align:center;font-size:0.88rem;"
               "border-bottom:1px solid #1a1a1a;")

    html = ['<table style="width:100%;border-collapse:collapse;margin-bottom:1.2rem;">']
    html.append('<thead><tr>')
    html.append(f'<th style="{_th}text-align:left;"></th>')
    for lbl in run_labels:
        html.append(f'<th style="{_th}">{lbl}</th>')
    html.append('</tr></thead><tbody>')

    for row_label, values, colors in rows:
        html.append('<tr>')
        html.append(f'<td style="{_td_lbl}">{row_label}</td>')
        for val, color in zip(values, colors):
            extra = f"color:{color};font-weight:600;" if color else "color:#e0e0e0;"
            html.append(f'<td style="{_td_val}{extra}">{val}</td>')
        html.append('</tr>')

    html.append('</tbody></table>')
    return "".join(html)


def _make_comparison_chart(run_dfs, group_channels, group_title, primary_ch=None,
                           height=380, smooth_points=1, x_max=None, custom_ranges=None):
    """
    Multi-run overlay chart, built by REUSING charts.make_overlay_chart.

    One make_overlay_chart figure is built per run (identical to Run Analysis:
    same normalization, colors, ranges, ratio-channel zeroing), then run 2+'s
    traces are re-styled with a dash pattern and merged into run 1's figure.
    Any future fix to make_overlay_chart automatically applies here.

    run_dfs:       list of (short_label, df, time_col)
    primary_ch:    Y-axis channel (defaults to first group channel present)
    smooth_points: rolling-average window (1 = no smoothing)
    x_max:         clamp charts to the run window (max ET + margin); None = full data
    custom_ranges: user-defined channel scales (same dict Run Analysis passes)
    """
    merged = None

    for run_idx, (run_label, df, time_col) in enumerate(run_dfs):
        if df is None:
            continue
        # Clamp to the run window — identical filter shape to Run Analysis:
        # df_view = df[(df[time_col] >= t_range[0]) & (df[time_col] <= t_range[1])]
        t_lo = float(df[time_col].min())
        t_hi = float(x_max) if x_max is not None else float(df[time_col].max())
        df_view = df[(df[time_col] >= t_lo) & (df[time_col] <= t_hi)]
        if df_view.empty:
            continue

        _pri = primary_ch or group_channels[0]
        fig_run = make_overlay_chart(
            group_channels, _pri, group_title, time_col, df_view,
            (t_lo, t_hi), "lines", height,
            dark=True, smooth_points=smooth_points, custom_ranges=custom_ranges,
        )
        if fig_run is None:
            continue

        dash = DASH_STYLES[run_idx % len(DASH_STYLES)]

        if merged is None:
            # Adopt run 1's full layout (axis titles, ranges, theme) then
            # drop the Plotly legend — the HTML legend replaces it.
            merged = go.Figure(layout=fig_run.layout)
            merged.update_layout(
                showlegend=False,
                height=height + 80,
                margin=dict(t=40, b=40, l=60, r=20),
            )

        for trace in fig_run.data:
            # Skip the invisible legend-proxy swatch traces
            if "lines" not in (trace.mode or ""):
                continue
            ch = trace.name
            trace.name = f"{ch} ({run_label})"
            trace.showlegend = False
            trace.line.dash = dash
            if trace.hovertemplate:
                trace.hovertemplate = trace.hovertemplate.replace(
                    "</b>", f"</b> ({run_label})", 1
                )
            merged.add_trace(trace)

    if merged is None or not merged.data:
        return None
    return merged


def _extract_trace_colors(fig):
    """Map channel name → line color, extracted directly from the figure's traces.

    Trace names are '{channel} ({run_label})' — strip the run suffix so the map
    is keyed by channel. Colors come straight from trace.line.color, guaranteeing
    the legend always matches what's actually plotted.
    """
    trace_colors = {}
    for trace in fig.data:
        name = trace.name or ""
        ch_name = name.rsplit(" (", 1)[0] if " (" in name else name
        color = trace.line.color if trace.line and trace.line.color else "#ffffff"
        if ch_name not in trace_colors:
            trace_colors[ch_name] = color
    return trace_colors


def _cmp_legend_html(run_dfs, valid_channels, color_map):
    """Custom HTML legend for comparison charts — one row per run, channels inline.

    color_map: channel → color, extracted from the Plotly figure's traces.
    """
    _DASH_LABEL = {"solid": "───", "dash": "╌╌╌", "dot": "·⋯·"}
    rows = []
    for run_idx, (run_label, df, _) in enumerate(run_dfs):
        if df is None:
            continue
        dash = DASH_STYLES[run_idx % len(DASH_STYLES)]
        sym  = _DASH_LABEL.get(dash, "───")

        items = "".join(
            f'<span style="color:{color_map.get(ch, "#aaa")} !important;">■</span>'
            f' {ch} &nbsp; '
            for ch in valid_channels
            if ch in df.columns and not df[ch].dropna().empty
        )
        rows.append(
            f'<div style="font-size:12px; margin-bottom:3px; font-family:monospace;">'
            f'<span style="color:white;">{sym} {run_label} &nbsp;</span>'
            f'{items}</div>'
        )

    return (
        '<div style="background:rgba(0,0,0,0.45);border:1px solid rgba(255,255,255,0.1);'
        'border-radius:4px;padding:6px 10px;margin-bottom:6px;">'
        + "".join(rows)
        + "</div>"
    )


# ── Main page ─────────────────────────────────────────────────────────────────

def show_run_comparison(username: str, logo_src: "str | None" = None):
    """Render the Run Comparison page."""
    if logo_src:
        st.markdown(
            f'<img src="{logo_src}" style="max-width:520px;width:60%;'
            f'margin:0 auto 4px auto;display:block;">',
            unsafe_allow_html=True,
        )

    _cmp_title_col, _cmp_back_col = st.columns([4, 1], vertical_alignment="center")
    with _cmp_title_col:
        st.markdown("## ⚖️ Run Comparison")
    with _cmp_back_col:
        if st.button("← Back to Run Manager", key="cmp_back_btn", use_container_width=True):
            # Uncheck every run that was being compared
            for _fid in st.session_state.get("compare_run_ids", []):
                st.session_state.pop(f"rm_chk_{_fid}", None)
            st.session_state["compare_run_ids"] = []
            st.session_state["compare_run_ids_pending"] = []
            st.session_state["rm_selected"] = set()
            st.session_state["current_page"] = "run_manager"
            st.query_params["p"] = "run_manager"
            st.rerun()
    st.markdown("---")

    run_ids = st.session_state.get("compare_run_ids", [])
    if not run_ids or len(run_ids) < 2:
        st.warning("Select 2–3 runs in Run Manager to compare.")
        return

    # ── Load run records ──────────────────────────────────────────────────────
    _records = []
    for rid in run_ids:
        rec = load_run(rid)
        _records.append((rid, rec))

    n = len(_records)

    # ── Car names ─────────────────────────────────────────────────────────────
    _all_cars = {c["car_id"]: c["car_name"] for c in get_user_cars(username)}

    def _car_name(rec):
        return _all_cars.get(rec.get("car_id"), "")

    # ── Column labels (short: date · ET) ─────────────────────────────────────
    def _col_label(rid, rec):
        slip = rec.get("timeslip", {})
        parts = []
        if slip.get("date"):
            parts.append(slip["date"])
        et = slip.get("ft_1320")
        if et:
            try:
                parts.append(f"{float(et):.3f}s")
            except Exception:
                pass
        return " · ".join(parts) or rid

    _col_labels = [_col_label(rid, rec) for rid, rec in _records]

    # ── Load CSV data for charts ───────────────────────────────────────────────
    _run_dfs = []  # list of (short_label, df, time_col) — None df means no CSV
    for (rid, rec), lbl in zip(_records, _col_labels):
        csv_bytes = load_run_csv_bytes(rid)
        if csv_bytes:
            try:
                df = load_racepak_csv(csv_bytes)
                tc = get_time_col(df)
                _run_dfs.append((lbl, df, tc))
            except Exception:
                _run_dfs.append((lbl, None, None))
        else:
            _run_dfs.append((lbl, None, None))

    _has_csv = any(df is not None for _, df, _ in _run_dfs)

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 0 — AI RUN COMPARISON (top of page)
    # ═══════════════════════════════════════════════════════════════════════════
    st.subheader("🤖 AI Run Comparison")

    _cmp_ai_system = (
        "You are an expert drag racing crew chief and data analyst. You are given "
        "two runs from the same car to compare: timeslip splits, weather, tuning "
        "setup (run details), and data-logger channel peaks.\n\n"
        "Analyze the comparison and cover, in this order:\n"
        "1. **Key differences** — the most significant differences between the two "
        "runs across timeslip, weather, setup, and channel data.\n"
        "2. **What caused the ET/MPH difference** — explain the most likely causes. "
        "An ET gain without a corresponding MPH gain means the improvement came from "
        "the launch, not more power. Use the 60ft and incremental splits to locate "
        "where time was gained or lost.\n"
        "3. **Tuning changes** — flag every setup change between the runs (jets, "
        "pulleys, overdrive, tire pressure, launch RPM, shift point, spark plug) and "
        "state whether each likely helped, hurt, or was neutral, citing the data.\n"
        "4. **Recommendations** — give 2-3 specific, actionable recommendations for "
        "the next run based on this comparison.\n\n"
        "Be concise and concrete. Cite actual numbers from the data. Account for "
        "weather/DA differences before crediting or blaming tuning changes."
    )

    def _build_cmp_ai_payload(records, run_dfs, col_labels):
        """Assemble both runs' data into a text payload for the AI prompt."""
        _payload_peaks = [
            ("Peak Engine RPM",  "Engine RPM",  False),
            ("Peak Boost (psi)", "Boost Press", False),
            ("Peak Fuel PSI",    "Fuel Press",  False),
            ("Min Oil PSI",      "Oil Press",   True),
            ("Peak Trans Temp",  "Trans Temp",  False),
        ]
        sections = []
        for ((rid, rec), (_, df, _tc), lbl) in zip(records, run_dfs, col_labels):
            slip = rec.get("timeslip", {})
            wx   = rec.get("weather", {})
            rd   = rec.get("run_details", {})
            da   = calc_density_altitude(
                wx.get("temperature_f"), wx.get("pressure_hpa"), wx.get("humidity_pct")
            )
            hpa  = wx.get("pressure_hpa")
            baro = hpa * 0.02953 if hpa else None

            lines = [f"=== RUN: {lbl} ==="]
            lines.append("Timeslip:")
            for _k, _lab in [
                ("reaction_time", "Reaction"), ("ft_60", "60ft"),
                ("ft_330", "330ft"), ("ft_660", "660ft"), ("mph_660", "660 MPH"),
                ("ft_1000", "1000ft"), ("ft_1320", "ET"), ("mph_1320", "Trap MPH"),
            ]:
                lines.append(f"  {_lab}: {slip.get(_k) if slip.get(_k) not in (None, '') else 'n/a'}")
            lines.append("Weather:")
            lines.append(f"  Temp (F): {wx.get('temperature_f', 'n/a')}")
            lines.append(f"  Humidity (%): {wx.get('humidity_pct', 'n/a')}")
            lines.append(f"  Baro (inHg): {f'{baro:.2f}' if baro else 'n/a'}")
            lines.append(f"  Density Alt (ft): {f'{da:,.0f}' if da is not None else 'n/a'}")
            lines.append("Run details / tuning setup:")
            for _k, _lab in [
                ("tire_pressure_fl", "Tire PSI FL"), ("tire_pressure_fr", "Tire PSI FR"),
                ("tire_pressure_rl", "Tire PSI RL"), ("tire_pressure_rr", "Tire PSI RR"),
                ("launch_rpm", "Launch RPM"), ("shift_point", "Shift Point"),
                ("main_jet", "Main Jet"), ("top_pulley", "Top Pulley"),
                ("bottom_pulley", "Bottom Pulley"), ("overdrive", "Overdrive"),
                ("spark_plug", "Spark Plug"),
            ]:
                _v = rd.get(_k)
                lines.append(f"  {_lab}: {_v if _v not in (None, '') else 'n/a'}")
            lines.append("Channel peaks (from data logger):")
            if df is not None:
                for _lab, _ch, _use_min in _payload_peaks:
                    if _ch in df.columns and not df[_ch].dropna().empty:
                        _s = df[_ch].dropna()
                        _v = float(_s.min() if _use_min else _s.max())
                        lines.append(f"  {_lab}: {_v:.1f}")
                    else:
                        lines.append(f"  {_lab}: n/a")
            else:
                lines.append("  (no data logger CSV for this run)")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    api_key = _get_secret("ANTHROPIC_API_KEY")
    _cmp_ai_cache_key = "cmp_ai_" + "_".join(rid for rid, _ in _records)

    if st.button("🤖 Analyze Runs", key="cmp_ai_analyze_btn", type="primary"):
        if not api_key:
            st.warning("⚠️ Add your Anthropic API key in the sidebar to use AI analysis.")
        else:
            with st.spinner("🤖 Analyzing with Claude — comparing runs…"):
                try:
                    import anthropic as _anthropic
                    _client = _anthropic.Anthropic(api_key=api_key)
                    _payload = _build_cmp_ai_payload(_records, _run_dfs, _col_labels)
                    _msg = _client.messages.create(
                        model="claude-opus-4-8",
                        max_tokens=8192,
                        system=_cmp_ai_system,
                        messages=[{
                            "role": "user",
                            "content": f"Here are the two runs to compare:\n\n{_payload}",
                        }],
                    )
                    st.session_state[_cmp_ai_cache_key] = _msg.content[0].text
                except Exception as _e:
                    st.error(f"AI analysis failed: {_e}")

    if st.session_state.get(_cmp_ai_cache_key):
        with st.container(border=True):
            st.markdown(st.session_state[_cmp_ai_cache_key])

    st.markdown("---")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — RUN IDENTITY
    # ═══════════════════════════════════════════════════════════════════════════
    st.subheader("Run Identity")
    _id_cols = st.columns(n)
    for col, (rid, rec), lbl in zip(_id_cols, _records, _col_labels):
        slip = rec.get("timeslip", {})
        car  = _car_name(rec)
        with col:
            if car:
                st.markdown(f"**{car}**")
            st.caption(lbl)
            for _k, _v in [
                ("Date",   slip.get("date", "—")),
                ("Time",   slip.get("time", "—")),
                ("Track",  (slip.get("track_name") or slip.get("track_location") or "—")),
                ("Round",  slip.get("round_number") or "—"),
                ("Lane",   (slip.get("lane") or "—").capitalize()),
                ("Result", slip.get("result") or "—"),
            ]:
                st.markdown(f"<span style='color:#888;font-size:0.82rem;'>{_k}:</span> "
                            f"**{_v}**", unsafe_allow_html=True)

    st.markdown("---")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — TIMESLIP
    # ═══════════════════════════════════════════════════════════════════════════
    st.subheader("Timeslip")

    def _slip_rows(records):
        rows = []
        slips = [rec.get("timeslip", {}) for _, rec in records]

        def _row(label, key, fmt, lower_better=True):
            vals_raw = [s.get(key) for s in slips]
            vals_str = [_fmt(v, fmt) for v in vals_raw]
            nums     = []
            for v in vals_raw:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    nums.append(None)
            colors = _best_worst_colors(nums, lower_better=lower_better)
            return (label, vals_str, colors)

        # DA: always recompute from weather fields including humidity (matches run_analysis display)
        def _da_values():
            vals_raw = []
            for _, rec in records:
                wx = rec.get("weather", {})
                da = calc_density_altitude(
                    wx.get("temperature_f"),
                    wx.get("pressure_hpa"),
                    wx.get("humidity_pct"),
                )
                vals_raw.append(da)
            vals_str = [_fmt(v, "{:,.0f} ft") for v in vals_raw]
            nums = []
            for v in vals_raw:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    nums.append(None)
            return (vals_str, _best_worst_colors(nums, lower_better=True))

        rows.append(_row("Reaction",    "reaction_time", "{:.3f}", lower_better=True))
        rows.append(_row("60'",         "ft_60",         "{:.3f}", lower_better=True))
        rows.append(_row("330'",        "ft_330",        "{:.3f}", lower_better=True))
        rows.append(_row("660'",        "ft_660",        "{:.3f}", lower_better=True))
        rows.append(_row("660 MPH",     "mph_660",       "{:.2f}", lower_better=False))
        rows.append(_row("1000'",       "ft_1000",       "{:.3f}", lower_better=True))
        rows.append(_row("ET",          "ft_1320",       "{:.3f}", lower_better=True))
        rows.append(_row("Trap MPH",    "mph_1320",      "{:.2f}", lower_better=False))
        _da_str, _da_col = _da_values()
        rows.append(("DA", _da_str, _da_col))
        return rows

    st.markdown(
        _cmp_table(_slip_rows(_records), _col_labels),
        unsafe_allow_html=True,
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — WEATHER
    # ═══════════════════════════════════════════════════════════════════════════
    st.subheader("Weather")

    def _wx_rows(records):
        rows = []
        def _row(label, getter, fmt, lower_better=True):
            vals_raw = [getter(rec) for _, rec in records]
            vals_str = [_fmt(v, fmt) for v in vals_raw]
            nums = []
            for v in vals_raw:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    nums.append(None)
            return (label, vals_str, _best_worst_colors(nums, lower_better=lower_better))

        def _temp(rec):
            # Strictly weather API data only — never timeslip/track_temp
            wx = rec.get("weather", {})
            return wx.get("temperature_f") or wx.get("temp_f")

        def _humid(rec):
            wx = rec.get("weather", {})
            return wx.get("humidity_pct")

        def _baro(rec):
            wx = rec.get("weather", {})
            hpa = wx.get("pressure_hpa")
            return hpa * 0.02953 if hpa else None

        def _da(rec):
            wx = rec.get("weather", {})
            return calc_density_altitude(
                wx.get("temperature_f"),
                wx.get("pressure_hpa"),
                wx.get("humidity_pct"),
            )

        rows.append(_row("Temp (°F)",       _temp,  "{:.1f}", lower_better=True))
        rows.append(_row("Humidity (%)",    _humid, "{:.0f}", lower_better=True))
        rows.append(_row("Baro (inHg)",     _baro,  "{:.2f}", lower_better=False))
        rows.append(_row("Density Alt (ft)", _da,   "{:,.0f}", lower_better=True))
        return rows

    st.markdown(
        _cmp_table(_wx_rows(_records), _col_labels),
        unsafe_allow_html=True,
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — RUN DETAILS
    # ═══════════════════════════════════════════════════════════════════════════
    st.subheader("Run Details")

    def _rd_rows(records):
        rows = []
        rds = [rec.get("run_details", {}) for _, rec in records]

        def _num_row(label, key, fmt="{:.1f}"):
            vals_raw = [rd.get(key) for rd in rds]
            vals_str = [_fmt(v, fmt) for v in vals_raw]
            return (label, vals_str, _differ_colors(vals_str))

        def _str_row(label, key):
            vals = [str(rd.get(key) or "—") for rd in rds]
            return (label, vals, _differ_colors(vals))

        for _lbl, _key, _fmt_s in [
            ("Tire PSI FL",     "tire_pressure_fl",  "{:.1f}"),
            ("Tire PSI FR",     "tire_pressure_fr",  "{:.1f}"),
            ("Tire PSI RL",     "tire_pressure_rl",  "{:.1f}"),
            ("Tire PSI RR",     "tire_pressure_rr",  "{:.1f}"),
            ("Launch RPM",      "launch_rpm",         "{:,.0f}"),
            ("Shift Point",     "shift_point",        "{:,.0f}"),
            ("Main Jet",        "main_jet",           "{:.3f}"),
            ("Top Pulley",      "top_pulley",         "{:.0f}"),
            ("Bottom Pulley",   "bottom_pulley",      "{:.0f}"),
            ("Overdrive %",     "overdrive",          "{:.1f}"),
            ("HS Jet",          "hs_jet",             "{:.3f}"),
            ("HS Open PSI",     "hs_open_psi",        "{:.0f}"),
            ("Valve Lash",      "valve_lash",         "{}"),
            ("Spark Plug",      "spark_plug",         "{}"),
            ("Track Temp (°F)", "track_temp_f",       "{:.0f}"),
            ("Tire Temp (°F)",  "tire_temp_f",        "{:.0f}"),
        ]:
            vals_raw = [rd.get(_key) for rd in rds]
            if _key == "overdrive":
                vals_str = [
                    f"{float(v) * 100:.1f}%" if v not in (None, "", 0, 0.0) else "—"
                    for v in vals_raw
                ]
            else:
                vals_str = [_fmt(v, _fmt_s) for v in vals_raw]
            # Only show rows where at least one run has data
            if all(v == "—" for v in vals_str):
                continue
            rows.append((_lbl, vals_str, _differ_colors(vals_str)))

        # Notes — always show if any run has them
        notes = [str(rd.get("notes") or "—") for rd in rds]
        if any(n != "—" for n in notes):
            rows.append(("Notes", notes, [None] * len(notes)))

        return rows

    _rd = _rd_rows(_records)
    if _rd:
        st.markdown(_cmp_table(_rd, _col_labels), unsafe_allow_html=True)
    else:
        st.caption("No run detail data recorded for these runs.")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 5 — CHANNEL PEAKS
    # ═══════════════════════════════════════════════════════════════════════════
    if _has_csv:
        st.subheader("Channel Peaks")

        _peak_defs = [
            ("Peak Engine RPM",   "Engine RPM",  False, "{:,.0f}"),
            ("Peak Boost (psi)",  "Boost Press", False, "{:.1f}"),
            ("Peak Accel G",      "Accel G",     False, "{:.3f}"),
            ("Peak Fuel PSI",     "Fuel Press",  False, "{:.1f}"),
            ("Peak Fuel Flow",    "Fuel Flow",   False, "{:.2f}"),
            ("Min Oil PSI",       "Oil Press",   True,  "{:.1f}"),
            ("Peak Trans Temp",   "Trans Temp",  False, "{:.0f}"),
        ]

        _peak_rows = []
        for label, ch, _min_is_best, fmt in _peak_defs:
            vals_raw = []
            for _, df, _ in _run_dfs:
                if df is not None and ch in df.columns:
                    s = df[ch].dropna()
                    if not s.empty:
                        vals_raw.append(s.min() if _min_is_best else s.max())
                    else:
                        vals_raw.append(None)
                else:
                    vals_raw.append(None)

            if all(v is None for v in vals_raw):
                continue

            vals_str = [_fmt(v, fmt) for v in vals_raw]
            colors   = _best_worst_colors(vals_raw, lower_better=_min_is_best)
            _peak_rows.append((label, vals_str, colors))

        if _peak_rows:
            st.markdown(_cmp_table(_peak_rows, _col_labels), unsafe_allow_html=True)
        else:
            st.caption("No channel data available.")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 6 — OVERLAID CHARTS
    # ═══════════════════════════════════════════════════════════════════════════
    if _has_csv:
        st.subheader("Overlaid Charts")

        # Load user's channel visibility + group-override prefs from Run Analysis
        _cfg      = load_config()
        _ch_prefs = _cfg.get("channel_prefs", {})
        # Hidden Channels multiselect from Run Analysis Graph Controls
        _hidden_set = set(_cfg.get("hidden_channels", []))

        # User-defined channel scales — same dict Run Analysis passes to
        # make_overlay_chart, so custom ranges apply identically here.
        _custom_ranges = load_channel_ranges(username)

        def _ch_in_group(ch: str, grp: str) -> bool:
            """True if channel belongs to grp (respects user group overrides)."""
            override = _ch_prefs.get(ch, {}).get("group")
            return (override or grp) == grp

        # Filter to runs that actually have CSV data
        _csv_dfs = [(lbl, df, tc) for lbl, df, tc in _run_dfs if df is not None]

        def _ch_default_show(ch: str) -> bool:
            """Run Analysis default: hide channels whose data is all exactly 0.

            With multiple runs, the channel stays visible if ANY run has
            non-zero data for it.
            """
            _seen = False
            for _, df, _ in _csv_dfs:
                if ch not in df.columns:
                    continue
                s = df[ch].dropna()
                if s.empty:
                    continue
                _seen = True
                if not (float(s.min()) == 0.0 and float(s.max()) == 0.0):
                    return True
            return not _seen  # no data anywhere → leave visible (chart skips it)

        def _ch_visible(ch: str) -> bool:
            """Mirrors Run Analysis: show pref (default = all-zero rule),
            then the Hidden Channels multiselect."""
            if ch in _hidden_set:
                return False
            return _ch_prefs.get(ch, {}).get("show", _ch_default_show(ch))

        # Run window: launch (0) to slowest run's ET + 0.5s margin.
        # Falls back to min(data max, 10s) — same default as Run Analysis.
        _ets = []
        for _, rec in _records:
            try:
                _et = float(rec.get("timeslip", {}).get("ft_1320") or 0)
                if _et > 0:
                    _ets.append(_et)
            except (TypeError, ValueError):
                pass
        if _ets:
            _cmp_x_max = max(_ets) + 0.5
        else:
            _data_max = max(
                (float(df[tc].max()) for _, df, tc in _csv_dfs), default=10.0
            )
            _cmp_x_max = min(_data_max, 10.0)

        for grp_name, grp_channels in _CMP_GROUPS.items():
            # Only include channels that are: visible, in this group, and present in
            # at least one run's CSV (non-empty).
            _grp_valid = [
                ch for ch in grp_channels
                if (
                    _ch_visible(ch)
                    and _ch_in_group(ch, grp_name)
                    and any(
                        ch in df.columns and not df[ch].dropna().empty
                        for _, df, _ in _csv_dfs
                    )
                )
            ]
            if not _grp_valid:
                continue

            st.markdown(f"### {grp_name}")

            # Read smooth value before rendering (key-before-widget pattern).
            # Default 1 — same as Run Analysis's per-chart smoothing sliders.
            _smooth_key = f"cmp_smooth_{grp_name}"
            _smooth_pts = st.session_state.get(_smooth_key, 1)

            # Y-axis primary channel — same defaults + read-before-render
            # pattern as Run Analysis.
            _grp_default = _CMP_PRIMARY_DEFAULTS.get(grp_name, _grp_valid[0])
            if _grp_default not in _grp_valid:
                _grp_default = _grp_valid[0]
            _pri_key = f"cmp_primary_{grp_name}"
            _primary_ch = st.session_state.get(_pri_key, _grp_default)
            if _primary_ch not in _grp_valid:
                _primary_ch = _grp_default

            # Build the figure first, then extract trace colors for the HTML
            # legend — guarantees legend colors match the plotted traces exactly.
            fig = _make_comparison_chart(
                _csv_dfs, _grp_valid, grp_name, primary_ch=_primary_ch,
                height=320, smooth_points=_smooth_pts, x_max=_cmp_x_max,
                custom_ranges=_custom_ranges,
            )
            if fig:
                _trace_colors = _extract_trace_colors(fig)
                _legend_html = _cmp_legend_html(_csv_dfs, _grp_valid, _trace_colors)
                # st.html renders raw HTML without markdown sanitization
                # interfering with inline styles; fall back for older Streamlit.
                if hasattr(st, "html"):
                    st.html(_legend_html)
                else:
                    st.markdown(_legend_html, unsafe_allow_html=True)
                st.plotly_chart(fig, use_container_width=True)

            # Y-axis selector + smoothing slider below chart (mirrors Run Analysis)
            st.selectbox(
                "Change Y-axis:", _grp_valid,
                index=_grp_valid.index(_primary_ch),
                key=_pri_key,
            )
            st.slider(
                "Smoothing window",
                min_value=1, max_value=25, value=1, step=1,
                key=_smooth_key,
                help="Rolling-average window in samples. 1 = no smoothing.",
            )
            st.markdown("---")
