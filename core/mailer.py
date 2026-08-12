"""Outreach and reward email.

Every send is written to `outbox` first and only then attempted over SMTP. If
SMTP is not configured the row still exists, so the whole workflow — invite,
unique link, reward — is demonstrable end to end with no mail server, and the
owner can read exactly what each customer would have received.
"""
from __future__ import annotations

import os
import secrets
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from core import db

DEFAULT_SUBJECT = "How are you finding your {product}, {name}?"

DEFAULT_BODY = """Hi {name},

You picked up {product} from us recently, and we'd genuinely like to know how
it's working out — what's good, what isn't, and what you'd change.

It's a short conversation rather than a form, and it takes about two minutes:

{link}

As a thank you, we'll email you a {reward} gift card the moment you finish.

Thanks for your time,
{company}
"""

REWARD_SUBJECT = "Your {reward} gift card from {company}"

REWARD_BODY = """Hi {name},

Thank you for telling us about your experience with {product} — that feedback
goes straight to the team that works on it.

Here is the {reward} gift card we promised:

    {code}

Redeem it against your next order. It does not expire.

With thanks,
{company}
"""


@dataclass
class SMTPConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    use_tls: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    @classmethod
    def from_env(cls) -> "SMTPConfig":
        return cls(
            host=os.getenv("SMTP_HOST", ""),
            port=int(os.getenv("SMTP_PORT", "587") or 587),
            username=os.getenv("SMTP_USER", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            sender=os.getenv("SMTP_SENDER", ""),
            use_tls=os.getenv("SMTP_TLS", "true").strip().lower() != "false",
        )


def new_token() -> str:
    """URL-safe, unguessable, and unique per (customer, campaign)."""
    return secrets.token_urlsafe(24)


def render(template: str, **values) -> str:
    """Fill a template, leaving unknown placeholders visibly intact.

    A silent KeyError here would mean an owner's typo in the editor blows up
    the whole send; showing `{typo}` in the preview tells them what to fix.
    """
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def queue_email(to_email: str, subject: str, body: str, kind: str) -> int:
    return db.execute(
        "INSERT INTO outbox (to_email, subject, body, kind) VALUES (?, ?, ?, ?)",
        (to_email, subject, body, kind),
    )


def deliver(outbox_id: int, cfg: SMTPConfig) -> tuple[bool, str | None]:
    """Attempt real delivery. Returns (delivered, error)."""
    row = db.query_one("SELECT * FROM outbox WHERE id = ?", (outbox_id,))
    if row is None:
        return False, "outbox row missing"

    if not cfg.configured:
        # Simulation mode is a first-class outcome, not a failure: the message
        # is recorded and readable, it just was not handed to a mail server.
        db.execute(
            "UPDATE outbox SET delivered = 0, error = ? WHERE id = ?",
            ("not sent — SMTP not configured (simulation mode)", outbox_id),
        )
        return False, None

    msg = EmailMessage()
    msg["Subject"] = row["subject"]
    msg["From"] = cfg.sender
    msg["To"] = row["to_email"]
    msg.set_content(row["body"])

    try:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=20) as server:
            if cfg.use_tls:
                server.starttls()
            if cfg.username:
                server.login(cfg.username, cfg.password)
            server.send_message(msg)
    except Exception as exc:  # surfaced to the owner, never swallowed
        db.execute(
            "UPDATE outbox SET delivered = 0, error = ? WHERE id = ?",
            (f"{type(exc).__name__}: {exc}", outbox_id),
        )
        return False, str(exc)

    db.execute(
        "UPDATE outbox SET delivered = 1, error = NULL WHERE id = ?", (outbox_id,)
    )
    return True, None


def feedback_link(base_url: str, token: str) -> str:
    base = (base_url or "http://localhost:8501").rstrip("/")
    return f"{base}/?token={token}"
