"""Automated Customer Feedback Collection & Insights Agent.

One Streamlit application serving two audiences. The URL decides which:
a `?token=...` query parameter means a customer arrived from their emailed
link, and anything else is the company owner.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import db  # noqa: E402
from views import feedback, owner  # noqa: E402

st.set_page_config(
    page_title="Customer Feedback Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init_db()

token = st.query_params.get("token")
if isinstance(token, list):  # older Streamlit returns a list
    token = token[0] if token else None

if token:
    feedback.render(token)
else:
    owner.render()
