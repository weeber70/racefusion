"""
charts.py — RaceFusion Plotly chart builders.

Every chart function returns a go.Figure with the dark theme applied,
ready for st.plotly_chart().

Exports:
  make_overlay_chart() — RacePak DataLink-style multi-channel overlay
  CHANNEL_COLORS       — designated color per channel name
  CHANNEL_UNITS        — unit label per channel name
  TRACE_COLORS         — fallback color rotation (for unlisted channels)
  RPM_CHANNEL_NAMES    — set of channel names that carry true RPM values
"""

import pandas as pd
import plotly.graph_objects as go

from styles import PLOTLY_DARK

# ── Channel names that carry actual RPM values ────────────────────────────────
# These are plotted at face value; every other channel is normalized into the
# same 0 → rpm_max range so the single Y-axis always reads in RPM.
RPM_CHANNEL_NAMES = {"Engine RPM", "DS RPM", "MSD Engine RPM", "MSD RevLim RPM"}

# ── Designated colors per channel (RacePak DataLink-inspired) ────────────────
CHANNEL_COLORS: dict[str, str] = {
    # ── RPM ──────────────────────────────────────────────────────────────────
    "Engine RPM":       "#FF3B3B",   # bright red
    "MSD Engine RPM":   "#FF6B35",   # red-orange
    "MSD RevLim RPM":   "#FF8C00",   # dark orange  (rev-limiter trace)
    "DS RPM":           "#00FF7F",   # spring green  (driveshaft)
    # ── Derived / ratio ───────────────────────────────────────────────────────
    "Engine/DS Ratio":  "#FFE600",   # yellow
    "Conv % Slip":      "#FFB347",   # amber
    # ── Induction / boost ────────────────────────────────────────────────────
    "Boost":            "#00E5FF",   # bright cyan
    "Manifold Pres":    "#00CFCF",   # teal-cyan
    "Throttle":         "#AEFF8A",   # lime green
    # ── Fuel ─────────────────────────────────────────────────────────────────
    "Fuel Pressure":    "#7B68EE",   # medium-slate-blue
    "Fuel Flow":        "#9B59B6",   # purple
    # ── Oil ──────────────────────────────────────────────────────────────────
    "Oil Pressure":     "#E056EF",   # magenta
    "Oil Temp":         "#EF53B0",   # hot pink
    # ── Coolant / water ───────────────────────────────────────────────────────
    "Water Temp":       "#54A3FF",   # cornflower blue
    "Coolant Temp":     "#4FC3F7",   # light blue
    # ── EGT / cylinders ──────────────────────────────────────────────────────
    "Avg. EGT":         "#FF8C00",   # dark orange
    "Cyl #1":           "#FF4500",   # orange-red
    "Cyl #2":           "#FF6347",   # tomato
    "Cyl #3":           "#FF7F50",   # coral
    "Cyl #4":           "#FFA07A",   # light salmon
    "Cyl #5":           "#FFB347",   # amber
    "Cyl #6":           "#FFD700",   # gold
    "Cyl #7":           "#FAFAD2",   # light goldenrod
    "Cyl #8":           "#FFF8DC",   # cornsilk
    # ── Misc ─────────────────────────────────────────────────────────────────
    "Battery":          "#BDBDBD",   # light grey
    "Ground Speed":     "#80CBC4",   # teal
    "Nitrous Pres":     "#B2FF59",   # bright lime
}

# ── Unit labels per channel ───────────────────────────────────────────────────
CHANNEL_UNITS: dict[str, str] = {
    "Engine RPM":       "RPM",
    "DS RPM":           "RPM",
    "MSD Engine RPM":   "RPM",
    "MSD RevLim RPM":   "RPM",
    "Engine/DS Ratio":  "",
    "Conv % Slip":      "%",
    "Boost":            "psi",
    "Manifold Pres":    "psi",
    "Throttle":         "%",
    "Fuel Pressure":    "psi",
    "Fuel Flow":        "lb/hr",
    "Oil Pressure":     "psi",
    "Oil Temp":         "°F",
    "Water Temp":       "°F",
    "Coolant Temp":     "°F",
    "Avg. EGT":         "°F",
    "Cyl #1":           "°F",
    "Cyl #2":           "°F",
    "Cyl #3":           "°F",
    "Cyl #4":           "°F",
    "Cyl #5":           "°F",
    "Cyl #6":           "°F",
    "Cyl #7":           "°F",
    "Cyl #8":           "°F",
    "Battery":          "V",
    "Ground Speed":     "mph",
    "Nitrous Pres":     "psi",
}

# ── Fallback rotation for channels with no designated color ───────────────────
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


def _ch_color(ch: str, position: int) -> str:
    """Designated color for ch; falls back to TRACE_COLORS rotation."""
    return CHANNEL_COLORS.get(ch) or TRACE_COLORS[position % len(TRACE_COLORS)]


def _ch_unit(ch: str) -> str:
    """Unit suffix for ch, or empty string."""
    return CHANNEL_UNITS.get(ch, "")


