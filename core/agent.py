"""The conversational feedback agent.

State lives entirely in the database, keyed by the invite token, so the
conversation survives a closed tab, a refresh, or a different device. The
agent asks one question at a time, acknowledges each answer, and probes once
when an answer carries no usable signal.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

from core import db, llm

# Product-agnostic by construction: the only variable is {product}, and no
# question presumes a category, a price, a delivery method, or a use case.
QUESTIONS: list[str] = [
    "To start — how would you describe your overall experience with {product} so far?",
    "What's the one thing about {product} that works best for you?",
    "And what's been the most frustrating or disappointing part?",
    "If you could change or add one thing to {product}, what would it be?",
    "Last one: how likely are you to recommend {product} to someone else, and why?",
]

# Acknowledgements used when no LLM is configured. Varied so a five-question
# conversation does not read like the same line five times.
_ACKS = [
    "Got it — thank you, that's useful.",
    "Understood, thanks for spelling that out.",
    "That's helpful to know.",
    "Noted — appreciate the detail.",
    "Thanks, that's exactly the kind of thing we're after.",
]

_LOW_SIGNAL = {
    "", "na", "n/a", "none", "nothing", "no", "yes", "ok", "okay", "fine",
    "good", "bad", "meh", "idk", "i dont know", "i don't know", "dunno",
    "nil", "-", "--", "nope", "yeah", "yep", "sure", "maybe", "same",
}

MIN_WORDS = 3


@dataclass
class Turn:
    """One rendered exchange in the transcript."""

    role: str  # "agent" | "customer"
    text: str


@dataclass
class SessionState:
    invite: db.sqlite3.Row
    customer: db.sqlite3.Row
    answers: list[db.sqlite3.Row]
    completed: bool
    question_idx: int          # which question is being asked now
    awaiting_probe: bool       # the pending prompt is a follow-up, not a new question


def load_session(token: str) -> SessionState | None:
    invite = db.query_one("SELECT * FROM invites WHERE token = ?", (token,))
    if invite is None:
        return None
    customer = db.query_one(
        "SELECT * FROM customers WHERE id = ?", (invite["customer_id"],)
    )
    answers = db.query(
        "SELECT * FROM answers WHERE invite_id = ? ORDER BY question_idx",
        (invite["id"],),
    )
    return SessionState(
        invite=invite,
        customer=customer,
        answers=list(answers),
        completed=invite["status"] == "completed",
        question_idx=len(answers),
        awaiting_probe=False,
    )


def mark_opened(invite_id: int) -> None:
    db.execute(
        """UPDATE invites
              SET status = CASE WHEN status = 'invited' THEN 'opened' ELSE status END,
                  opened_at = COALESCE(opened_at, datetime('now'))
            WHERE id = ?""",
        (invite_id,),
    )


def question_text(idx: int, product: str) -> str:
    return QUESTIONS[idx].replace("{product}", product)


def total_questions() -> int:
    return len(QUESTIONS)


def is_low_signal(answer: str) -> bool:
    """True when an answer gives us nothing to analyse.

    Deliberately conservative — a short answer that names something specific
    ("battery dies fast") passes, because word count alone is a poor proxy for
    usefulness and over-probing annoys people who already answered.
    """
    cleaned = re.sub(r"[^\w\s']", "", answer.strip().lower())
    if cleaned in _LOW_SIGNAL:
        return True
    words = [w for w in cleaned.split() if w]
    if len(words) < MIN_WORDS and not any(len(w) > 6 for w in words):
        return True
    return False


def greeting(name: str, product: str) -> str:
    return (
        f"Hi {name} — thanks for taking a moment. I'd like to ask you "
        f"{len(QUESTIONS)} quick questions about **{product}**, one at a time. "
        "There are no right answers, and the more candid you are the more "
        "useful it is to us."
    )


def acknowledgement(product: str, question: str, answer: str) -> str:
    text = llm.acknowledge(product, question, answer)
    return text or random.choice(_ACKS)


def probe_question(product: str, question: str, answer: str) -> str:
    text = llm.probe(product, question, answer)
    return text or (
        "Could you say a little more about that? Even one concrete example helps."
    )


def record_answer(
    invite_id: int, idx: int, question: str, answer: str, probed: bool
) -> None:
    """Written the moment an answer arrives, which is what makes the session
    resumable — an abandoned conversation keeps everything already given."""
    db.execute(
        """INSERT INTO answers (invite_id, question_idx, question, answer, probed)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (invite_id, question_idx) DO UPDATE SET
               answer = excluded.answer, probed = excluded.probed""",
        (invite_id, idx, question, answer, int(probed)),
    )
    db.execute(
        """UPDATE invites
              SET status = CASE WHEN status = 'completed' THEN 'completed'
                                ELSE 'in_progress' END
            WHERE id = ?""",
        (invite_id,),
    )


def is_complete(invite_id: int) -> bool:
    """"Completed" means every question has a recorded answer — not merely that
    the customer reached the last screen. The reward hangs off this definition."""
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM answers WHERE invite_id = ?", (invite_id,)
    )
    return (row["n"] if row else 0) >= len(QUESTIONS)


def complete(invite_id: int) -> None:
    db.execute(
        """UPDATE invites
              SET status = 'completed',
                  completed_at = COALESCE(completed_at, datetime('now'))
            WHERE id = ?""",
        (invite_id,),
    )


def transcript(invite_id: int) -> str:
    rows = db.query(
        "SELECT question, answer FROM answers WHERE invite_id = ? ORDER BY question_idx",
        (invite_id,),
    )
    return "\n\n".join(f"Q: {r['question']}\nA: {r['answer']}" for r in rows)
