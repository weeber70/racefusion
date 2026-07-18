"""
styles.py — RaceFusion CSS injection and color constants.

All st.markdown(<style>…</style>) calls live here.
app.py calls apply_all_styles() once after authentication.
"""

import streamlit as st

# ── Color constants ───────────────────────────────────────────────────────────
PRIMARY_RED        = "#cc1111"
PRIMARY_RED_BRIGHT = "#ee2222"
PRIMARY_RED_DARK   = "#8b0000"
PRIMARY_RED_DEEP   = "#5a0000"
PRIMARY_RED_DIM    = "#2a0000"

BG_MAIN    = "#08080d"
BG_SIDEBAR = "#0d0d14"
BG_CARD    = "#0f0f18"
BG_INPUT   = "#141420"
BG_HOVER   = "#2a1a1a"
BG_POPUP   = "#111111"

TEXT_BRIGHT  = "#ffffff"
TEXT_PRIMARY = "#e8e8e8"
TEXT_DIM     = "#999"
TEXT_MUTED   = "#888"

BORDER_DARK = "#2a1a1a"
BORDER_MID  = "#2a2a3a"
BORDER_RED  = "#3a2a2a"

# ── Plotly dark theme — applied to every chart fig.update_layout() ────────────
PLOTLY_DARK = {
    "template":      "plotly_dark",
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor":  "rgba(0,0,0,0)",
}


# ── Main dark theme ────────────────────────────────────────────────────────────
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
/* ── Alerts / Info ── */
[data-testid="stAlert"] {
    background-color: #2a0000 !important;
    border-left: 4px solid #ff2222 !important;
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
        st.markdown(css, unsafe_allow_html=True)


def apply_login_styles():
    """Minimal dark theme for the login/register page (before full auth)."""
    st.markdown("""<style>
.stApp,[data-testid="stAppViewContainer"]{background:#08080d!important}
.stApp *{color:#e8e8e8!important}
[data-testid="baseButton-primary"]{background:#cc1111!important;color:#fff!important;font-weight:700!important;border:none!important}
[data-testid="baseButton-secondary"]{background:#1a1a24!important;color:#e8e8e8!important;border:1px solid #3a2a2a!important}
[data-testid="stAlert"]{background:#2a0000!important;border-left:4px solid #ff2222!important}
[data-testid="stTabs"] [role="tab"]{color:#e8e8e8!important}
[data-testid="stTabs"] [role="tab"][aria-selected="true"]{color:#cc1111!important}
</style>""", unsafe_allow_html=True)


def apply_maintenance_styles():
    """CSS to hide sidebar/header during the maintenance-mode full-screen block."""
    st.markdown("""
<style>
[data-testid="stSidebar"]{display:none!important}
header,[data-testid="stHeader"]{display:none!important}
.block-container{padding:0!important;max-width:100%!important}
</style>""", unsafe_allow_html=True)


def apply_all_styles():
    """Call once after authentication to apply the full dark theme."""
    _inject_theme(True)
