"""Optional Claude integration.

Every function here has a deterministic fallback, so the application is fully
functional with no API key: the LLM improves acknowledgements, probing and
theme extraction, but nothing depends on it being present.
"""
from __future__ import annotations

import json
import re

from core import config

MODEL = "claude-opus-5"


def available() -> bool:
    if not config.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        # Not in requirements.txt by default — the app falls back to the
        # built-in scorer, so a missing package is a normal state, not an error.
        return False
    return True


def _client():
    import anthropic

    return anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))


def _text(response) -> str:
    if getattr(response, "stop_reason", None) == "refusal":
        return ""
    return "".join(b.text for b in response.content if b.type == "text").strip()


def _call(system: str, prompt: str, max_tokens: int = 1024) -> str | None:
    """One short, low-effort call. Returns None on any failure — the caller
    then uses its deterministic path rather than surfacing an error to a
    customer who is mid-conversation."""
    if not available():
        return None
    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return _text(response) or None
    except Exception:
        return None


ACK_SYSTEM = (
    "You are a feedback interviewer for a company that sells a product or "
    "service. You never assume what the product category is. Reply with one "
    "short sentence (max 20 words) that acknowledges what the customer just "
    "said and shows you understood the specific point they made. Do not ask a "
    "question. Do not apologise repeatedly. Do not use emoji."
)


def acknowledge(product: str, question: str, answer: str) -> str | None:
    return _call(
        ACK_SYSTEM,
        f"Product: {product}\nQuestion asked: {question}\n"
        f"Customer answered: {answer}\n\nWrite the acknowledgement.",
        max_tokens=120,
    )


PROBE_SYSTEM = (
    "You are a feedback interviewer. The customer's answer was vague, very "
    "short, or did not address the question. Write one short follow-up "
    "question (max 25 words) that gently asks for a specific detail. Do not "
    "scold them. Do not repeat the original question verbatim."
)


def probe(product: str, question: str, answer: str) -> str | None:
    return _call(
        PROBE_SYSTEM,
        f"Product: {product}\nQuestion asked: {question}\n"
        f"Customer answered: {answer}\n\nWrite the follow-up question.",
        max_tokens=120,
    )


ANALYSE_SYSTEM = (
    "You analyse a single customer's product feedback. Return ONLY a JSON "
    "object, no prose, with keys: sentiment (one of positive, mixed, "
    "negative), score (0.0 to 1.0 where 1.0 is delighted), themes (array of "
    "1-4 short lowercase theme labels, two words max each, describing what "
    "the feedback is ABOUT — e.g. 'battery life', 'delivery delay', "
    "'pricing', 'support response'), summary (one sentence, max 25 words), "
    "needs_attention (boolean, true if this customer is at risk of churning "
    "or reports a serious problem). Never invent themes not present in the "
    "text."
)


def analyse(product: str, transcript: str) -> dict | None:
    raw = _call(
        ANALYSE_SYSTEM,
        f"Product: {product}\n\nFeedback transcript:\n{transcript}\n\nReturn the JSON.",
        max_tokens=600,
    )
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    sentiment = str(data.get("sentiment", "")).lower()
    if sentiment not in {"positive", "mixed", "negative"}:
        return None
    try:
        score = max(0.0, min(1.0, float(data.get("score", 0.5))))
    except (TypeError, ValueError):
        return None
    themes = [
        str(t).lower().strip()
        for t in (data.get("themes") or [])
        if str(t).strip()
    ][:4]

    return {
        "sentiment": sentiment,
        "score": score,
        "themes": themes,
        "summary": str(data.get("summary", "")).strip()[:300],
        "needs_attention": bool(data.get("needs_attention")),
    }
