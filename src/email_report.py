"""Email rendering + delivery. Design doc §5.

Renders the three email types (Sunday lineup, recap/FAAB, inactives alert) as
matched HTML + plaintext pairs — plaintext is not an afterthought, per the
doc: "you'll read this on a phone Sunday morning." Delivery is via Resend
(one POST, no SMTP quirks); the email credential is the only secret in the
whole system.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

from src.explain import Decision, format_decision_plaintext, format_delta, rank_decisions

load_dotenv()

RESEND_API_URL = "https://api.resend.com/emails"


class EmailSendError(RuntimeError):
    pass


@dataclass
class WeeklyLineupContext:
    week: int
    opponent_name: str
    mode: str
    projected: float
    opponent_projected: float
    win_prob: float  # 0-1
    p_playoffs: float
    p_bye: float
    p_title: float
    changes: list[Decision] = field(default_factory=list)
    forced_note: str = "Rest of lineup is forced — no live decisions this week."
    watch_items: list[str] = field(default_factory=list)  # pre-formatted warning lines
    mode_note: str = ""


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _pct1(x: float) -> str:
    return f"{x * 100:.1f}%"


def subject_line(prefix: str, ctx: WeeklyLineupContext) -> str:
    n_changes = len(ctx.changes)
    return (
        f"{prefix} Week {ctx.week} Lineup — {n_changes} change{'s' if n_changes != 1 else ''} · "
        f"{_pct(ctx.win_prob)} win · P(title) {_pct1(ctx.p_title)}"
    )


def render_lineup_email(ctx: WeeklyLineupContext, cfg: dict) -> tuple[str, str, str]:
    """Returns (subject, html_body, plaintext_body)."""
    leverage_cfg = cfg["leverage_thresholds"]
    ranked = rank_decisions(ctx.changes, leverage_cfg)
    subject = subject_line(cfg["email"]["subject_prefix"], ctx)

    # ---- plaintext ----
    lines = [
        f"WEEK {ctx.week} vs. {ctx.opponent_name}".ljust(46) + f"MODE: {ctx.mode}",
        f"Projected {ctx.projected:.1f} · Opp {ctx.opponent_projected:.1f} · Win prob {_pct(ctx.win_prob)}",
        f"Season: P(playoffs) {_pct(ctx.p_playoffs)} · P(bye) {_pct(ctx.p_bye)} · P(title) {_pct1(ctx.p_title)}",
        "",
    ]

    if ranked:
        lines.append(f"── CHANGES ({len(ranked)}) " + "─" * 27)
        lines.append("")
        for i, d in enumerate(ranked, 1):
            lines.append(format_decision_plaintext(d, i))
            lines.append("")

    lines.append("── FORCED / NO DECISION " + "─" * 19)
    lines.append("")
    lines.append(ctx.forced_note)
    lines.append("")

    if ctx.watch_items:
        lines.append("── WATCH ITEMS " + "─" * 28)
        lines.append("")
        for w in ctx.watch_items:
            lines.append(f"⚠ {w}")
        lines.append("")

    if ctx.mode_note:
        lines.append("── MODE NOTE " + "─" * 30)
        lines.append("")
        lines.append(ctx.mode_note)

    text = "\n".join(lines).rstrip() + "\n"

    # ---- html: same content, monospace <pre>, styled for mobile-first reading ----
    html_body = f"""\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 640px; margin: 0 auto;">
  <pre style="white-space: pre-wrap; word-wrap: break-word; font-family: 'SF Mono', Menlo, Consolas, monospace;
              font-size: 14px; line-height: 1.5; background: #f6f6f7; padding: 16px; border-radius: 8px;
              color: #1a1a1a;">{html.escape(text)}</pre>
</div>"""

    return subject, html_body, text


@dataclass
class RecapContext:
    week: int
    mode: str
    faab_targets: list[Decision] = field(default_factory=list)
    other_notes: list[str] = field(default_factory=list)


def render_recap_email(ctx: RecapContext, cfg: dict) -> tuple[str, str, str]:
    leverage_cfg = cfg["leverage_thresholds"]
    ranked = rank_decisions(ctx.faab_targets, leverage_cfg)
    subject = f"{cfg['email']['subject_prefix']} Week {ctx.week} Recap — {len(ranked)} FAAB target(s) · MODE: {ctx.mode}"

    lines = [f"WEEK {ctx.week} RECAP".ljust(46) + f"MODE: {ctx.mode}", ""]
    if ranked:
        lines.append(f"── FAAB TARGETS ({len(ranked)}) " + "─" * 20)
        lines.append("")
        for i, d in enumerate(ranked, 1):
            lines.append(format_decision_plaintext(d, i))
            lines.append("")
    else:
        lines.append("No FAAB targets worth bidding on this week.")
        lines.append("")

    if ctx.other_notes:
        lines.append("── NOTES " + "─" * 34)
        lines.append("")
        for n in ctx.other_notes:
            lines.append(f"• {n}")

    text = "\n".join(lines).rstrip() + "\n"
    html_body = (
        "<div style=\"font-family:-apple-system,sans-serif;max-width:640px;margin:0 auto;\">"
        f"<pre style=\"white-space:pre-wrap;font-family:Menlo,Consolas,monospace;font-size:14px;"
        f"line-height:1.5;background:#f6f6f7;padding:16px;border-radius:8px;color:#1a1a1a;\">"
        f"{html.escape(text)}</pre></div>"
    )
    return subject, html_body, text


def render_inactives_email(week: int, alerts: list[str], cfg: dict) -> tuple[str, str, str]:
    """Only ever called when something actually changed — §5, "its arrival is the signal"."""
    subject = f"{cfg['email']['subject_prefix']} Week {week} ALERT — {alerts[0] if alerts else 'lineup change needed'}"
    lines = [f"WEEK {week} INACTIVES ALERT", ""]
    for a in alerts:
        lines.append(f"⚠ {a}")
    text = "\n".join(lines).rstrip() + "\n"
    html_body = (
        "<div style=\"font-family:-apple-system,sans-serif;max-width:640px;margin:0 auto;\">"
        f"<pre style=\"white-space:pre-wrap;font-family:Menlo,Consolas,monospace;font-size:14px;"
        f"line-height:1.5;background:#fff4f4;padding:16px;border-radius:8px;color:#1a1a1a;\">"
        f"{html.escape(text)}</pre></div>"
    )
    return subject, html_body, text


def send_email(subject: str, html_body: str, text_body: str, cfg: dict) -> str:
    """POSTs to Resend. Raises EmailSendError on any non-2xx — callers must let
    this propagate and exit non-zero (design doc §9: "silent email failure").
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise EmailSendError("RESEND_API_KEY not set — see env.template")

    payload = {
        "from": cfg["email"]["from"],
        "to": [cfg["email"]["to"]],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    resp = requests.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 300:
        raise EmailSendError(f"Resend -> {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("id", "")
