"""Gift card issuance.

"One reward per genuine completed response" is enforced in two places that do
not depend on application flow being correct: `rewards.invite_id` is UNIQUE, so
the database refuses a second row, and issuance is gated on every question
having an answer rather than on the customer merely reaching the final screen.
"""
from __future__ import annotations

import secrets

from core import agent, db, mailer

REWARD_AMOUNT = 1000
REWARD_CURRENCY = "INR"
REWARD_LABEL = "₹1,000"


def _code() -> str:
    raw = secrets.token_hex(6).upper()
    return f"GIFT-{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def existing(invite_id: int) -> db.sqlite3.Row | None:
    return db.query_one("SELECT * FROM rewards WHERE invite_id = ?", (invite_id,))


def issue(invite_id: int, company: str, cfg: mailer.SMTPConfig) -> tuple[db.sqlite3.Row | None, str]:
    """Issue at most one reward for a completed response.

    Returns (reward_row, status) where status is one of: issued, already,
    incomplete.
    """
    already = existing(invite_id)
    if already is not None:
        return already, "already"

    if not agent.is_complete(invite_id):
        return None, "incomplete"

    try:
        db.execute(
            "INSERT INTO rewards (invite_id, code, amount, currency) VALUES (?, ?, ?, ?)",
            (invite_id, _code(), REWARD_AMOUNT, REWARD_CURRENCY),
        )
    except db.sqlite3.IntegrityError:
        # Lost a race against a concurrent submit — the other one won, and the
        # UNIQUE constraint is precisely what stopped a second gift card.
        return existing(invite_id), "already"

    reward = existing(invite_id)
    row = db.query_one(
        """SELECT c.name, c.email, c.product
             FROM invites i JOIN customers c ON c.id = i.customer_id
            WHERE i.id = ?""",
        (invite_id,),
    )
    if row is not None and reward is not None:
        subject = mailer.render(
            mailer.REWARD_SUBJECT, reward=REWARD_LABEL, company=company
        )
        body = mailer.render(
            mailer.REWARD_BODY,
            name=row["name"], product=row["product"], reward=REWARD_LABEL,
            code=reward["code"], company=company,
        )
        outbox_id = mailer.queue_email(row["email"], subject, body, kind="reward")
        mailer.deliver(outbox_id, cfg)

    return reward, "issued"
