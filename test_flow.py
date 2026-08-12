"""End-to-end exercise of the workflow, with no Streamlit UI involved.

Run: python test_flow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import db  # noqa: E402

# Use a throwaway database so a test run never touches real data.
db.DB_PATH = db.DB_PATH.parent / "test_feedback.db"
if db.DB_PATH.exists():
    db.DB_PATH.unlink()

from core import agent, analysis, ingest, mailer, rewards  # noqa: E402

ok = fail = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok, fail
    if condition:
        ok += 1
        print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


db.init_db()
cfg = mailer.SMTPConfig()  # unconfigured → simulation mode

print("\n[1] Ingest — unfamiliar headers, missing fields, bad email, duplicates")
with open("sample_customers.csv", "rb") as fh:
    class Upload:
        name = "sample_customers.csv"
        _data = fh.read()
        def read(self):
            return self._data
    df = ingest.read_table(Upload())

mapping = ingest.guess_mapping(list(df.columns))
check("auto-mapped 'Customer Name' → name", mapping["name"] == "Customer Name", str(mapping["name"]))
check("auto-mapped 'E-mail Address' → email", mapping["email"] == "E-mail Address", str(mapping["email"]))
check("auto-mapped 'Product Purchased' → product", mapping["product"] == "Product Purchased", str(mapping["product"]))

report = ingest.validate(df, mapping)
check("missing name caught", len(report.missing_name) == 1, f"{len(report.missing_name)} row(s)")
check("invalid email caught", len(report.bad_email) == 1, f"{len(report.bad_email)} row(s)")
check("missing product caught", len(report.missing_product) == 1, f"{len(report.missing_product)} row(s)")
check("duplicate caught", len(report.duplicates) == 1, f"{len(report.duplicates)} row(s)")
check("valid rows kept", len(report.valid) > 50, f"{len(report.valid)} importable")

print("\n[2] Import")
for row in report.valid.itertuples():
    db.execute(
        "INSERT OR IGNORE INTO customers (name, email, product, extra) VALUES (?, ?, ?, ?)",
        (row.name, row.email, row.product, row._4),
    )
imported = db.query_one("SELECT COUNT(*) AS n FROM customers")["n"]
check("customers imported", imported == len(report.valid), f"{imported} rows")

print("\n[3] Outreach — unique link per customer")
campaign_id = db.execute(
    "INSERT INTO campaigns (name, subject, body) VALUES (?, ?, ?)",
    ("Test", mailer.DEFAULT_SUBJECT, mailer.DEFAULT_BODY),
)
customers = db.query("SELECT * FROM customers")
for c in customers:
    token = mailer.new_token()
    invite_id = db.execute(
        "INSERT INTO invites (campaign_id, customer_id, token, status, sent_at) "
        "VALUES (?, ?, ?, 'invited', datetime('now'))",
        (campaign_id, c["id"], token),
    )
    values = {"name": c["name"], "product": c["product"], "company": "Test Co",
              "reward": rewards.REWARD_LABEL,
              "link": mailer.feedback_link("http://localhost:8501", token)}
    oid = mailer.queue_email(
        c["email"], mailer.render(mailer.DEFAULT_SUBJECT, **values),
        mailer.render(mailer.DEFAULT_BODY, **values), "invite",
    )
    mailer.deliver(oid, cfg)

tokens = db.query("SELECT token FROM invites")
check("one invite per customer", len(tokens) == imported, f"{len(tokens)} invites")
check("all tokens unique", len({t["token"] for t in tokens}) == len(tokens))
sample_mail = db.query_one("SELECT body FROM outbox WHERE kind='invite' LIMIT 1")
check("email personalised with link", "?token=" in sample_mail["body"])
check("no unfilled placeholders", "{name}" not in sample_mail["body"] and "{product}" not in sample_mail["body"])

print("\n[4] Conversation — resume, probing, completion")
invite = db.query("SELECT * FROM invites LIMIT 1")[0]
session = agent.load_session(invite["token"])
check("session loads from token", session is not None and session.question_idx == 0)

check("low-signal answer detected", agent.is_low_signal("ok"))
check("specific short answer accepted", not agent.is_low_signal("battery dies fast"))

product = session.customer["product"]
replies = [
    "Honestly it's been excellent, I use it every single day and it just works.",
    "The battery life is brilliant — easily four days between charges.",
    "The app is slow and buggy, it crashes whenever I open the history tab.",
    "I'd fix the app performance first, and add a proper dark mode.",
    "Yes I'd recommend it to a friend, the hardware is genuinely great value.",
]
for i, reply in enumerate(replies[:2]):
    agent.record_answer(invite["id"], i, agent.question_text(i, product), reply, False)

resumed = agent.load_session(invite["token"])
check("abandoned session resumes at right question", resumed.question_idx == 2, f"idx={resumed.question_idx}")
check("not complete yet", not agent.is_complete(invite["id"]))

for i, reply in enumerate(replies[2:], start=2):
    agent.record_answer(invite["id"], i, agent.question_text(i, product), reply, False)
check("complete after all questions", agent.is_complete(invite["id"]))
agent.complete(invite["id"])

print("\n[5] Reward — exactly one per completed response")
r1, s1 = rewards.issue(invite["id"], "Test Co", cfg)
r2, s2 = rewards.issue(invite["id"], "Test Co", cfg)
check("first issue succeeds", s1 == "issued" and r1 is not None, f"code={r1['code'] if r1 else None}")
check("second issue refused", s2 == "already")
check("same code returned", r1["code"] == r2["code"])
count = db.query_one("SELECT COUNT(*) AS n FROM rewards WHERE invite_id = ?", (invite["id"],))["n"]
check("exactly one reward row", count == 1, f"{count} row(s)")
check("reward is Rs 1,000", r1["amount"] == 1000 and r1["currency"] == "INR")

incomplete = db.query("SELECT * FROM invites WHERE id != ? LIMIT 1", (invite["id"],))[0]
r3, s3 = rewards.issue(incomplete["id"], "Test Co", cfg)
check("incomplete response gets no reward", s3 == "incomplete" and r3 is None)

print("\n[6] Analysis")
result = analysis.analyse_invite(invite["id"], product, force=True)
check("sentiment assigned", result["sentiment"] in {"positive", "mixed", "negative"}, result["sentiment"])
check("score in range", 0.0 <= result["score"] <= 1.0, f"{result['score']:.2f}")
check("themes extracted", len(result["themes"]) > 0, str(result["themes"][:3]))

neg, neg_label = analysis.score_text("terrible, broken, I want a refund, worst purchase ever")
pos, pos_label = analysis.score_text("absolutely love it, works perfectly, brilliant value")
check("negative text scores low", neg_label == "negative", f"{neg:.2f}")
check("positive text scores high", pos_label == "positive", f"{pos:.2f}")
check("negation handled", analysis.score_text("not good at all")[0] < 0.5)
check("churn language flags attention", analysis.flags_attention("I want a refund", 0.6))

print("\n[7] Dashboard metrics")
f = analysis.funnel()
check("funnel counts invited", f["invited"] == imported, str(f))
check("funnel counts completed", f["completed"] == 1, str(f))
metrics = analysis.headline_metrics()
check("six headline metrics", len(metrics) == 6, str([m["label"] for m in metrics]))
check("every metric has value+caption", all(m["value"] and m["caption"] for m in metrics))
check("by-product table builds", len(analysis.by_product()) > 0)
check("recommend split builds", sum(analysis.recommend_split().values()) == 1)

print("\n[8] HTML rendering — the </div> leak the reference screenshot shows")
from views import ui
html_out = ui.kpi_cards(metrics)
check("balanced div tags", html_out.count("<div") == html_out.count("</div>"),
      f"{html_out.count('<div')} open / {html_out.count('</div>')} close")
check("no stray closing tag as text", "&lt;/div&gt;" not in html_out)
funnel_html = ui.funnel_panel([("Invited", 10), ("Completed", 4)])
check("funnel html balanced", funnel_html.count("<div") == funnel_html.count("</div>"))
sent_html = ui.sentiment_panel(analysis.sentiment_counts())
check("sentiment html balanced", sent_html.count("<div") == sent_html.count("</div>"))
check("sentiment labelled, not colour-only", "Positive" in sent_html and "Negative" in sent_html)
risk_html = ui.risk_list(analysis.attention_queue(5))
check("risk list html balanced", risk_html.count("<div") == risk_html.count("</div>"))
check("user text is escaped", "<script>" not in ui.esc("<script>alert(1)</script>"))

print(f"\n{'=' * 52}\n  {ok} passed, {fail} failed\n{'=' * 52}")
sys.exit(1 if fail else 0)
