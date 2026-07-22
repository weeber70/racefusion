"""
run_manager.py — RaceFusion Run Manager page.
"""
import re
import streamlit as st
from datetime import datetime
from database import _sb, _delete_run_files, get_effective_da
from weather import canonical_track, build_track_display_map
from config import load_config


def show_run_manager(saved_runs: list, current_user: str, access_granted: bool, logo_src: "str | None" = None):
    """Render the Run Manager page."""
    if logo_src:
        st.markdown(
            f'<img src="{logo_src}" style="max-width:520px;width:60%;'
            f'margin:0 auto 4px auto;display:block;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🏁 RaceFusion")
    st.markdown("# 🗂️ Run Manager")

    # ── Init session state ─────────────────────────────────────────────────────
    if "rm_selected" not in st.session_state:
        st.session_state["rm_selected"] = set()
    if "compare_run_ids_pending" not in st.session_state:
        st.session_state["compare_run_ids_pending"] = []

    # ── Fetch all runs for this user (includes event_name column) ─────────────
    _rm_rows: list[dict] = []
    if _sb:
        try:
            _rm_rows = (
                _sb.table("runs")
                .select("csv_filename,run_data,event_name,created_at")
                .eq("username", current_user)
                .order("created_at", desc=True)
                .execute()
                .data
            )
        except Exception as _rm_fetch_err:
            st.error(f"Could not load runs: {_rm_fetch_err}")

    # ── Build flat list with derived fields ────────────────────────────────────
    _rm_run_list: list[dict] = []
    for _rmr in _rm_rows:
        _rmr_rec  = _rmr.get("run_data") or {}
        _rmr_slip = _rmr_rec.get("timeslip") or {}
        _rm_run_list.append({
            "filename":   _rmr["csv_filename"],
            "event_name": _rmr.get("event_name") or "",
            "record":     _rmr_rec,
            "slip":       _rmr_slip,
            "date":       _rmr_slip.get("date", ""),
            "track":      _rmr_slip.get("track_name", "") or _rmr_slip.get("track_location", ""),
            "created_at": _rmr.get("created_at", ""),
        })

    _rm_all_ids = [r["filename"] for r in _rm_run_list]

    # ── Search + Compare button row ────────────────────────────────────────────
    # Rebuild pending list from actual checkbox widget state each render —
    # never from rm_selected, which can carry stale IDs across sessions.
    _rm_pending = [
        k[len("rm_chk_"):]
        for k, v in st.session_state.items()
        if k.startswith("rm_chk_") and v is True
    ]
    st.session_state["compare_run_ids_pending"] = _rm_pending
    _rm_n_sel = len(_rm_pending)
    _rm_can_compare = (_rm_n_sel == 2)

    if _rm_n_sel == 0:
        _rm_cmp_caption = "Select 2 runs to compare"
    elif _rm_n_sel == 1:
        _rm_cmp_caption = "Select 1 more run"
    elif _rm_n_sel == 2:
        _rm_cmp_caption = "Ready to compare"
    else:
        _rm_cmp_caption = f"Too many selected (max 2)"

    _rm_top_c1, _rm_top_c2 = st.columns([3, 1])
    with _rm_top_c1:
        _rm_search_raw = st.text_input(
            "🔍 Search", placeholder="Filter by track, date, or event name…",
            key="rm_search_input", label_visibility="collapsed",
        )
    with _rm_top_c2:
        if st.button(
            "⚖️ Compare Selected",
            key="rm_compare_btn",
            type="primary",
            disabled=not _rm_can_compare,
            use_container_width=True,
        ):
            st.session_state["compare_run_ids"] = list(
                st.session_state.get("compare_run_ids_pending", [])
            )
            st.session_state["current_page"] = "run_comparison"
            st.query_params["p"] = "run_comparison"
            st.rerun()
        st.caption(_rm_cmp_caption)

    _rm_search = (_rm_search_raw or "").strip().lower()

    st.divider()

    # ── Group runs by (date, canonical track) ──────────────────────────────────
    # canonical_track applies auto-normalization (case, punctuation,
    # " at <place>" suffixes) plus the user's manual track merges, so
    # "Lucas Oil Raceway Park" and "Lucas Oil Raceway Park At Indianapolis"
    # collapse into one event. Raw timeslip text is never modified.
    _rm_aliases = load_config().get("track_aliases", {}) or {}
    # One consistent display name per canonical track — computed over ALL
    # runs so every date bucket of the same track shows the identical label.
    _rm_track_disp = build_track_display_map(
        (r["track"] for r in _rm_run_list), _rm_aliases
    )
    from collections import defaultdict as _rm_defaultdict
    _rm_groups: dict = _rm_defaultdict(list)
    for _rmr in _rm_run_list:
        _rm_norm_track = canonical_track(_rmr["track"], _rm_aliases)[0] if _rmr["track"] else ""
        _rm_groups[(_rmr["date"], _rm_norm_track)].append(_rmr)

    # ── Two-level hierarchy: track → date ──────────────────────────────────────
    # Top level is bounded by DISTINCT TRACKS (not race days). Each track
    # expander contains one toggle row per date visited; Streamlit forbids
    # nested expanders, so the date level is a toggle that expands in place.
    _rm_by_track: dict = _rm_defaultdict(list)   # track_key → [(date, runs)]
    for (_gd, _gt), _gruns in _rm_groups.items():
        _rm_by_track[_gt].append((_gd, _gruns))

    if not _rm_by_track:
        st.info("No runs saved yet. Use **Create New Run** to upload your first run.")

    # Track order: most runs first (mirrors Season Summary's Runs by Track)
    _rm_track_order = sorted(
        _rm_by_track.keys(),
        key=lambda _tk: -sum(len(_r) for _, _r in _rm_by_track[_tk]),
    )

    for _rm_gntrack in _rm_track_order:
        _rm_track_days = sorted(
            _rm_by_track[_rm_gntrack],
            key=lambda _d: (_d[0] or "0000-00-00"),
            reverse=True,
        )
        # Canonical display name — identical for every bucket of this track
        _rm_disp_track = _rm_track_disp.get(_rm_gntrack) or "Unknown Track"

        # Search filter at the date-group level; track stays visible if ANY
        # of its days match (by track name, date, or event name).
        if _rm_search:
            _rm_days_visible = [
                (_gd, _gruns) for _gd, _gruns in _rm_track_days
                if _rm_search in
                f"{_gd} {_rm_disp_track} {_gruns[0]['event_name'] or ''}".lower()
            ]
        else:
            _rm_days_visible = _rm_track_days
        if not _rm_days_visible:
            continue

        # Track summary: total runs + best ET across all days
        _rm_trk_total = sum(len(_r) for _, _r in _rm_track_days)
        _rm_trk_ets = []
        for _, _gruns in _rm_track_days:
            for _gr in _gruns:
                try:
                    _et_v = float(_gr["slip"].get("ft_1320") or 0)
                    if _et_v > 0:
                        _rm_trk_ets.append(_et_v)
                except (TypeError, ValueError):
                    pass
        _rm_trk_best = f"{min(_rm_trk_ets):.3f}s" if _rm_trk_ets else "—"

        _rm_trk_lbl = (
            f"📍 {_rm_disp_track} · {_rm_trk_total} run"
            f"{'s' if _rm_trk_total != 1 else ''} · Best ET: {_rm_trk_best}"
        )

        with st.expander(_rm_trk_lbl, expanded=False):
          for (_rm_gdate, _rm_evt_runs) in _rm_days_visible:
            _rm_stored_evt = _rm_evt_runs[0]["event_name"] or ""
            _rm_n_evt      = len(_rm_evt_runs)

            # Reformat date as M-DD-YYYY for display (sorting uses ISO date)
            try:
                _rm_disp_date = __import__("datetime").date.fromisoformat(_rm_gdate).strftime("%-m-%d-%Y")
            except Exception:
                _rm_disp_date = _rm_gdate or "—"

            # Stable key fragment: date + canonical track (deduplicated/safe)
            _rm_gkey = re.sub(r"[^\w]", "_", f"{_rm_gdate}_{_rm_gntrack}")

            _rm_day_lbl = (
                f"📅 {_rm_disp_date} · {_rm_n_evt} run"
                f"{'s' if _rm_n_evt != 1 else ''}"
                + (f" · {_rm_stored_evt}" if _rm_stored_evt else "")
            )
            # Date sub-row: toggle expands the day's runs in place
            if not st.toggle(_rm_day_lbl, key=f"rm_day_{_rm_gkey}"):
                continue
            _rm_evt_ids     = [r["filename"] for r in _rm_evt_runs]
            _rm_evt_sel_key  = f"rm_sel_evt_{_rm_gkey}"
            _rm_evt_del_key  = f"rm_del_evt_{_rm_gkey}"

            # Callbacks capture current loop values via default-arg idiom
            def _rm_on_evt_sel(_eids=_rm_evt_ids, _esk=_rm_evt_sel_key):
                _checked = st.session_state.get(_esk, False)
                for _fid in _eids:
                    st.session_state[f"rm_chk_{_fid}"] = _checked
                    if _checked:
                        st.session_state["rm_selected"].add(_fid)
                    else:
                        st.session_state["rm_selected"].discard(_fid)

            # Checked runs in this event — determines whether 🗑 is active
            _rm_evt_checked_ids = [
                fid for fid in _rm_evt_ids
                if st.session_state.get(f"rm_chk_{fid}", False)
            ]
            _rm_evt_n_checked = len(_rm_evt_checked_ids)

            # ── Event controls: [☐ select] [🗑] — both packed into the narrow
            # zone under the date/run-count text (first ~15% of the row):
            # checkbox at ~0–8%, trash immediately right at ~8–17%.
            _rm_evc1, _rm_evc2, _rm_evc3 = st.columns([1, 1, 10])
            _rm_evc1.checkbox(
                "Select event", key=_rm_evt_sel_key,
                label_visibility="collapsed",
                on_change=_rm_on_evt_sel,
            )
            # 🗑 is disabled until ≥1 run in this event is checked;
            # when active it deletes only the checked runs (not the whole event).
            if _rm_evc2.button(
                "🗑",
                key=_rm_evt_del_key,
                help=(
                    f"Delete {_rm_evt_n_checked} checked run(s) in this event"
                    if _rm_evt_n_checked
                    else "Check runs below to enable"
                ),
                disabled=(_rm_evt_n_checked == 0),
            ):
                st.session_state[f"rm_conf_{_rm_evt_del_key}"] = True

            if st.session_state.get(f"rm_conf_{_rm_evt_del_key}"):
                st.warning(
                    f"⚠️ Delete {_rm_evt_n_checked} checked run(s) from this event? "
                    "This cannot be undone."
                )
                _rm_ce1, _rm_ce2 = st.columns(2)
                if _rm_ce1.button("✅ Confirm", key=f"rm_conf_yes_{_rm_gkey}", type="primary"):
                    for _fid in _rm_evt_checked_ids:
                        _delete_run_files(_fid)
                        st.session_state.pop(f"rm_chk_{_fid}", None)
                        st.session_state["rm_selected"].discard(_fid)
                    st.session_state.pop(f"rm_conf_{_rm_evt_del_key}", None)
                    st.session_state["_reset_selector"] = True
                    st.rerun()
                if _rm_ce2.button("Cancel", key=f"rm_conf_no_{_rm_gkey}"):
                    st.session_state.pop(f"rm_conf_{_rm_evt_del_key}", None)
                    st.rerun()

            st.divider()

            # ── Column header row ──────────────────────────────────────────────
            _rm_hcols = st.columns([0.5, 1.5, 1.2, 1.2, 1.5, 1.8, 1.2, 1.5])
            for _rmhc, _rmhl in zip(
                _rm_hcols,
                ["", "Date", "Time", "ET", "Speed", "DA", "Result", ""],
            ):
                _rmhc.markdown(f"**{_rmhl}**")

            # ── One row per run ────────────────────────────────────────────────
            for _rm_run in _rm_evt_runs:
                _rm_fn   = _rm_run["filename"]
                _rm_slip = _rm_run["slip"]
                _rm_rec  = _rm_run["record"]
                _rm_rd   = _rm_rec.get("run_details") or {}

                _rm_r_date = _rm_run["date"] or "—"
                _rm_r_time = _rm_slip.get("time", "") or "—"
                _rm_r_et   = _rm_slip.get("ft_1320")
                _rm_r_spd  = _rm_slip.get("mph_1320")
                _rm_r_res  = _rm_rd.get("result") or "—"
                _rm_r_da   = get_effective_da(_rm_rec)

                def _rm_on_row_chk(_fn=_rm_fn):
                    if st.session_state.get(f"rm_chk_{_fn}", False):
                        st.session_state["rm_selected"].add(_fn)
                    else:
                        st.session_state["rm_selected"].discard(_fn)

                _rm_rcols = st.columns([0.5, 1.5, 1.2, 1.2, 1.5, 1.8, 1.2, 1.5])
                _rm_rcols[0].checkbox(
                    "", key=f"rm_chk_{_rm_fn}",
                    label_visibility="collapsed",
                    on_change=_rm_on_row_chk,
                )
                _rm_rcols[1].write(_rm_r_date)
                _rm_rcols[2].write(_rm_r_time)
                _rm_rcols[3].write(f"{float(_rm_r_et):.3f}s" if _rm_r_et else "—")
                _rm_rcols[4].write(
                    f"{float(_rm_r_spd):.2f} mph" if _rm_r_spd else "—"
                )
                _rm_rcols[5].write(
                    f"{_rm_r_da:,} ft" if _rm_r_da is not None else "—"
                )
                _rm_rcols[6].write(_rm_r_res)
                if _rm_rcols[7].button(
                    "▶ Open", key=f"rm_open_{_rm_fn}", use_container_width=True
                ):
                    st.session_state["active_run_id"] = _rm_fn
                    st.query_params["run"] = _rm_fn
                    st.session_state["current_page"] = "dashboard"
                    st.query_params["p"] = "dashboard"
                    # Sync the sidebar selectbox index
                    for _rmi, _rmsr in enumerate(saved_runs):
                        if _rmsr["filename"] == _rm_fn:
                            st.session_state["_run_selector_idx"] = _rmi + 1
                            st.session_state.pop("run_selector", None)
                            break
                    st.rerun()

    st.markdown(
        "<div style='text-align:center;color:rgba(255,255,255,0.35);font-size:0.75rem;"
        "padding:2rem 0 1rem 0;border-top:1px solid rgba(255,255,255,0.08);margin-top:3rem;'>"
        "© 2025 Weeb Enterprises, LLC · RaceFusion™ · All rights reserved · "
        "<a href='mailto:chris@weebenterprises.com' style='color:rgba(255,255,255,0.35);"
        "text-decoration:none;'>Contact Us</a></div>",
        unsafe_allow_html=True,
    )
    st.stop()  # Don't render the dashboard on the run manager page
