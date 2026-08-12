# Automated Customer Feedback Collection & Insights Agent

A single Streamlit application. A company uploads its customer list, emails every
customer a unique link, collects a structured review through a conversational
agent, rewards each genuine completion with a ₹1,000 gift card, and reads the
result on a live dashboard.

Works for **any** product category — a book, a smart watch, a course, an
appliance, a SaaS plan. Nothing is hard-coded to an industry.

## Run it

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\streamlit run app.py
```

Open http://localhost:8501, go to **Customers**, and upload
`sample_customers.csv` to try it immediately.

```bash
python test_flow.py    # 45 checks over the whole workflow, no UI needed
```

## It runs with no credentials

Neither an SMTP server nor an API key is required.

| Missing | What happens instead |
|---|---|
| SMTP config | Every email is written to the **Outbox** tab exactly as the customer would receive it, links included. Open one to walk the customer's path yourself. |
| `ANTHROPIC_API_KEY` | A lexicon scorer handles sentiment, themes and probing. The dashboard labels this as demo mode. |

Set `SMTP_HOST` / `SMTP_SENDER` (plus `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_PORT`)
to send real mail, and `ANTHROPIC_API_KEY` to switch analysis to Claude
(`claude-opus-5`).

## How it works

```
app.py                 ?token=… → the customer's conversation, else the owner
core/db.py             SQLite schema and access
core/ingest.py         file reading, column mapping, validation, dedupe
core/mailer.py         templates, personalisation, SMTP + simulation
core/agent.py          the 5-question conversation, resume, probing
core/analysis.py       sentiment, themes, the dashboard's metrics
core/rewards.py        gift card issuance
core/llm.py            optional Claude layer; every call has a fallback
views/                 owner tabs, customer chat, presentation helpers
```

### The decisions worth defending

**Identity is (email, product), not email.** One person can buy two products and
owes you two separate reviews. Deduping on email alone would silently drop the
second.

**The token is the session.** A customer is identified only by the unguessable
token in their link — no login, no cookie. That is also what makes the link
work on a different device from the one that opened the email.

**Answers are written the moment they arrive.** Someone who abandons halfway and
returns next week resumes at the exact question they stopped on, because the
conversation's state was never in memory.

**"Completed" means every question has an answer** — not that the customer
reached the last screen. The reward hangs off that definition.

**One reward per response is enforced by the database.** `rewards.invite_id` is
`UNIQUE`, so a double-submit, a refresh, or two concurrent requests cannot mint a
second gift card even if the application logic is wrong. Reopening a finished
link shows the original code rather than issuing another.

**The agent probes at most once per question.** A vague answer earns one gentle
follow-up; a second would badger someone who genuinely has nothing to add.
Short-but-specific answers ("battery dies fast") are accepted as-is, because word
count is a poor proxy for usefulness.

**Insights are cached per response.** The dashboard never re-scores work already
done, so opening it repeatedly costs nothing and never re-bills an API call.

### Turning open text into something measurable

Every completed response gets a sentiment label and score, 1–4 themes, a one-line
summary, and an `needs_attention` flag. Claude does this when configured;
otherwise a lexicon scorer with negation and intensifier handling does.

Churn language ("refund", "cancelling", "switching") flags a response for the
owner **regardless of its score** — a leaving customer buried inside otherwise
mild feedback is exactly the row that must not be averaged away.

### The six numbers on the dashboard

Chosen so each one either states the health of the programme or points at a
decision. Counts derivable from another tile are left out — "rewards issued" is
just completions again, so it sits in the Responses tab instead.

| Metric | The decision it drives |
|---|---|
| Response rate | Is the outreach itself working? |
| Average sentiment | How is the product doing overall? |
| Needs attention | Who do I call today? |
| Negative responses | Is this a few loud voices or a real trend? |
| Top complaint | What is the one thing to fix? |
| Weakest product | Where do I spend the next sprint? |

"Weakest product" requires at least three responses, so a single grumpy review
cannot permanently own that slot.

### Charts

Built from plain HTML and CSS — no charting dependency. The stage bars share a
baseline rather than tapering as a funnel, because a funnel's width encodes
nothing the eye reads reliably.

The sentiment palette (`#048A5E` / `#B7791F` / `#B42318`) was checked with the
`dataviz` validator against the light chart surface: lightness band, chroma
floor, colour-blind separation, normal-vision separation and contrast all pass.
Its CVD separation lands in the warn band, which is only legal with secondary
encoding — so every segment carries its name and count in the key, and identity
never rests on colour alone. The charts paint their own light surface, so the
palette holds whichever Streamlit theme the viewer runs.

## Known limits

- Sending is sequential, so a very large list is slow; a real deployment would
  queue this rather than block the page.
- The lexicon scorer is English-only and is a heuristic, not a model. It exists
  so the product works with no API key.
- Bounces are not tracked — "invited" counts what was sent, not what arrived.
