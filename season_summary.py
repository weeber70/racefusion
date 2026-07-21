"""
season_summary.py — RaceFusion Season Summary page.
"""
import streamlit as st
from datetime import datetime
from database import get_effective_da


def show_season_summary(saved_runs: list, cfg: dict, logo_src: "str | None" = None):
    """Render the Season Summary page."""
    if logo_src:
        st.markdown(
            f'<img src="{logo_src}" style="max-width:520px;width:60%;'
            f'margin:0 auto 4px auto;display:block;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🏁 RaceFusion")
    st.markdown("# 📅 Season Summary")
    st.markdown(
        "<p style='color:#888;margin-top:-12px;'>Season stats and records pulled from your saved runs.</p>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ── Parse year from a timeslip date string in any common format ───────────
    def _ssm_parse_year(date_str):
        if not date_str:
            return None
        from datetime import datetime
        s = str(date_str).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).year
            except ValueError:
                pass
        try:
            y = int(s[:4])
            if 2000 <= y <= 2099:
                return y
        except Exception:
            pass
        return None

    # ── Build annotated run list from saved_runs (already loaded above) ──────
    _ssm_all = []
    for _sr in saved_runs:
        _rec  = _sr.get("record", {})
        _slip = _rec.get("timeslip") or {}
        _yr   = _ssm_parse_year(_slip.get("date"))
        if _yr:
            _ssm_all.append({"year": _yr, "rec": _rec, "slip": _slip})

    if not _ssm_all:
        st.info("No runs with dates found yet. Add timeslips to your runs to see season stats here.")
        st.stop()

    _ssm_years = sorted({r["year"] for r in _ssm_all}, reverse=True)
    _ssm_sel_year = st.selectbox("Season", _ssm_years, index=0, key="season_year_sel")
    _ssm_runs = [r for r in _ssm_all if r["year"] == _ssm_sel_year]

    if not _ssm_runs:
        st.info(f"No runs logged for {_ssm_sel_year}.")
        st.stop()

    # ── Aggregate stats ───────────────────────────────────────────────────────
    def _ssm_f(key, cast=float):
        vals = []
        for r in _ssm_runs:
            v = r["slip"].get(key)
            if v is not None:
                try:
                    vals.append(cast(v))
                except (ValueError, TypeError):
                    pass
        return vals

    _ssm_ets  = _ssm_f("ft_1320")   # ET is stored as ft_1320
    _ssm_mphs = _ssm_f("mph_1320")  # trap MPH is stored as mph_1320
    _ssm_60s  = _ssm_f("ft_60")
    _ssm_rts  = _ssm_f("reaction_time")

    _ssm_best_et  = min(_ssm_ets)  if _ssm_ets  else None
    _ssm_best_mph = max(_ssm_mphs) if _ssm_mphs else None
    _ssm_best_60  = min(_ssm_60s)  if _ssm_60s  else None
    _ssm_best_rt  = min(_ssm_rts)  if _ssm_rts  else None

    _ssm_wins   = sum(1 for r in _ssm_runs if r["rec"].get("run_details", {}).get("result") == "Win")
    _ssm_losses = sum(1 for r in _ssm_runs if r["rec"].get("run_details", {}).get("result") == "Loss")
    _ssm_byes   = sum(1 for r in _ssm_runs if r["rec"].get("run_details", {}).get("result") == "Bye")
    _ssm_decided = _ssm_wins + _ssm_losses
    _ssm_win_pct = (_ssm_wins / _ssm_decided * 100) if _ssm_decided > 0 else None

    _ssm_fmt = lambda v, fmt: fmt.format(v) if v is not None else "—"

    # ── Stats card helpers ────────────────────────────────────────────────────
    def _ssm_stat_row(label, value, color="#eee", bold=False):
        fw = "font-weight:700;" if bold else ""
        return (f'<tr>'
                f'<td style="color:#888;padding:4px 12px 4px 0;white-space:nowrap;">{label}</td>'
                f'<td style="color:{color};{fw}text-align:right;padding:4px 0;">{value}</td>'
                f'</tr>')

    # Always show W-L-Bye counts; dim to #444 when zero so the non-zero ones pop
    def _wl_num(n, color, label):
        c = color if n > 0 else "#444"
        return f"<span style='color:{c};font-weight:{'700' if n > 0 else '400'};'>{n}&nbsp;{label}</span>"

    def _ssm_agg(run_list):
        """Aggregate stats over an arbitrary list of annotated run dicts."""
        def _f(key):
            vals = []
            for r in run_list:
                v = r["slip"].get(key)
                if v is not None:
                    try:
                        vals.append(float(v))
                    except (ValueError, TypeError):
                        pass
            return vals
        ets  = _f("ft_1320")
        mphs = _f("mph_1320")
        s60s = _f("ft_60")
        rts  = _f("reaction_time")
        wins    = sum(1 for r in run_list if r["rec"].get("run_details", {}).get("result") == "Win")
        losses  = sum(1 for r in run_list if r["rec"].get("run_details", {}).get("result") == "Loss")
        byes    = sum(1 for r in run_list if r["rec"].get("run_details", {}).get("result") == "Bye")
        decided = wins + losses
        return dict(
            n        = len(run_list),
            best_et  = min(ets)  if ets  else None,
            best_mph = max(mphs) if mphs else None,
            best_60  = min(s60s) if s60s else None,
            best_rt  = min(rts)  if rts  else None,
            wins=wins, losses=losses, byes=byes, decided=decided,
            win_pct  = (wins / decided * 100) if decided > 0 else None,
        )

    def _ssm_build_card(st_obj, title):
        """Render a stats card into *st_obj* (either a column or st)."""
        wl_html = (
            _wl_num(st_obj["wins"],   "#2ecc71", "W") +
            " <span style='color:#333;'>&nbsp;·&nbsp;</span> " +
            _wl_num(st_obj["losses"], "#e74c3c", "L") +
            (" <span style='color:#333;'>&nbsp;·&nbsp;</span> " +
             _wl_num(st_obj["byes"], "#f0a500", "Bye") if st_obj["byes"] else "")
        )
        wp  = st_obj["win_pct"]
        dec = st_obj["decided"]
        win_pct_str = f"{wp:.1f}%" if wp is not None else ("—" if dec == 0 else "0.0%")
        rows = (
            _ssm_stat_row("Total Runs",     str(st_obj["n"]),                             color="#eee",    bold=True) +
            _ssm_stat_row("Best ET",        _ssm_fmt(st_obj["best_et"],  "{:.3f} s"),     color="#ffcc00", bold=True) +
            _ssm_stat_row("Best MPH",       _ssm_fmt(st_obj["best_mph"], "{:.2f} mph"),   color="#cc1111", bold=True) +
            (_ssm_stat_row("Best 60ft",     _ssm_fmt(st_obj["best_60"],  "{:.3f} s"),     color="#4db8ff") if st_obj["best_60"]  is not None else "") +
            (_ssm_stat_row("Best Reaction", _ssm_fmt(st_obj["best_rt"],  "{:.3f} s"),     color="#2ecc71") if st_obj["best_rt"]  is not None else "") +
            f'<tr><td style="color:#888;padding:4px 12px 4px 0;white-space:nowrap;">Record</td>'
            f'<td style="text-align:right;padding:4px 0;">{wl_html}</td></tr>' +
            f'<tr><td style="color:#888;padding:4px 12px 4px 0;white-space:nowrap;">Win %</td>'
            f'<td style="color:{"#2ecc71" if wp and wp >= 50 else "#eee"};'
            f'font-weight:{"700" if dec > 0 else "400"};text-align:right;padding:4px 0;">'
            f'{win_pct_str}</td></tr>'
        )
        return (f'<div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;'
                f'background:#0a0a0a;font-family:monospace;">'
                f'<div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:10px;'
                f'border-bottom:1px solid #2a0000;padding-bottom:6px;">{title}</div>'
                f'<table style="width:100%;border-collapse:collapse;font-size:0.92rem;">{rows}</table>'
                f'</div>')

    # ── Render Season Stats + All-Time Best side by side ─────────────────────
    _ssm_season_stats  = _ssm_agg(_ssm_runs)
    _ssm_alltime_stats = _ssm_agg(_ssm_all)

    _col_season, _col_alltime = st.columns(2)
    with _col_season:
        st.markdown(
            _ssm_build_card(_ssm_season_stats, f"🏆 {_ssm_sel_year} Season Stats"),
            unsafe_allow_html=True,
        )
    with _col_alltime:
        st.markdown(
            _ssm_build_card(_ssm_alltime_stats, "🏆 All-Time Best Stats"),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Chronological Run Log ─────────────────────────────────────────────────
    # Sort runs by date+time ascending so multiple same-day runs are ordered correctly
    def _ssm_sort_key(r):
        from datetime import datetime
        date_s = str(r["slip"].get("date", "") or "").strip()
        time_s = str(r["slip"].get("time", "") or "").strip()
        dt = datetime.min
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(date_s, fmt)
                break
            except ValueError:
                pass
        if time_s:
            for tfmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
                try:
                    t = datetime.strptime(time_s, tfmt)
                    dt = dt.replace(hour=t.hour, minute=t.minute)
                    break
                except ValueError:
                    pass
        return dt

    _ssm_sorted = sorted(_ssm_runs, key=_ssm_sort_key)

    # Find best ET / reaction / 60ft indices for per-cell highlights
    def _ssm_best_low_idx(key):
        """Return the row index of the lowest non-null value for a slip field, or -1."""
        vals = []
        for _ri, _rr in enumerate(_ssm_sorted):
            try:
                vals.append((_ri, float(_rr["slip"].get(key))))
            except (TypeError, ValueError):
                pass
        return min(vals, key=lambda x: x[1])[0] if vals else -1

    _ssm_best_et_idx  = _ssm_best_low_idx("ft_1320")
    _ssm_best_rt_idx  = _ssm_best_low_idx("reaction_time")
    _ssm_best_60_idx  = _ssm_best_low_idx("ft_60")

    def _ssm_cell(val, fmt=None, zero_blank=False):
        """Format a cell value or return an em-dash."""
        if val is None or val == "":
            return "—"
        if zero_blank:
            try:
                if float(val) == 0:
                    return "—"
            except (ValueError, TypeError):
                pass
        if fmt:
            try:
                return fmt.format(float(val))
            except (ValueError, TypeError):
                pass
        return str(val)

    _ssm_log_rows_html = ""
    for _ri, _rr in enumerate(_ssm_sorted):
        _sl  = _rr["slip"]
        _rd2 = _rr["rec"].get("run_details") or {}
        _is_best_et = (_ri == _ssm_best_et_idx)
        _is_best_rt = (_ri == _ssm_best_rt_idx)
        _is_best_60 = (_ri == _ssm_best_60_idx)

        _row_bg  = "background:#1a0505;" if _is_best_et else ""
        _et_color = "#ffcc00" if _is_best_et else "#e08030"
        _et_bold  = "font-weight:700;" if _is_best_et else ""
        _rt_color = "#2ecc71" if _is_best_rt else "#aaa"
        _rt_bold  = "font-weight:700;" if _is_best_rt else ""
        _60_color = "#4db8ff" if _is_best_60 else "#aaa"
        _60_bold  = "font-weight:700;" if _is_best_60 else ""

        _res_val   = _rd2.get("result", "")
        _res_color = {"Win": "#2ecc71", "Loss": "#e74c3c", "Bye": "#f0a500"}.get(_res_val, "#666")
        _res_icon  = {"Win": "🏆", "Loss": "❌", "Bye": "🚗"}.get(_res_val, "")
        _res_disp  = f"{_res_icon} {_res_val}" if _res_val else "—"

        # DA: shared helper — da_override wins, else recomputed from raw
        # weather (same source as the run_analysis "Weather at Run Time" card).
        _da2 = get_effective_da(_rr["rec"])
        _da_disp = f"{int(round(_da2)):,}" if _da2 is not None else "—"

        # Date + time combined for display (e.g. "2026-06-13 10:34 AM")
        _date_str = _sl.get("date") or ""
        _time_str = _sl.get("time") or ""
        if _time_str:
            # Convert 24h "HH:MM" → 12h "H:MM AM/PM"
            try:
                from datetime import datetime as _dt
                _t = _dt.strptime(_time_str.strip(), "%H:%M")
                _time_disp = _t.strftime("%-I:%M %p")
            except Exception:
                _time_disp = _time_str
            _date_disp = f"{_date_str} {_time_disp}" if _date_str else _time_disp
        else:
            _date_disp = _date_str if _date_str else "—"

        # 660ft MPH column
        _mph_660 = _ssm_cell(_sl.get("mph_660"), fmt="{:.2f}")

        _ssm_log_rows_html += (
            f'<tr>'
            f'<td style="padding:4px 6px;text-align:left;border-bottom:1px solid #111;{_row_bg}color:#aaa;white-space:nowrap;">'
            f'{_date_disp}</td>'
            f'<td style="padding:4px 6px;text-align:left;border-bottom:1px solid #111;{_row_bg}color:#ddd;white-space:nowrap;max-width:160px;overflow:hidden;text-overflow:ellipsis;">'
            f'{_ssm_cell((_sl.get("track_name") or _sl.get("track_location") or "").strip().title() or None)}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:{_rt_color};{_rt_bold}">{_ssm_cell(_sl.get("reaction_time"), "{:.3f}")}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:{_60_color};{_60_bold}">{_ssm_cell(_sl.get("ft_60"),  "{:.3f}")}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:#aaa;">{_ssm_cell(_sl.get("ft_330"), "{:.3f}")}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:#aaa;">{_mph_660}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:#aaa;">{_ssm_cell(_sl.get("ft_1000"), "{:.3f}")}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:{_et_color};{_et_bold}">{_ssm_cell(_sl.get("ft_1320"), "{:.3f}")}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:#eee;">{_ssm_cell(_sl.get("mph_1320"), "{:.2f}")}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:#888;">{_da_disp}</td>'
            f'<td style="padding:4px 6px;text-align:right;border-bottom:1px solid #111;{_row_bg}color:{_res_color};">{_res_disp}</td>'
            f'</tr>'
        )

    _col_hdr = 'style="color:#666;font-weight:500;padding:4px 6px 8px;text-align:right;border-bottom:1px solid #2a0000;white-space:nowrap;"'
    _col_hdrl = 'style="color:#666;font-weight:500;padding:4px 6px 8px;text-align:left;border-bottom:1px solid #2a0000;white-space:nowrap;"'

    st.markdown(f"""
<div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;
  background:#0a0a0a;font-family:monospace;overflow-x:auto;">
  <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:10px;
    border-bottom:1px solid #2a0000;padding-bottom:6px;">
    📋 Run Log
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:0.82rem;min-width:720px;">
    <thead><tr>
      <th {_col_hdrl}>Date</th>
      <th {_col_hdrl}>Track</th>
      <th {_col_hdr}>Reaction</th>
      <th {_col_hdr}>60ft</th>
      <th {_col_hdr}>330ft</th>
      <th {_col_hdr}>660ft&nbsp;MPH</th>
      <th {_col_hdr}>1000ft</th>
      <th {_col_hdr}>ET</th>
      <th {_col_hdr}>MPH</th>
      <th {_col_hdr}>DA&nbsp;ft</th>
      <th {_col_hdr}>Result</th>
    </tr></thead>
    <tbody>{_ssm_log_rows_html}</tbody>
  </table>
</div>
<p style="color:#555;font-size:0.75rem;margin-top:8px;margin-left:2px;font-family:monospace;line-height:1.8;">
  <span style="display:inline-block;width:9px;height:9px;background:#ffcc00;border-radius:2px;vertical-align:middle;margin-right:4px;"></span><span style="color:#888;">Best ET</span>
  &nbsp;&nbsp;&nbsp;
  <span style="display:inline-block;width:9px;height:9px;background:#2ecc71;border-radius:2px;vertical-align:middle;margin-right:4px;"></span><span style="color:#888;">Best Reaction</span>
  &nbsp;&nbsp;&nbsp;
  <span style="display:inline-block;width:9px;height:9px;background:#4db8ff;border-radius:2px;vertical-align:middle;margin-right:4px;"></span><span style="color:#888;">Best 60ft</span>
</p>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Runs by Track ─────────────────────────────────────────────────────────
    _ssm_track_map: dict = {}
    for r in _ssm_runs:
        _tk = (r["slip"].get("track_name") or r["slip"].get("track_location") or "Unknown").strip().title() or "Unknown"
        if _tk not in _ssm_track_map:
            _ssm_track_map[_tk] = {"runs": 0, "ets": []}
        _ssm_track_map[_tk]["runs"] += 1
        _et_v = r["slip"].get("ft_1320")   # ET stored as ft_1320
        if _et_v is not None:
            try:
                _ssm_track_map[_tk]["ets"].append(float(_et_v))
            except (ValueError, TypeError):
                pass

    _ssm_track_rows_html = ""
    for _tk, _td in sorted(_ssm_track_map.items(), key=lambda x: -x[1]["runs"]):
        _best = f"{min(_td['ets']):.3f}s" if _td["ets"] else "—"
        _ssm_track_rows_html += (
            f'<tr>'
            f'<td style="color:#eee;padding:5px 12px 5px 0;">{_tk}</td>'
            f'<td style="color:#aaa;text-align:center;padding:5px 12px;">{_td["runs"]}</td>'
            f'<td style="color:#ffcc00;font-weight:700;text-align:right;padding:5px 0;">{_best}</td>'
            f'</tr>'
        )

    st.markdown(f"""
<div style="border:1px solid #8b0000;border-radius:10px;padding:16px 20px;
  background:#0a0a0a;font-family:monospace;">
  <div style="font-size:1.1rem;font-weight:700;color:#cc1111;margin-bottom:10px;
    border-bottom:1px solid #2a0000;padding-bottom:6px;">
    🏟️ Runs by Track
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:0.92rem;">
    <thead>
      <tr>
        <th style="color:#666;text-align:left;padding:4px 12px 8px 0;font-weight:500;">Track</th>
        <th style="color:#666;text-align:center;padding:4px 12px 8px;font-weight:500;">Runs</th>
        <th style="color:#666;text-align:right;padding:4px 0 8px;font-weight:500;">Best ET</th>
      </tr>
    </thead>
    <tbody>{_ssm_track_rows_html}</tbody>
  </table>
</div>""", unsafe_allow_html=True)

    st.markdown(
        "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
        "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
        "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
        "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
        "text-decoration:none;'>Contact Us</a></div>",
        unsafe_allow_html=True,
    )
    st.stop()  # Don't render the dashboard on the season page
