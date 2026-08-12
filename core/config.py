"""Configuration lookup that works both locally and on Streamlit Cloud.

Locally, settings come from environment variables. On Streamlit Community
Cloud they are entered in the app's Secrets panel and surface through
`st.secrets`. Reading only one of the two means a correctly-configured cloud
deployment still behaves as if nothing were set — so this checks both.
"""
from __future__ import annotations

import os


def get(key: str, default: str = "") -> str:
    """Return a setting from Streamlit secrets, then the environment."""
    try:
        import streamlit as st

        # Accessing st.secrets raises if no secrets file exists at all, which
        # is the normal local case — fall through to the environment.
        if key in st.secrets:
            value = st.secrets[key]
            if value is not None and str(value).strip():
                return str(value).strip()
    except Exception:
        pass

    return os.getenv(key, default).strip()


def get_int(key: str, default: int) -> int:
    raw = get(key, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def get_bool(key: str, default: bool) -> bool:
    raw = get(key, "").lower()
    if not raw:
        return default
    return raw not in {"false", "0", "no", "off"}
