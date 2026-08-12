"""What the customer sees when they click the link in their email."""
from __future__ import annotations

import streamlit as st

from core import agent, analysis, db, mailer, rewards


def _company() -> str:
    return st.session_state.get("company", "Your Company")


def render(token: str) -> None:
    session = agent.load_session(token)

    if session is None:
        st.error("This feedback link isn't valid.")
        st.caption(
            "It may have been mistyped or truncated by an email client. "
            "Copying the whole link from the email usually fixes it."
        )
        return

    name = session.customer["name"]
    product = session.customer["product"]
    st.title(f"Your feedback on {product}")

    # A second visit after finishing must not create a second response — and
    # therefore must not create a second gift card.
    if session.completed:
        reward = rewards.existing(session.invite["id"])
        st.success(f"Thanks {name} — you've already completed this one.")
        if reward:
            st.markdown(
                f"Your **{rewards.REWARD_LABEL}** gift card is "
                f"`{reward['code']}` — we emailed it to "
                f"{session.customer['email']} too."
            )
        st.caption("There's nothing more to do here.")
        with st.expander("See what you told us"):
            for row in session.answers:
                st.markdown(f"**{row['question']}**")
                st.write(row["answer"])
        return

    agent.mark_opened(session.invite["id"])

    key = f"chat::{token}"
    state = st.session_state.setdefault(
        key, {"probe_used": {}, "pending_probe": None, "greeted": False}
    )

    total = agent.total_questions()
    answered = len(session.answers)

    # Replay whatever already exists, so returning to a half-finished
    # conversation shows the thread rather than starting over.
    st.chat_message("assistant").write(agent.greeting(name, product))
    for row in session.answers:
        st.chat_message("assistant").write(row["question"])
        st.chat_message("user").write(row["answer"])

    if answered:
        st.progress(answered / total, text=f"{answered} of {total} answered")

    idx = answered
    if idx >= total:
        _finish(session, product)
        return

    pending_probe = state.get("pending_probe")
    prompt_text = pending_probe or agent.question_text(idx, product)
    st.chat_message("assistant").write(prompt_text)

    reply = st.chat_input("Type your answer…")
    if not reply:
        return

    reply = reply.strip()
    if not reply:
        return

    question = agent.question_text(idx, product)

    # Probe at most once per question: a second nudge on the same question
    # reads as badgering, and someone who has nothing to add never escapes it.
    if (
        agent.is_low_signal(reply)
        and not state["probe_used"].get(str(idx))
        and pending_probe is None
    ):
        state["probe_used"][str(idx)] = True
        state["pending_probe"] = agent.probe_question(product, question, reply)
        st.session_state[key] = state
        st.rerun()

    answer = reply
    if pending_probe:
        # Keep both halves — the follow-up answer alone often loses the subject.
        prior = state.get("probe_context", "")
        answer = f"{prior} {reply}".strip() if prior else reply
        state["pending_probe"] = None

    agent.record_answer(
        session.invite["id"], idx, question, answer,
        probed=bool(state["probe_used"].get(str(idx))),
    )
    st.session_state[key] = state
    st.rerun()


def _finish(session, product: str) -> None:
    invite_id = session.invite["id"]

    if not agent.is_complete(invite_id):
        st.warning("Something's missing — please answer the remaining questions.")
        return

    agent.complete(invite_id)
    analysis.analyse_invite(invite_id, product, force=True)

    reward, status = rewards.issue(
        invite_id, _company(), mailer.SMTPConfig.from_env()
    )

    st.chat_message("assistant").write(
        f"That's everything — thank you. Your answers go straight to the team "
        f"that works on {product}."
    )
    st.balloons()

    if reward is not None:
        st.success(
            f"Your {rewards.REWARD_LABEL} gift card: `{reward['code']}`"
        )
        if status == "issued":
            st.caption(f"We've emailed a copy to {session.customer['email']}.")
        else:
            st.caption("This is the same card we issued earlier — one per response.")
    else:
        st.info("We'll email your gift card shortly.")
