"""Prove the mail path actually delivers, without needing real credentials.

Runs a throwaway SMTP server in-process, points the app's own SMTPConfig at
it, sends a real invite through the real code path, and asserts the message
arrived with the customer's personalised link in it.

If this passes, the sending code is correct and the only remaining variable
is the credentials in the deployed environment.

Run: python test_smtp.py
"""
from __future__ import annotations

import asyncio
import email
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import db  # noqa: E402

db.DB_PATH = db.DB_PATH.parent / "test_smtp.db"
if db.DB_PATH.exists():
    db.DB_PATH.unlink()

from core import mailer, rewards  # noqa: E402

ok = fail = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


# --------------------------------------------------------------------------
# A minimal SMTP server. Written against asyncio directly rather than pulling
# in aiosmtpd, so this test adds no dependency the app does not already have.
# --------------------------------------------------------------------------
RECEIVED: list[str] = []


class SMTPProtocol(asyncio.Protocol):
    def connection_made(self, transport):
        self.transport = transport
        self.buf = b""
        self.in_data = False
        transport.write(b"220 localhost test\r\n")

    def data_received(self, data):
        self.buf += data
        while True:
            if self.in_data:
                if b"\r\n.\r\n" in self.buf:
                    body, _, rest = self.buf.partition(b"\r\n.\r\n")
                    RECEIVED.append(body.decode("utf-8", "replace"))
                    self.buf = rest
                    self.in_data = False
                    self.transport.write(b"250 OK\r\n")
                    continue
                return
            if b"\r\n" not in self.buf:
                return
            line, _, self.buf = self.buf.partition(b"\r\n")
            cmd = line.decode("utf-8", "replace").upper()
            if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                self.transport.write(b"250-localhost\r\n250 AUTH LOGIN\r\n")
            elif cmd.startswith(("MAIL", "RCPT")):
                self.transport.write(b"250 OK\r\n")
            elif cmd.startswith("DATA"):
                self.in_data = True
                self.transport.write(b"354 Send data\r\n")
            elif cmd.startswith("QUIT"):
                self.transport.write(b"221 Bye\r\n")
                self.transport.close()
                return
            else:
                self.transport.write(b"250 OK\r\n")


def run_server(port: int, ready: threading.Event) -> None:
    async def main():
        loop = asyncio.get_running_loop()
        server = await loop.create_server(SMTPProtocol, "127.0.0.1", port)
        ready.set()
        async with server:
            await asyncio.sleep(25)

    asyncio.run(main())


PORT = 1025
ready = threading.Event()
threading.Thread(target=run_server, args=(PORT, ready), daemon=True).start()
ready.wait(timeout=5)
time.sleep(0.2)

print("\n[1] Local SMTP server")
check("server listening", ready.is_set(), f"127.0.0.1:{PORT}")

print("\n[2] Config")
cfg = mailer.SMTPConfig(
    host="127.0.0.1", port=PORT, username="", password="",
    sender="shop@example.com", use_tls=False,
)
check("config reports configured", cfg.configured)

print("\n[3] Send a real invite through the app's own code path")
db.init_db()
token = mailer.new_token()
values = {
    "name": "Priya Sharma",
    "product": "Aurora Smart Watch",
    "company": "Northwind Labs",
    "reward": rewards.REWARD_LABEL,
    "link": mailer.feedback_link("https://avinashddk.streamlit.app", token),
}
subject = mailer.render(mailer.DEFAULT_SUBJECT, **values)
body = mailer.render(mailer.DEFAULT_BODY, **values)
outbox_id = mailer.queue_email("priya@example.com", subject, body, "invite")
delivered, error = mailer.deliver(outbox_id, cfg)

check("deliver() reported success", delivered, error or "")
check("no error recorded", error is None, str(error))
time.sleep(0.4)
check("server actually received it", len(RECEIVED) == 1, f"{len(RECEIVED)} message(s)")

if RECEIVED:
    msg = email.message_from_string(RECEIVED[0])
    check("To header correct", "priya@example.com" in str(msg.get("To")))
    check("From header correct", "shop@example.com" in str(msg.get("From")))
    check("subject personalised", "Priya Sharma" in str(msg.get("Subject")),
          str(msg.get("Subject")))

    # Decode rather than reading the raw string: the body is quoted-printable,
    # so '?token=' appears on the wire as '?token=3D'. Every mail client
    # decodes it — a raw-string assertion would fail on a correct message.
    parts = {}
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        parts[part.get_content_subtype()] = (
            part.get_payload(decode=True).decode("utf-8", "replace")
        )

    check("both text and html parts sent", set(parts) == {"plain", "html"},
          str(sorted(parts)))

    text = parts.get("plain", "")
    check("text: customer name", "Priya Sharma" in text)
    check("text: product", "Aurora Smart Watch" in text)
    check("text: unique link intact", f"?token={token}" in text)
    check("text: no unfilled placeholders",
          not any(p in text for p in ("{name}", "{product}", "{link}", "{reward}")))

    htm = parts.get("html", "")
    check("html: link is a real anchor", f'href="' in htm and token in htm)
    check("html: has a click target", "Share your feedback" in htm)
    check("html: no unfilled placeholders",
          not any(p in htm for p in ("{name}", "{product}", "{link}", "{reward}")))

print("\n[4] Outbox row marked delivered")
row = db.query_one("SELECT * FROM outbox WHERE id = ?", (outbox_id,))
check("delivered flag set", bool(row["delivered"]))
check("error cleared", row["error"] is None, str(row["error"]))

print("\n[5] Failure path — wrong port must report, not swallow")
bad = mailer.SMTPConfig(host="127.0.0.1", port=1, sender="x@example.com", use_tls=False)
oid2 = mailer.queue_email("x@example.com", "s", "b", "invite")
d2, e2 = mailer.deliver(oid2, bad)
check("reports failure", not d2)
check("gives an actual error", bool(e2), (e2 or "")[:60])
row2 = db.query_one("SELECT * FROM outbox WHERE id = ?", (oid2,))
check("error stored for the owner to read", bool(row2["error"]))

print(f"\n{'=' * 50}\n  {ok} passed, {fail} failed\n{'=' * 50}")
sys.exit(1 if fail else 0)
