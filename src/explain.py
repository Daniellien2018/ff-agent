"""Decision -> rationale + leverage classification. Load-bearing per design doc §6:
"since a human reads every output, the system's job is to be legible, not autonomous."

Every recommendation the system makes should be representable as a `Decision`
so email_report.py has one uniform thing to render, and so the leverage tags
(§4.6) are computed the same way everywhere instead of being eyeballed
per-script.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Decision:
    kind: str  # "start_sit" | "stream_def" | "stream_k" | "faab_bid" | "ir_move" | "watch" | "trade"
    action: str  # short imperative, e.g. "START Kenneth Walker over Charbonnet"
    delta_p_title: float  # signed fraction, e.g. 0.009 == +0.9 percentage points of P(title)
    rationale: str
    extra: dict = field(default_factory=dict)  # e.g. {"faab_bid": 12, "walk_away_above": 25}


LEVERAGE_ORDER = ["critical", "moderate", "marginal", "conditional"]


def classify_leverage(delta_p_title: float, cfg: dict) -> str:
    """§4.6. `cfg` is the `leverage_thresholds` block of config.yaml."""
    magnitude = abs(delta_p_title)
    if magnitude > cfg["critical"]:
        return "critical"
    if magnitude > cfg["moderate"]:
        return "moderate"
    return "marginal"


def format_delta(delta_p_title: float) -> str:
    sign = "+" if delta_p_title >= 0 else ""
    return f"{sign}{delta_p_title * 100:.1f}% P(title)"


def format_decision_plaintext(d: Decision, index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    lines = [f"{prefix}{d.action}   [{format_delta(d.delta_p_title)}]"]
    for line in d.rationale.strip().splitlines():
        lines.append(f"   {line}")
    if "faab_bid" in d.extra:
        lines.append(f"   FAAB bid ${d.extra['faab_bid']}. Walk away above ${d.extra.get('walk_away_above', '?')}.")
    return "\n".join(lines)


def rank_decisions(decisions: list[Decision], leverage_cfg: dict) -> list[Decision]:
    """Critical first, then moderate, then marginal, each desc by |Δ P(title)|.

    `leverage_cfg` is config.yaml's `leverage_thresholds` block — pass the real
    one, not a copy, so a tuned threshold doesn't silently drift out of sync.
    """
    order = {k: i for i, k in enumerate(LEVERAGE_ORDER)}
    return sorted(
        decisions,
        key=lambda d: (order[classify_leverage(d.delta_p_title, leverage_cfg)], -abs(d.delta_p_title)),
    )
