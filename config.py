"""
config.py — RaceFusion user configuration persistence.

NOTE: DEFAULT_CONFIG does not exist in the current codebase; load_config()
returns {} when Supabase is unavailable or the row is absent.
"""

import streamlit as st
from database import _sb


def load_config() -> dict:
    if not _sb: return {}
    username = st.session_state.get("rf_user", "")
    try:
        rows = _sb.table("user_configs").select("config").eq("username", username).execute().data
        if rows:
            data = rows[0]["config"] or {}
            data.pop("anthropic_api_key", None)
            return data
    except Exception:
        pass
    return {}


def save_config(cfg: dict):
    if not _sb: return
    username = st.session_state.get("rf_user", "")
    safe = {k: v for k, v in cfg.items() if k != "anthropic_api_key"}
    try:
        _sb.table("user_configs").upsert(
            {"username": username, "config": safe, "updated_at": "now()"},
            on_conflict="username"
        ).execute()
    except Exception as e:
        st.warning(f"Config save failed: {e}")
