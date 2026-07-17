"""
charts.py — RaceFusion Plotly chart builders.

Every chart function returns a go.Figure with PLOTLY_DARK applied,
ready for st.plotly_chart().

Currently extracted:
  make_overlay_chart() — RacePak DataLink-style multi-channel overlay
"""

import pandas as pd
import plotly.graph_objects as go

from styles import PLOTLY_DARK

# ── Channel constants (used in make_overlay_chart) ────────────────────────────
# Channel names that carry actual RPM values — used for global RacePak scale
RPM_CHANNEL_NAMES = {"Engine RPM", "DS RPM", "MSD Engine RPM", "MSD RevLim RPM"}

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


def make_overlay_chart(channels, title, time_col, df_view, t_range, mode, height,
                       dark=True, global_rpm_max=8000.0, y_min=-10.0, y_max=10000.0,
                       smooth_points=1):
    """RacePak DataLink-style overlay chart with a shared global RPM scale.

    All charts share the same y-axis (y_min / y_max from the RPM Range slider) so
    every group is directly comparable regardless of what channels it contains.
    - RPM channels: plotted at actual RPM values.
    - All other channels: min→max scaled into 0→global_rpm_max; zero-anchored when
      the channel crosses zero so negative raw values appear below y=0.
    - Dashed reference line at global_rpm_max (actual run peak, floats below y_max).
    - Solid zero line drawn whenever y_min < 0.
    - Hover always shows the channel's actual value.
    """
    valid = [ch for ch in channels if not df_view[ch].dropna().empty]
    if not valid:
        return None

    fig = go.Figure()
    _all_scaled_min = 0.0   # tracks the lowest scaled RPM-space value in this group

    for i, ch in enumerate(valid):
        _c  = TRACE_COLORS[i % len(TRACE_COLORS)]
        _raw_col = df_view[ch]
        # Smooth raw values before scaling so rolling avg operates in real units
        if smooth_points > 1:
            raw = _raw_col.rolling(window=smooth_points, center=True, min_periods=1).mean()
        else:
            raw = _raw_col
        # Ratio channels (Conv % Slip, Engine/DS Ratio) involve division by DS RPM;
        # mask out pre-launch points where DS RPM is near zero to prevent distortion.
        if ch in ("Conv % Slip", "Engine/DS Ratio") and "DS RPM" in df_view.columns:
            raw = raw.where(df_view["DS RPM"] >= 100, other=float("nan"))
        if ch in RPM_CHANNEL_NAMES:
            y_plot = raw
        else:
            _cmin = float(raw.min())
            _cmax = float(raw.max())
            _crng = _cmax - _cmin
            if _crng == 0:
                y_plot = pd.Series(
                    [0.0 if _cmin == 0.0 else global_rpm_max / 2] * len(raw),
                    index=raw.index,
                )
            elif _cmin < 0.0 and _cmax > 0.0:
                # Zero-anchored: raw=0 → y=0, positive peak → global_rpm_max
                y_plot = raw * (global_rpm_max / _cmax)
            else:
                # All-positive (or all-negative): min→0, max→global_rpm_max
                y_plot = (raw - _cmin) / _crng * global_rpm_max
        # Track the actual scaled minimum for this group
        _ch_scaled_min = float(y_plot.min())
        if _ch_scaled_min < _all_scaled_min:
            _all_scaled_min = _ch_scaled_min
        fig.add_trace(go.Scatter(
            x=df_view[time_col], y=y_plot,
            customdata=raw,
            mode=mode, name=ch,
            legendgroup=ch, showlegend=False,
            line=dict(width=1.5, color=_c),
            marker=dict(size=3),
            hovertemplate=f"<b>{ch}</b>: %{{customdata:.4g}}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", name=ch,
            legendgroup=ch, showlegend=True,
            marker=dict(symbol="square", size=10, color=_c),
        ))

    # Dynamic floor: 15% below the actual scaled minimum if any channel goes
    # negative in RPM space; otherwise a modest 5% gap below zero.
    if _all_scaled_min < 0:
        y_min = _all_scaled_min * 1.15
    else:
        y_min = -(y_max * 0.05)

    # Ceiling line at y_max
    fig.add_hline(
        y=y_max,
        line_dash="solid", line_color="rgba(255,255,255,0.5)", line_width=1.5,
    )
    # Zero reference line — always drawn, clearly visible as the baseline
    fig.add_hline(
        y=0,
        line_dash="solid", line_color="rgba(255,255,255,0.35)", line_width=1,
    )

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
        xaxis=dict(title=f"{time_col} (s)", range=[t_range[0], t_range[1]]),
        yaxis=dict(title="RPM", range=[y_min, y_max], autorange=False, rangemode="normal", tickformat=",d"),
        hovermode="x unified",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.12, xanchor="left", x=0,
            font=dict(size=11, color="#e8e8e8"),
            bgcolor="rgba(0,0,0,0.6)",
            bordercolor="rgba(255,255,255,0.1)",
            borderwidth=1,
        ),
    )
    return fig