def make_overlay_chart(channels, primary_channel, title, time_col, df_view, t_range, mode, height,
                       dark=True, smooth_points=1):
    """RacePak DataLink-style overlay chart — primary-channel Y-axis.

    Every channel is normalized to its own 0–100% range, then mapped onto the
    primary channel's actual min→max scale.  Switching the primary channel changes
    only the Y-axis ruler; trace shapes are invariant.

    - Ratio channels (Engine/DS Ratio, Conv % Slip) are zeroed any time
      DS RPM is below 100, preventing division-by-zero blowout.
    - Hover tooltip always shows the channel's actual raw value with units.
    """
    valid = [ch for ch in channels if ch in df_view.columns and not df_view[ch].dropna().empty]
    if not valid:
        return None

    # Fall back gracefully if the requested primary channel is missing or empty
    if primary_channel not in df_view.columns or df_view[primary_channel].dropna().empty:
        primary_channel = valid[0]

    # ── Primary channel: defines the Y-axis ruler ────────────────────────────
    _pri_raw = df_view[primary_channel].copy()
    if smooth_points > 1:
        _pri_raw = _pri_raw.rolling(window=smooth_points, center=True, min_periods=1).mean()
    if primary_channel in ("Conv % Slip", "Engine/DS Ratio") and "DS RPM" in df_view.columns:
        _pri_raw = _pri_raw.where(df_view["DS RPM"] >= 100, other=0.0)
    primary_min = float(_pri_raw.min())
    primary_max = float(_pri_raw.max())
    if primary_max == primary_min:          # degenerate / flat channel
        primary_max = primary_min + 1.0

    _pri_unit = _ch_unit(primary_channel)
    _y_title  = f"{primary_channel} [{_pri_unit}]" if _pri_unit else primary_channel
    # Add a 5% margin above and below so traces don't hug the axis edges
    _margin   = (primary_max - primary_min) * 0.05
    _y_range  = [primary_min - _margin, primary_max + _margin]

    fig = go.Figure()

    for i, ch in enumerate(valid):
        _c    = _ch_color(ch, i)
        _unit = _ch_unit(ch)
        _raw_col = df_view[ch]

        # Smooth in real units before normalization
        if smooth_points > 1:
            raw = _raw_col.rolling(window=smooth_points, center=True, min_periods=1).mean()
        else:
            raw = _raw_col.copy()

        # Zero ratio/slip channels any time DS RPM < 100 — prevents ÷0 blowout
        if ch in ("Conv % Slip", "Engine/DS Ratio") and "DS RPM" in df_view.columns:
            raw = raw.where(df_view["DS RPM"] >= 100, other=0.0)

        # ── Normalize: channel → 0–1, then map to primary channel's scale ───
        ch_min = float(raw.min())
        ch_max = float(raw.max())
        if ch_max == ch_min:
            normalized = pd.Series(0.5, index=raw.index)
        else:
            normalized = (raw - ch_min) / (ch_max - ch_min)
        y_plot = primary_min + normalized * (primary_max - primary_min)

        # Hover: actual raw value + unit (never the normalized display value)
        _unit_sfx = f" {_unit}" if _unit else ""
        _hover    = f"<b>{ch}</b>: %{{customdata:.4g}}{_unit_sfx}<extra></extra>"

        fig.add_trace(go.Scatter(
            x=df_view[time_col], y=y_plot,
            customdata=raw,
            mode=mode, name=ch,
            legendgroup=ch, showlegend=False,
            line=dict(width=1.5, color=_c),
            marker=dict(size=3, color=_c),
            hovertemplate=_hover,
        ))
        # Invisible legend-proxy swatch (square marker, always visible in legend)
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", name=ch,
            legendgroup=ch, showlegend=True,
            marker=dict(symbol="square", size=10, color=_c),
        ))

    # Pre-launch shade
    if t_range[0] < 0:
        fig.add_vrect(
            x0=t_range[0], x1=min(0.0, t_range[1]),
            fillcolor="rgba(100,100,100,0.12)", layer="below", line_width=0,
            annotation_text="pre-launch", annotation_position="top left",
            annotation_font_size=10,
        )

    fig.update_layout(
        height=height + 120,
        margin=dict(t=120, b=40, l=60, r=20),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            title=f"{time_col} (s)",
            range=[t_range[0], t_range[1]],
            gridcolor="rgba(255,255,255,0.1)",
            color="white",
        ),
        yaxis=dict(
            title=_y_title,
            range=_y_range,
            autorange=False,
            rangemode="normal",
            tickformat=",",
            gridcolor="rgba(255,255,255,0.1)",
            color="white",
        ),
        hovermode="x unified",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.12, xanchor="left", x=0,
            font=dict(size=11, color="#e8e8e8"),
            bgcolor="rgba(0,0,0,0.6)",
            bordercolor="rgba(255,255,255,0.1)",
            borderwidth=1,
        ),
    )
    return fig
