"""The company owner's side of the application."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import agent, analysis, db, ingest, mailer, rewards
from views import ui


def _settings() -> tuple[str, str, mailer.SMTPConfig]:
    company = st.session_state.setdefault("company", "Your Company")
    base_url = st.session_state.setdefault("base_url", "http://localhost:8501")
    return company, base_url, mailer.SMTPConfig.from_env()


def render() -> None:
    company, base_url, cfg = _settings()
    st.markdown(ui.CSS, unsafe_allow_html=True)

    with st.sidebar:
        st.subheader("Settings")
        st.session_state["company"] = st.text_input("Company name", value=company)
        st.session_state["base_url"] = st.text_input(
            "App base URL", value=base_url,
            help="Used to build each customer's unique feedback link.",
        )
        st.divider()
        st.caption("**Email**")
        if cfg.configured:
            st.success(f"SMTP ready — {cfg.host}")
        else:
            st.info(
                "Simulation mode. Every email is recorded in the Outbox tab "
                "instead of being sent. Set SMTP_HOST and SMTP_SENDER to send "
                "for real."
            )
        st.caption("**Feedback analysis**")
        from core import llm
        if llm.available():
            st.success(f"Claude connected — {llm.MODEL}")
        else:
            st.info(
                "Using the built-in scorer. Set ANTHROPIC_API_KEY for "
                "model-quality summaries, probing and themes."
            )

    tabs = st.tabs(
        ["Dashboard", "Customers", "Outreach", "Responses", "Outbox"]
    )
    with tabs[0]:
        _dashboard(company, cfg)
    with tabs[1]:
        _customers()
    with tabs[2]:
        _outreach(company, cfg)
    with tabs[3]:
        _responses()
    with tabs[4]:
        _outbox(cfg)


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
def _dashboard(company: str, cfg: mailer.SMTPConfig) -> None:
    from core import llm

    scored = analysis.analyse_pending()
    if scored:
        st.toast(f"Scored {scored} new response(s).")

    if not cfg.configured or not llm.available():
        parts = []
        if not cfg.configured:
            parts.append("simulated email")
        if not llm.available():
            parts.append("built-in scoring")
        st.markdown(
            f'<div class="fa-wrap"><div class="fa-banner">Demo mode — '
            f'{" &amp; ".join(parts)}</div></div>',
            unsafe_allow_html=True,
        )

    total_customers = db.query_one("SELECT COUNT(*) AS n FROM customers")["n"]
    if not total_customers:
        st.subheader("Overview")
        st.info(
            "No customers yet. Upload your customer sheet in the **Customers** "
            "tab to get started."
        )
        return

    st.subheader("Overview")
    st.caption(f"How {company}'s products are performing, according to customers.")

    st.markdown(ui.kpi_cards(analysis.headline_metrics()), unsafe_allow_html=True)

    f = analysis.funnel()
    left, right = st.columns(2)
    with left:
        st.markdown(
            ui.funnel_panel([
                ("Invited", f["invited"]),
                ("Opened", f["opened"]),
                ("Started", f["started"]),
                ("Completed", f["completed"]),
            ]),
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            ui.sentiment_panel(analysis.sentiment_counts()), unsafe_allow_html=True
        )

    st.write("")
    left2, right2 = st.columns(2)
    with left2:
        st.markdown(ui.themes_panel(analysis.top_themes(8)), unsafe_allow_html=True)
    with right2:
        split = analysis.recommend_split()
        total = sum(split.values())
        st.markdown(
            ui.sentiment_panel({
                "positive": split["promoters"],
                "mixed": split["passives"],
                "negative": split["detractors"],
            }).replace(
                "Sentiment distribution", "Would they recommend you?"
            ).replace(
                f"Across {total:,} scored responses",
                f"From the final question · {total:,} answered",
            ),
            unsafe_allow_html=True,
        )

    st.write("")
    st.markdown("#### Needs attention")
    st.caption("Customers at risk of leaving, or reporting a serious problem.")
    st.markdown(ui.risk_list(analysis.attention_queue(10)), unsafe_allow_html=True)

    by_product = analysis.by_product()
    if by_product:
        st.write("")
        st.markdown("#### By product")
        st.dataframe(
            pd.DataFrame(by_product), use_container_width=True, hide_index=True
        )


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------
def _customers() -> None:
    st.subheader("Customers")

    uploaded = st.file_uploader(
        "Upload your customer sheet", type=["csv", "xlsx", "xls"],
        help="Any column layout — you'll confirm the mapping before importing.",
    )

    if uploaded is not None:
        try:
            df = ingest.read_table(uploaded)
        except Exception as exc:
            st.error(f"Could not read that file: {exc}")
            return

        if df.empty:
            st.warning("That sheet has no rows.")
            return

        st.caption(f"Read {len(df):,} rows. Confirm which column is which:")
        guess = ingest.guess_mapping(list(df.columns))
        options = ["— not in this sheet —"] + list(df.columns)
        cols = st.columns(3)
        chosen: dict[str, str | None] = {}
        for i, field in enumerate(("name", "email", "product")):
            default = options.index(guess[field]) if guess[field] in options else 0
            with cols[i]:
                picked = st.selectbox(field.title(), options, index=default, key=f"map_{field}")
            chosen[field] = None if picked == options[0] else picked

        if not all(chosen.values()):
            st.warning("Pick a column for name, email and product to continue.")
            return

        try:
            report = ingest.validate(df, chosen)
        except Exception as exc:
            st.error(str(exc))
            return

        c1, c2 = st.columns(2)
        c1.metric("Ready to import", f"{len(report.valid):,}")
        c2.metric("Rows with problems", f"{report.problem_count:,}")

        if report.problem_count:
            with st.expander(f"Review {report.problem_count} row(s) that need attention"):
                for label, frame, note in (
                    ("Missing name", report.missing_name, "skipped — no one to address"),
                    ("Missing email", report.missing_email, "skipped — nowhere to send"),
                    ("Invalid email", report.bad_email, "skipped — would bounce"),
                    ("Missing product", report.missing_product, "skipped — nothing to ask about"),
                    ("Duplicate", report.duplicates, "first occurrence kept"),
                ):
                    if len(frame):
                        st.markdown(f"**{label}** ({len(frame)}) — _{note}_")
                        st.dataframe(
                            frame[["_row", "name", "email", "product"]].rename(
                                columns={"_row": "Sheet row"}
                            ),
                            use_container_width=True, hide_index=True,
                        )
            st.caption(
                "Fix these in your sheet and re-upload if you want them included — "
                "importing now brings in the valid rows only."
            )

        if len(report.valid) and st.button("Import these customers", type="primary"):
            added = updated = 0
            for row in report.valid.itertuples():
                existing = db.query_one(
                    "SELECT id FROM customers WHERE email = ? AND product = ?",
                    (row.email, row.product),
                )
                if existing:
                    db.execute(
                        "UPDATE customers SET name = ?, extra = ? WHERE id = ?",
                        (row.name, row._4, existing["id"]),
                    )
                    updated += 1
                else:
                    db.execute(
                        "INSERT INTO customers (name, email, product, extra) VALUES (?, ?, ?, ?)",
                        (row.name, row.email, row.product, row._4),
                    )
                    added += 1
            st.success(f"Imported {added:,} new and updated {updated:,} existing.")
            st.rerun()

    st.divider()
    rows = db.query(
        """SELECT c.id, c.name, c.email, c.product,
                  COUNT(i.id)                                              AS invites,
                  SUM(CASE WHEN i.status = 'completed' THEN 1 ELSE 0 END)  AS responded
             FROM customers c
        LEFT JOIN invites i ON i.customer_id = c.id
         GROUP BY c.id ORDER BY c.product, c.name"""
    )
    if not rows:
        st.info("No customers yet.")
        return

    frame = pd.DataFrame([dict(r) for r in rows])
    products = sorted(frame["product"].unique())
    picked = st.multiselect("Filter by product", products, default=[])
    if picked:
        frame = frame[frame["product"].isin(picked)]

    st.caption(f"{len(frame):,} customer(s)")
    st.dataframe(
        frame.rename(columns={
            "name": "Name", "email": "Email", "product": "Product",
            "invites": "Invites", "responded": "Responded",
        }).drop(columns=["id"]),
        use_container_width=True, hide_index=True,
    )


# --------------------------------------------------------------------------
# Outreach
# --------------------------------------------------------------------------
def _outreach(company: str, cfg: mailer.SMTPConfig) -> None:
    st.subheader("Outreach")
    customers = db.query("SELECT * FROM customers ORDER BY product, name")
    if not customers:
        st.info("Import customers first.")
        return

    products = sorted({c["product"] for c in customers})
    picked = st.multiselect(
        "Send to which products?", products, default=products,
        help="Reach a segment rather than everyone.",
    )
    skip_sent = st.checkbox("Skip customers already invited in this campaign", value=True)
    recipients = [c for c in customers if c["product"] in picked]
    st.caption(f"{len(recipients):,} customer(s) selected")

    st.markdown("**Email** — edit before sending. "
                "`{name}`, `{product}`, `{link}`, `{company}` and `{reward}` are filled in per customer.")
    subject = st.text_input("Subject", value=mailer.DEFAULT_SUBJECT)
    body = st.text_area("Body", value=mailer.DEFAULT_BODY, height=260)

    if recipients:
        sample = recipients[0]
        with st.expander(f"Preview as {sample['name']} ({sample['product']})"):
            preview = {
                "name": sample["name"], "product": sample["product"],
                "company": company, "reward": rewards.REWARD_LABEL,
                "link": mailer.feedback_link(
                    st.session_state["base_url"], "EXAMPLE-TOKEN"
                ),
            }
            st.text(mailer.render(subject, **preview))
            st.divider()
            st.text(mailer.render(body, **preview))

    if st.button("Send to selected customers", type="primary", disabled=not recipients):
        campaign_id = db.execute(
            "INSERT INTO campaigns (name, subject, body) VALUES (?, ?, ?)",
            (f"Campaign · {len(recipients)} recipients", subject, body),
        )
        sent = skipped = failed = 0
        progress = st.progress(0.0)

        for n, customer in enumerate(recipients, start=1):
            if skip_sent:
                prior = db.query_one(
                    "SELECT 1 FROM invites WHERE customer_id = ? LIMIT 1",
                    (customer["id"],),
                )
                if prior:
                    skipped += 1
                    progress.progress(n / len(recipients))
                    continue

            token = mailer.new_token()
            invite_id = db.execute(
                """INSERT INTO invites (campaign_id, customer_id, token, status, sent_at)
                   VALUES (?, ?, ?, 'invited', datetime('now'))""",
                (campaign_id, customer["id"], token),
            )
            values = {
                "name": customer["name"], "product": customer["product"],
                "company": company, "reward": rewards.REWARD_LABEL,
                "link": mailer.feedback_link(st.session_state["base_url"], token),
            }
            outbox_id = mailer.queue_email(
                customer["email"],
                mailer.render(subject, **values),
                mailer.render(body, **values),
                kind="invite",
            )
            delivered, error = mailer.deliver(outbox_id, cfg)
            if delivered:
                sent += 1
            elif error:
                failed += 1
            else:
                sent += 1  # simulation mode: recorded, not delivered
            progress.progress(n / len(recipients))

        progress.empty()
        if cfg.configured:
            st.success(f"Sent {sent:,} · skipped {skipped:,} · failed {failed:,}")
        else:
            st.success(
                f"Prepared {sent:,} invite(s) · skipped {skipped:,}. "
                "SMTP isn't configured, so open the **Outbox** tab to read each "
                "email and open the customer links."
            )
        if failed:
            st.warning("Some sends failed — see the Outbox tab for the error.")


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------
def _responses() -> None:
    st.subheader("Responses")
    rows = db.query(
        """SELECT i.id AS invite_id, c.name, c.email, c.product, i.status,
                  i.completed_at, n.sentiment, n.score, n.summary
             FROM invites i
             JOIN customers c ON c.id = i.customer_id
        LEFT JOIN insights n ON n.invite_id = i.id
            WHERE i.status IN ('in_progress', 'completed')
         ORDER BY i.completed_at DESC NULLS LAST, i.id DESC"""
    )
    if not rows:
        st.info("No one has started a feedback conversation yet.")
        return

    for row in rows:
        score = f"{row['score'] * 100:.0f}%" if row["score"] is not None else "—"
        label = row["sentiment"] or ("in progress" if row["status"] != "completed" else "unscored")
        with st.expander(f"{row['name']} · {row['product']} · {label} · {score}"):
            if row["summary"]:
                st.markdown(f"**Summary** — {row['summary']}")
            answers = db.query(
                "SELECT question, answer FROM answers WHERE invite_id = ? ORDER BY question_idx",
                (row["invite_id"],),
            )
            for a in answers:
                st.markdown(f"**{a['question']}**")
                st.write(a["answer"])
            reward = rewards.existing(row["invite_id"])
            if reward:
                st.success(f"Gift card issued — `{reward['code']}`")
            elif row["status"] == "completed":
                st.warning("Completed but no reward recorded.")
            else:
                st.caption(
                    f"Answered {len(answers)} of {agent.total_questions()} — "
                    "they can resume from their original link."
                )


# --------------------------------------------------------------------------
# Outbox
# --------------------------------------------------------------------------
def _outbox(cfg: mailer.SMTPConfig) -> None:
    st.subheader("Outbox")
    if not cfg.configured:
        st.info(
            "SMTP isn't configured, so nothing was actually delivered. Every "
            "email below is exactly what the customer would have received — "
            "open an invite's link to try the feedback conversation yourself."
        )

    kind = st.radio("Show", ["All", "Invites", "Rewards"], horizontal=True)
    clause = {"All": "", "Invites": "WHERE kind = 'invite'", "Rewards": "WHERE kind = 'reward'"}[kind]
    rows = db.query(f"SELECT * FROM outbox {clause} ORDER BY id DESC LIMIT 200")
    if not rows:
        st.info("Nothing here yet.")
        return

    for row in rows:
        status = "delivered" if row["delivered"] else (row["error"] or "not delivered")
        with st.expander(f"{row['to_email']} · {row['subject']} · {status}"):
            st.text(row["body"])
