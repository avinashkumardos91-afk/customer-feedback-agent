"""Turning open-ended answers into something measurable.

Two layers: a deterministic lexicon scorer that always runs, and an optional
Claude pass that replaces it when a key is configured. Results are cached per
invite, so opening the dashboard never re-scores or re-bills work already done.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from core import db, llm

POSITIVE = {
    "love", "loved", "great", "excellent", "brilliant", "perfect", "amazing",
    "fantastic", "happy", "delighted", "impressed", "smooth", "reliable",
    "easy", "intuitive", "fast", "quick", "beautiful", "solid", "worth",
    "recommend", "recommended", "best", "good", "nice", "helpful", "useful",
    "responsive", "durable", "comfortable", "value", "exceeded", "outstanding",
}

NEGATIVE = {
    "hate", "hated", "terrible", "awful", "horrible", "poor", "bad", "worst",
    "broken", "broke", "faulty", "defective", "slow", "laggy", "buggy", "bug",
    "crash", "crashes", "crashed", "confusing", "difficult", "hard", "clunky",
    "expensive", "overpriced", "disappointed", "disappointing", "frustrating",
    "frustrated", "annoying", "useless", "waste", "refund", "return", "cancel",
    "cancelled", "delay", "delayed", "late", "missing", "damaged", "unreliable",
    "unresponsive", "ignored", "rude", "slow", "fails", "failed", "failure",
}

INTENSIFIERS = {"very", "really", "extremely", "so", "incredibly", "totally"}
NEGATORS = {"not", "no", "never", "isn't", "wasn't", "didn't", "doesn't", "don't", "cant", "can't"}

# Churn language: a single hit flags the response for the owner regardless of
# the overall score, because "I'm cancelling" buried in otherwise mild feedback
# is exactly the row that must not be averaged away.
CHURN_SIGNALS = {
    "refund", "return it", "returning", "cancel", "cancelled", "cancelling",
    "switch", "switching", "competitor", "never again", "waste of money",
    "stopped using", "gave up", "unusable", "last time",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "so", "very",
    "really", "just", "it", "its", "it's", "is", "was", "были", "be", "been",
    "am", "are", "were", "i", "me", "my", "we", "our", "you", "your", "they",
    "them", "this", "that", "these", "those", "of", "to", "in", "on", "for",
    "with", "at", "by", "from", "as", "have", "has", "had", "do", "does",
    "did", "would", "could", "should", "will", "can", "get", "got", "make",
    "made", "one", "thing", "things", "like", "also", "much", "more", "most",
    "some", "any", "all", "not", "no", "yes", "there", "here", "when", "what",
    "which", "who", "how", "why", "about", "up", "out", "down", "over", "too",
    "product", "bit", "lot", "well", "still", "even", "quite", "far", "sure",
    # Adverbs and filler that survive frequency ranking but name nothing.
    # Without these, "honestly" and "every single" outrank "battery life".
    "honestly", "genuinely", "actually", "definitely", "probably", "basically",
    "literally", "totally", "absolutely", "completely", "simply", "certainly",
    "every", "single", "day", "days", "time", "times", "way", "ways", "want",
    "wanted", "need", "needed", "use", "used", "using", "say", "said", "think",
    "thought", "feel", "felt", "know", "knew", "seem", "seems", "looks",
    "going", "gone", "come", "came", "take", "taken", "give", "given", "put",
    "first", "last", "next", "back", "good", "bad", "better", "worse", "best",
    "worst", "big", "small", "new", "old", "long", "short", "high", "low",
}

# Contractions and possessives are never useful theme labels ("i'd", "it's").
_CONTRACTION = re.compile(r"'")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def score_text(text: str) -> tuple[float, str]:
    """Lexicon sentiment with negation and intensifier handling.

    Returns (score in 0..1, label). This is a heuristic, and the dashboard
    labels it as such — it exists so the product works with no API key, not to
    claim parity with a model.
    """
    words = _tokens(text)
    if not words:
        return 0.5, "mixed"

    total = 0.0
    for i, word in enumerate(words):
        weight = 0.0
        if word in POSITIVE:
            weight = 1.0
        elif word in NEGATIVE:
            weight = -1.0
        if weight == 0.0:
            continue
        if i and words[i - 1] in INTENSIFIERS:
            weight *= 1.5
        if any(w in NEGATORS for w in words[max(0, i - 3):i]):
            weight *= -1.0
        total += weight

    # Squash to 0..1; ±4 net polarity words is treated as saturated.
    score = max(0.0, min(1.0, 0.5 + total / 8.0))
    if score >= 0.62:
        label = "positive"
    elif score <= 0.38:
        label = "negative"
    else:
        label = "mixed"
    return score, label


def extract_themes(text: str, limit: int = 4) -> list[str]:
    """Frequent bigrams and nouns, minus stopwords.

    Bigrams first because "battery life" and "delivery delay" carry far more
    meaning to an owner than "battery" and "delivery" on their own.
    """
    words = [
        w for w in _tokens(text)
        if w not in STOPWORDS and len(w) > 2 and not _CONTRACTION.search(w)
    ]
    if not words:
        return []

    # Bigrams only from words that were adjacent in the original text — pairing
    # words that had stopwords between them invents phrases nobody wrote.
    raw = _tokens(text)
    keep = set(words)
    bigrams = [
        f"{a} {b}" for a, b in zip(raw, raw[1:]) if a in keep and b in keep
    ]
    counts = Counter(bigrams)
    themes = [phrase for phrase, n in counts.most_common(limit) if n > 1]

    # Within a single response a phrase rarely repeats, so requiring n > 1
    # would throw away every bigram and leave "battery" and "life" as two
    # unrelated themes. Allow the two strongest single-occurrence phrases,
    # ranked by how often their component words appear overall.
    if not themes and bigrams:
        freq = Counter(words)
        ranked = sorted(
            set(bigrams),
            key=lambda p: -sum(freq[w] for w in p.split()),
        )
        themes = ranked[:2]

    if len(themes) < limit:
        used = set(" ".join(themes).split())
        for word, n in Counter(words).most_common(limit * 4):
            if word in used:
                continue
            themes.append(word)
            used.add(word)
            if len(themes) >= limit:
                break
    return themes[:limit]


def flags_attention(text: str, score: float) -> bool:
    lowered = text.lower()
    if any(signal in lowered for signal in CHURN_SIGNALS):
        return True
    return score <= 0.35


def analyse_invite(invite_id: int, product: str, force: bool = False) -> dict:
    """Score one completed response, using the cache unless forced."""
    if not force:
        cached = db.query_one(
            "SELECT * FROM insights WHERE invite_id = ?", (invite_id,)
        )
        if cached:
            return {
                "sentiment": cached["sentiment"],
                "score": cached["score"],
                "themes": json.loads(cached["themes"]),
                "summary": cached["summary"],
                "needs_attention": bool(cached["needs_attention"]),
            }

    rows = db.query(
        "SELECT answer FROM answers WHERE invite_id = ? ORDER BY question_idx",
        (invite_id,),
    )
    text = " ".join(r["answer"] for r in rows).strip()
    if not text:
        return {
            "sentiment": "mixed", "score": 0.5, "themes": [],
            "summary": "No answers recorded.", "needs_attention": False,
        }

    from core import agent

    result = llm.analyse(product, agent.transcript(invite_id))
    if result is None:
        score, sentiment = score_text(text)
        result = {
            "sentiment": sentiment,
            "score": score,
            "themes": extract_themes(text),
            "summary": (text[:160] + "…") if len(text) > 160 else text,
            "needs_attention": flags_attention(text, score),
        }
    else:
        # Churn language overrides a cheerful model score — the owner would
        # rather see a false positive here than miss a leaving customer.
        result["needs_attention"] = result["needs_attention"] or any(
            s in text.lower() for s in CHURN_SIGNALS
        )

    db.execute(
        """INSERT INTO insights
               (invite_id, sentiment, score, themes, summary, needs_attention)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (invite_id) DO UPDATE SET
               sentiment = excluded.sentiment, score = excluded.score,
               themes = excluded.themes, summary = excluded.summary,
               needs_attention = excluded.needs_attention""",
        (
            invite_id, result["sentiment"], result["score"],
            json.dumps(result["themes"]), result["summary"],
            int(result["needs_attention"]),
        ),
    )
    return result


def analyse_pending() -> int:
    """Score every completed response that has no cached insight yet."""
    rows = db.query(
        """SELECT i.id, c.product
             FROM invites i
             JOIN customers c ON c.id = i.customer_id
        LEFT JOIN insights n ON n.invite_id = i.id
            WHERE i.status = 'completed' AND n.invite_id IS NULL"""
    )
    for row in rows:
        analyse_invite(row["id"], row["product"])
    return len(rows)


def funnel() -> dict[str, int]:
    row = db.query_one(
        """SELECT
               COUNT(*)                                                   AS invited,
               SUM(CASE WHEN status IN ('opened','in_progress','completed') THEN 1 ELSE 0 END) AS opened,
               SUM(CASE WHEN status IN ('in_progress','completed') THEN 1 ELSE 0 END)          AS started,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)      AS completed
           FROM invites"""
    )
    return {
        "invited": row["invited"] or 0,
        "opened": row["opened"] or 0,
        "started": row["started"] or 0,
        "completed": row["completed"] or 0,
    }


def sentiment_counts() -> dict[str, int]:
    rows = db.query("SELECT sentiment, COUNT(*) AS n FROM insights GROUP BY sentiment")
    counts = {"positive": 0, "mixed": 0, "negative": 0}
    for row in rows:
        counts[row["sentiment"]] = row["n"]
    return counts


def by_product() -> list[dict]:
    rows = db.query(
        """SELECT c.product                                            AS product,
                  COUNT(DISTINCT i.id)                                 AS invited,
                  SUM(CASE WHEN i.status = 'completed' THEN 1 ELSE 0 END) AS responses,
                  AVG(n.score)                                         AS avg_score,
                  SUM(COALESCE(n.needs_attention, 0))                  AS attention
             FROM invites i
             JOIN customers c ON c.id = i.customer_id
        LEFT JOIN insights n ON n.invite_id = i.id
         GROUP BY c.product
         ORDER BY responses DESC, product"""
    )
    return [
        {
            "Product": r["product"],
            "Invited": r["invited"],
            "Responses": r["responses"] or 0,
            "Response rate": (
                f"{(r['responses'] or 0) / r['invited'] * 100:.0f}%"
                if r["invited"] else "—"
            ),
            "Avg sentiment": (
                f"{r['avg_score'] * 100:.0f}%" if r["avg_score"] is not None else "—"
            ),
            "Needs attention": r["attention"] or 0,
        }
        for r in rows
    ]


def top_themes(limit: int = 10) -> list[tuple[str, int]]:
    rows = db.query("SELECT themes FROM insights")
    counter: Counter[str] = Counter()
    for row in rows:
        for theme in json.loads(row["themes"]):
            counter[theme] += 1
    return counter.most_common(limit)


def average_score() -> float | None:
    row = db.query_one("SELECT AVG(score) AS s FROM insights")
    return row["s"] if row and row["s"] is not None else None


def recommend_split() -> dict[str, int]:
    """Promoters vs detractors, read off the final "would you recommend"
    question rather than the whole transcript.

    Scoring the recommendation answer on its own is the point: a customer can
    be lukewarm about a feature and still advocate for the product, and mixing
    those signals together hides exactly that.
    """
    from core import agent  # local import: agent imports analysis at call time

    rows = db.query(
        """SELECT a.answer
             FROM answers a
             JOIN invites i ON i.id = a.invite_id
            WHERE a.question_idx = ? AND i.status = 'completed'""",
        (agent.total_questions() - 1,),
    )
    split = {"promoters": 0, "passives": 0, "detractors": 0}
    for row in rows:
        score, _ = score_text(row["answer"])
        if score >= 0.62:
            split["promoters"] += 1
        elif score <= 0.38:
            split["detractors"] += 1
        else:
            split["passives"] += 1
    return split


def top_complaint() -> tuple[str, int] | None:
    """The theme that recurs most across negative and mixed responses.

    Themes from positive responses are excluded — "great battery life" and
    "battery life dies" would otherwise cancel into one meaningless label.
    """
    rows = db.query(
        "SELECT themes FROM insights WHERE sentiment IN ('negative', 'mixed')"
    )
    counter: Counter[str] = Counter()
    for row in rows:
        for theme in json.loads(row["themes"]):
            counter[theme] += 1
    return counter.most_common(1)[0] if counter else None


def weakest_product(min_responses: int = 3) -> dict | None:
    """Lowest-scoring product with enough responses to mean anything.

    The floor matters: one grumpy review on a product with a single response
    would otherwise permanently own this slot.
    """
    row = db.query_one(
        """SELECT c.product AS product, AVG(n.score) AS avg_score, COUNT(*) AS n
             FROM insights n
             JOIN invites   i ON i.id = n.invite_id
             JOIN customers c ON c.id = i.customer_id
         GROUP BY c.product
           HAVING COUNT(*) >= ?
         ORDER BY avg_score ASC
            LIMIT 1""",
        (min_responses,),
    )
    if row is None:
        return None
    return {"product": row["product"], "score": row["avg_score"], "responses": row["n"]}


def reward_spend() -> tuple[int, int]:
    """(count, total amount) — what this campaign has actually cost."""
    row = db.query_one("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM rewards")
    return (row["n"] or 0, row["total"] or 0)


def headline_metrics() -> list[dict]:
    """The six numbers an owner should be able to act on in ten seconds.

    Each one either states the health of the programme or points at a
    decision; counts that can be derived from another tile are left out.
    """
    f = funnel()
    counts = sentiment_counts()
    responses = f["completed"]
    avg = average_score()
    attention = len(attention_queue(limit=10_000))
    complaint = top_complaint()
    weakest = weakest_product()
    reward_count, reward_total = reward_spend()

    negative_rate = (counts["negative"] / responses * 100) if responses else 0.0

    return [
        {
            "label": "Response rate",
            "value": f"{responses / f['invited'] * 100:.0f}%" if f["invited"] else "—",
            "caption": f"{responses:,} of {f['invited']:,} invited replied",
            "tone": "good" if f["invited"] and responses / f["invited"] >= 0.3 else "warn",
        },
        {
            "label": "Average sentiment",
            "value": f"{avg * 100:.0f}%" if avg is not None else "—",
            "caption": f"{counts['positive']} positive · {counts['mixed']} mixed · {counts['negative']} negative",
            "tone": "good" if (avg or 0) >= 0.6 else "warn" if (avg or 0) >= 0.4 else "bad",
        },
        {
            "label": "Needs attention",
            "value": f"{attention:,}",
            "caption": "at risk of leaving or reporting a serious problem",
            "tone": "bad" if attention else "good",
        },
        {
            "label": "Negative responses",
            "value": f"{negative_rate:.0f}%",
            "caption": f"{counts['negative']:,} of {responses:,} responses",
            "tone": "good" if negative_rate < 10 else "warn" if negative_rate < 25 else "bad",
        },
        {
            "label": "Top complaint",
            "value": complaint[0].title() if complaint else "—",
            "caption": f"raised in {complaint[1]} responses" if complaint else "nothing recurring yet",
            "tone": "warn" if complaint else "good",
        },
        {
            "label": "Weakest product",
            "value": weakest["product"] if weakest else "—",
            "caption": (
                f"{weakest['score'] * 100:.0f}% sentiment across {weakest['responses']} responses"
                if weakest else "not enough responses yet"
            ),
            "tone": "bad" if weakest and weakest["score"] < 0.4 else "warn" if weakest else "good",
        },
    ]


def attention_queue(limit: int = 25) -> list[db.sqlite3.Row]:
    return db.query(
        """SELECT c.name, c.email, c.product, n.score, n.summary, n.sentiment,
                  i.completed_at, i.id AS invite_id
             FROM insights n
             JOIN invites   i ON i.id = n.invite_id
             JOIN customers c ON c.id = i.customer_id
            WHERE n.needs_attention = 1
         ORDER BY n.score ASC, i.completed_at DESC
            LIMIT ?""",
        (limit,),
    )
