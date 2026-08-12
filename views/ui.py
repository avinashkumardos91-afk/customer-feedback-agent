"""Shared presentation helpers.

Every helper returns ONE complete, balanced HTML string and is rendered with a
single `st.markdown(..., unsafe_allow_html=True)` call. Building markup across
several calls is what produces the classic Streamlit failure where a closing
`</div>` is printed to the page as literal text.
"""
from __future__ import annotations

import html

# Palette validated with the dataviz validator against a light surface:
#   node scripts/validate_palette.js "#048A5E,#B7791F,#B42318" --mode light
# All checks pass; CVD separation lands in the 6-8 warn band, which is legal
# only with secondary encoding — so every sentiment segment is direct-labelled
# with its name and count, never identified by colour alone.
GOOD = "#048A5E"
WARN = "#B7791F"
BAD = "#B42318"
ACCENT = "#3538CD"

SURFACE = "#FFFFFF"
CANVAS = "#F7F7F8"
BORDER = "#E4E4E7"
INK = "#18181B"
INK_SOFT = "#52525B"
INK_MUTED = "#8A8A93"

TONE_COLOR = {"good": GOOD, "warn": WARN, "bad": BAD, "accent": ACCENT}

CSS = f"""
<style>
  .fa-wrap {{ font-family: ui-sans-serif, -apple-system, "Segoe UI", sans-serif; }}
  .fa-banner {{
      background: #EEF0FF; color: {ACCENT}; border: 1px solid #D6D9FB;
      border-radius: 8px; padding: .6rem 1rem; text-align: center;
      font-size: .78rem; font-weight: 600; letter-spacing: .04em;
      text-transform: uppercase; margin-bottom: 1.25rem;
  }}
  .fa-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
      gap: 14px; margin: .25rem 0 1.75rem;
  }}
  .fa-card {{
      background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
      padding: 1.05rem 1.15rem; display: flex; flex-direction: column; gap: .3rem;
      border-top: 3px solid var(--tone);
  }}
  .fa-card .lbl {{
      font-size: .74rem; color: {INK_MUTED}; letter-spacing: .06em;
      text-transform: uppercase; font-weight: 600;
  }}
  .fa-card .val {{
      font-size: 1.95rem; font-weight: 700; color: {INK}; line-height: 1.1;
      font-variant-numeric: tabular-nums; overflow-wrap: anywhere;
  }}
  .fa-card .cap {{ font-size: .78rem; color: {INK_SOFT}; line-height: 1.4; }}

  .fa-panel {{
      background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
      padding: 1.15rem 1.25rem; height: 100%;
  }}
  .fa-panel h4 {{
      margin: 0 0 .2rem; font-size: .95rem; color: {INK}; font-weight: 650;
  }}
  .fa-panel .sub {{ font-size: .78rem; color: {INK_MUTED}; margin-bottom: 1rem; }}

  .fa-stage {{ display: flex; align-items: center; gap: .7rem; margin-bottom: .6rem; }}
  .fa-stage .nm {{ width: 84px; font-size: .8rem; color: {INK_SOFT}; flex: none; }}
  .fa-stage .track {{ flex: 1; background: {CANVAS}; border-radius: 4px; height: 26px; }}
  .fa-stage .fill {{
      height: 26px; border-radius: 4px; background: {ACCENT};
      display: flex; align-items: center; justify-content: flex-end;
      padding-right: .5rem; color: #fff; font-size: .76rem; font-weight: 600;
      font-variant-numeric: tabular-nums; min-width: 2.5rem;
  }}
  .fa-stage .pct {{
      width: 46px; text-align: right; font-size: .78rem; color: {INK_SOFT};
      font-variant-numeric: tabular-nums; flex: none;
  }}

  .fa-bar {{ display: flex; width: 100%; height: 30px; border-radius: 4px; overflow: hidden; gap: 2px; }}
  .fa-seg {{ height: 30px; }}
  .fa-key {{ display: flex; flex-wrap: wrap; gap: 1rem; margin-top: .85rem; }}
  .fa-key .item {{ display: flex; align-items: center; gap: .4rem; font-size: .8rem; color: {INK_SOFT}; }}
  .fa-key .dot {{ width: 10px; height: 10px; border-radius: 2px; flex: none; }}
  .fa-key .n {{ color: {INK}; font-weight: 600; font-variant-numeric: tabular-nums; }}

  .fa-theme {{ display: flex; align-items: center; gap: .7rem; margin-bottom: .5rem; }}
  .fa-theme .nm {{ width: 140px; font-size: .82rem; color: {INK}; flex: none;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .fa-theme .track {{ flex: 1; background: {CANVAS}; border-radius: 4px; height: 18px; }}
  .fa-theme .fill {{ height: 18px; border-radius: 4px; background: {ACCENT}; }}
  .fa-theme .n {{ width: 30px; text-align: right; font-size: .8rem; color: {INK_SOFT};
      font-variant-numeric: tabular-nums; flex: none; }}

  .fa-risk {{
      background: {SURFACE}; border: 1px solid {BORDER}; border-left: 3px solid {BAD};
      border-radius: 8px; padding: .8rem 1rem; margin-bottom: .6rem;
  }}
  .fa-risk .top {{ display: flex; justify-content: space-between; gap: 1rem;
      align-items: baseline; margin-bottom: .3rem; flex-wrap: wrap; }}
  .fa-risk .who {{ font-size: .9rem; font-weight: 650; color: {INK}; }}
  .fa-risk .meta {{ font-size: .76rem; color: {INK_MUTED}; }}
  .fa-risk .say {{ font-size: .84rem; color: {INK_SOFT}; line-height: 1.5; }}
  .fa-empty {{ color: {INK_MUTED}; font-size: .85rem; padding: .5rem 0; }}
</style>
"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def kpi_cards(metrics: list[dict]) -> str:
    cards = "".join(
        f'<div class="fa-card" style="--tone:{TONE_COLOR.get(m["tone"], ACCENT)}">'
        f'<div class="lbl">{esc(m["label"])}</div>'
        f'<div class="val">{esc(m["value"])}</div>'
        f'<div class="cap">{esc(m["caption"])}</div>'
        f"</div>"
        for m in metrics
    )
    return f'<div class="fa-wrap"><div class="fa-grid">{cards}</div></div>'


def funnel_panel(stages: list[tuple[str, int]]) -> str:
    """Horizontal stage bars rather than a tapered funnel.

    A funnel's width encodes nothing reliable — the eye reads its *area*, which
    is not proportional to the count. Bars on a shared baseline compare exactly.
    """
    top = max((n for _, n in stages), default=0) or 1
    rows = ""
    for name, count in stages:
        width = max(count / top * 100, 1.5)
        pct = f"{count / top * 100:.0f}%" if top else "—"
        rows += (
            f'<div class="fa-stage"><div class="nm">{esc(name)}</div>'
            f'<div class="track"><div class="fill" style="width:{width:.1f}%">{count:,}</div></div>'
            f'<div class="pct">{pct}</div></div>'
        )
    return (
        '<div class="fa-wrap"><div class="fa-panel"><h4>Response funnel</h4>'
        '<div class="sub">Where invited customers drop off</div>'
        f"{rows}</div></div>"
    )


def sentiment_panel(counts: dict[str, int]) -> str:
    """One stacked bar plus a labelled key.

    Each segment carries its name and count in the key, which is the secondary
    encoding the palette's CVD warn band requires — identity never rests on
    colour alone.
    """
    order = [("Positive", counts.get("positive", 0), GOOD),
             ("Mixed", counts.get("mixed", 0), WARN),
             ("Negative", counts.get("negative", 0), BAD)]
    total = sum(n for _, n, _ in order)

    if not total:
        body = '<div class="fa-empty">No responses scored yet.</div>'
    else:
        segments = "".join(
            f'<div class="fa-seg" style="width:{n / total * 100:.2f}%;background:{color}"'
            f' title="{esc(label)}: {n}"></div>'
            for label, n, color in order if n
        )
        key = "".join(
            f'<div class="item"><span class="dot" style="background:{color}"></span>'
            f'{esc(label)} <span class="n">{n:,}</span>'
            f'<span>({n / total * 100:.0f}%)</span></div>'
            for label, n, color in order
        )
        body = f'<div class="fa-bar">{segments}</div><div class="fa-key">{key}</div>'

    return (
        '<div class="fa-wrap"><div class="fa-panel"><h4>Sentiment distribution</h4>'
        f'<div class="sub">Across {total:,} scored responses</div>{body}</div></div>'
    )


def themes_panel(themes: list[tuple[str, int]]) -> str:
    if not themes:
        body = '<div class="fa-empty">Themes appear once responses are scored.</div>'
    else:
        top = themes[0][1] or 1
        body = "".join(
            f'<div class="fa-theme"><div class="nm" title="{esc(name)}">{esc(name)}</div>'
            f'<div class="track"><div class="fill" style="width:{n / top * 100:.1f}%"></div></div>'
            f'<div class="n">{n}</div></div>'
            for name, n in themes
        )
    return (
        '<div class="fa-wrap"><div class="fa-panel"><h4>What customers keep mentioning</h4>'
        '<div class="sub">Recurring themes, most frequent first</div>'
        f"{body}</div></div>"
    )


def risk_list(rows) -> str:
    if not rows:
        return (
            '<div class="fa-wrap"><div class="fa-empty">'
            "Nothing flagged — no customer is currently at risk.</div></div>"
        )
    items = "".join(
        f'<div class="fa-risk"><div class="top">'
        f'<span class="who">{esc(r["name"])}</span>'
        f'<span class="meta">{esc(r["product"])} · {esc(r["email"])} · '
        f'sentiment {r["score"] * 100:.0f}%</span></div>'
        f'<div class="say">{esc(r["summary"])}</div></div>'
        for r in rows
    )
    return f'<div class="fa-wrap">{items}</div>'
