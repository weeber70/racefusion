"""
admin.py — RaceFusion Admin Panel page.
"""
import json
import streamlit as st
from database import _sb, _write_maintenance_mode


def show_admin_panel(maintenance_on: bool, current_user: str):
    """Render the admin panel expander (weeber70 only)."""
    if current_user != "weeber70" or not _sb:
        return
    with st.expander("🔒 Admin Panel", expanded=False):


        def _time_ago(ts_str: str) -> str:
            if not ts_str:
                return "never"
            try:
                from datetime import datetime, timezone
                _ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                _s  = int((datetime.now(timezone.utc) - _ts).total_seconds())
                if _s < 60:    return f"{_s}s ago"
                if _s < 3600:  return f"{_s // 60}m ago"
                if _s < 86400: return f"{_s // 3600}h ago"
                return f"{_s // 86400}d ago"
            except Exception:
                return ts_str

        # ── Maintenance mode toggle ───────────────────────────────────────────
        _maint_toggle = st.toggle(
            "🚧 Maintenance Mode",
            value=maintenance_on,
            key="admin_maint_toggle",
            help="When ON, all users except weeber70 see the maintenance screen.",
        )
        if _maint_toggle != maintenance_on:
            _write_maintenance_mode(_maint_toggle)
            st.rerun()

        st.markdown("---")

        try:
            from datetime import datetime, timezone, timedelta

            _ten_ago      = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            _active_rows  = _sb.table("sessions").select("username").gte("last_seen", _ten_ago).execute().data
            _active_count = len(_active_rows)

            _cred_res    = _sb.table("credentials").select("username", count="exact").execute()
            _total_users = _cred_res.count or len(_cred_res.data)

            _runs_res    = _sb.table("runs").select("username", count="exact").execute()
            _total_runs  = _runs_res.count or len(_runs_res.data)

            try:
                _slip_res   = _sb.table("runs").select("id", count="exact").not_.is_("run_data->>timeslip_storage_key", "null").execute()
                _total_slip = _slip_res.count or 0
            except Exception:
                _total_slip = "—"

            _a1, _a2 = st.columns(2)
            _a1.metric("Active now",        _active_count)
            _a2.metric("Accounts",          _total_users)
            _b1, _b2 = st.columns(2)
            _b1.metric("Runs logged",       _total_runs)
            _b2.metric("Timeslips scanned", _total_slip)

            st.markdown("---")

            _all_creds    = _sb.table("credentials").select("username").execute().data
            _all_sessions = {r["username"]: r["last_seen"]
                             for r in _sb.table("sessions").select("username,last_seen").execute().data}
            _run_rows     = _runs_res.data or _sb.table("runs").select("username").execute().data
            _run_counts   = {}
            for _rr in _run_rows:
                _u = _rr.get("username", "")
                _run_counts[_u] = _run_counts.get(_u, 0) + 1

            _all_emails: dict[str, str] = {}
            # 1. Seed from user_configs JSON blob (legacy store)
            try:
                _cfg_rows = _sb.table("user_configs").select("username,config").execute().data
                for _cr in _cfg_rows:
                    _cfg_blob = _cr.get("config") or {}
                    if isinstance(_cfg_blob, str):
                        try: _cfg_blob = json.loads(_cfg_blob)
                        except Exception: _cfg_blob = {}
                    _em = _cfg_blob.get("email", "")
                    if _em:
                        _all_emails[_cr["username"]] = _em
            except Exception:
                pass
            # 2. Override/fill from credentials.email (authoritative column)
            try:
                _cred_email_rows = _sb.table("credentials").select("username,email").execute().data
                for _cer in _cred_email_rows:
                    _em = _cer.get("email") or ""
                    if _em:
                        _all_emails[_cer["username"]] = _em
            except Exception:
                pass

            _rows_html = ""
            for _cu in sorted(_all_creds, key=lambda x: x["username"]):
                _un  = _cu["username"]
                _ls  = _time_ago(_all_sessions.get(_un, ""))
                _rc  = _run_counts.get(_un, 0)
                _em  = _all_emails.get(_un, "—")
                _bold = "font-weight:700;" if _un == "weeber70" else ""
                _rows_html += (
                    f'<tr>'
                    f'<td style="color:#ccc;{_bold}padding:3px 6px 3px 0;">{_un}</td>'
                    f'<td style="color:#777;padding:3px 6px;font-size:0.78rem;">{_em}</td>'
                    f'<td style="color:#888;padding:3px 6px;">{_ls}</td>'
                    f'<td style="color:#cc1111;text-align:right;padding:3px 0;">{_rc}</td>'
                    f'</tr>'
                )

            st.markdown(f"""
<div style="font-size:0.82rem;font-family:monospace;overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;">
<thead><tr>
  <th style="color:#666;text-align:left;padding:2px 6px 4px 0;border-bottom:1px solid #2a2a3a;">User</th>
  <th style="color:#666;text-align:left;padding:2px 6px 4px;border-bottom:1px solid #2a2a3a;">Email</th>
  <th style="color:#666;text-align:left;padding:2px 6px 4px;border-bottom:1px solid #2a2a3a;">Last seen</th>
  <th style="color:#666;text-align:right;padding:2px 0 4px;border-bottom:1px solid #2a2a3a;">Runs</th>
</tr></thead>
<tbody>{_rows_html}</tbody>
</table>
</div>""", unsafe_allow_html=True)

        except Exception as _admin_err:
            st.warning(f"Admin data unavailable: {_admin_err}")
