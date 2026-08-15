#!/usr/bin/env python3
"""Phase 0 recon — see fantasy-agent-design.md Appendix.

Answers, in order:
  1. Pull league + rosters + users + draft picks -> data/recon/*.json
  2. Roster sizes vs draft rounds -> redraft or carryover/keeper?
  3. Probe undocumented endpoints, report which respond for 2026
  4. Pull 2025 transactions, report actual waiver processing timestamps
  5. Confirm what positions the two FLEX slots accept
  6. Fuzzy name->player_id resolver, self-tested against 20 players

Read-only. Never attempts a write against Sleeper. Safe to re-run — everything
goes through sleeper_client's disk cache.

This talks to api.sleeper.app, which may not be reachable from a sandboxed
shell. If every step below fails with a connection error, run this from a
regular (non-sandboxed) terminal instead.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.players import PlayerIndex
from src.sleeper_client import SleeperClient, SleeperError

RECON_DIR = Path("data/recon")

# Endpoints not in Sleeper's public docs but referenced in community write-ups.
# We only probe read paths — nothing here writes.
UNDOCUMENTED_PROBES = {
    "league_drafts": lambda c, cfg: c.get_league_drafts(cfg["league"]["league_id"]),
    "traded_picks": lambda c, cfg: c.get_traded_picks(cfg["league"]["league_id"]),
    "winners_bracket": lambda c, cfg: c.get_winners_bracket(cfg["league"]["league_id"]),
    "losers_bracket": lambda c, cfg: c.get_losers_bracket(cfg["league"]["league_id"]),
    "nfl_state": lambda c, cfg: c.get_nfl_state(),
    "trending_add": lambda c, cfg: c.get_trending(trend_type="add", limit=5),
    "trending_drop": lambda c, cfg: c.get_trending(trend_type="drop", limit=5),
}

# Sleeper's own semantics for flex-style roster_position tokens. If the league
# uses a token not in this table, that's a real "go check the app" flag.
KNOWN_FLEX_TOKENS = {
    "FLEX": ["RB", "WR", "TE"],
    "SUPER_FLEX": ["QB", "RB", "WR", "TE"],
    "WRRB_FLEX": ["WR", "RB"],
    "REC_FLEX": ["WR", "TE"],
    "IDP_FLEX": ["DL", "LB", "DB"],
}

# Well-known 2025/2026 skill-position players. We don't hardcode player_ids
# (we have no live access while building this) — instead we look each one up
# in the freshly-loaded blob by exact name, then test whether *messy* variants
# of that name (nickname, missing suffix, added suffix, one typo) still
# resolve back to the same id. This is self-validating: it works whether the
# blob is 2026's real data or a recon fixture, and it doesn't depend on me
# knowing Sleeper's internal ids.
KNOWN_PLAYER_PROBES = [
    ("Patrick Mahomes", "Pat Mahomes"),
    ("Josh Allen", "Josh Allen"),
    ("Lamar Jackson", "Lamar Jacksn"),
    ("Justin Jefferson", "Justin Jefferson"),
    ("Ja'Marr Chase", "Jamarr Chase"),
    ("CeeDee Lamb", "Ceedee Lamb"),
    ("Tyreek Hill", "Tyreek Hill"),
    ("Amon-Ra St. Brown", "Amon Ra St Brown"),
    ("A.J. Brown", "AJ Brown"),
    ("Christian McCaffrey", "Christian Mccaffrey"),
    ("Breece Hall", "Breece Hall"),
    ("Bijan Robinson", "Bijan Robinson"),
    ("Jahmyr Gibbs", "Jahmyr Gibbs"),
    ("Kenneth Walker III", "Kenneth Walker"),
    ("Saquon Barkley", "Saquon Barkely"),
    ("Travis Kelce", "Travis Kelce"),
    ("Sam LaPorta", "Sam Laporta"),
    ("Mark Andrews", "Mark Andrews"),
    ("Puka Nacua", "Puka Nacua"),
    ("Nico Collins", "Nico Collins"),
]


def _json_default(o):
    if isinstance(o, (datetime,)):
        return o.isoformat()
    raise TypeError(f"not serializable: {o!r}")


def dump(name: str, data) -> None:
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    path = RECON_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, default=_json_default))
    print(f"  wrote {path} ({len(json.dumps(data))} bytes)")


def step1_pull_core(client: SleeperClient, cfg: dict) -> dict:
    print("\n[1/6] Pulling league, rosters, users, draft, draft picks...")
    league_id = cfg["league"]["league_id"]
    draft_id = cfg["league"]["draft_id"]
    out = {}
    for name, fn in [
        ("league", lambda: client.get_league(league_id)),
        ("rosters", lambda: client.get_rosters(league_id)),
        ("users", lambda: client.get_users(league_id)),
        ("draft", lambda: client.get_draft(draft_id)),
        ("draft_picks", lambda: client.get_draft_picks(draft_id)),
    ]:
        try:
            data = fn()
            out[name] = data
            dump(name, data)
        except SleeperError as e:
            print(f"  FAILED {name}: {e}")
            out[name] = None
    return out


def step2_redraft_vs_carryover(core: dict) -> dict:
    print("\n[2/6] Redraft vs. carryover/keeper assessment...")
    league = core.get("league")
    rosters = core.get("rosters")
    draft = core.get("draft")
    draft_picks = core.get("draft_picks")

    report = {"verdict": "UNKNOWN", "evidence": {}}
    if not (league and rosters and draft):
        report["verdict"] = "INCONCLUSIVE — missing core data (see failures above)"
        print(f"  {report['verdict']}")
        return report

    settings = league.get("settings", {})
    # league.settings.draft_rounds is unreliable — observed stale/wrong in testing
    # (reported 3 while the draft object's own settings.rounds said 15, and the
    # actual pick count matched 15). The draft object is the source of truth for
    # how many rounds actually happened.
    draft_rounds = draft.get("settings", {}).get("rounds") or settings.get("draft_rounds")
    max_keepers = settings.get("max_keepers")
    n_teams = len(rosters)
    n_picks = len(draft_picks) if draft_picks else 0
    expected_picks_if_full_redraft = draft_rounds * n_teams if draft_rounds else None

    roster_sizes = [len(r.get("players") or []) for r in rosters]
    avg_roster_size = sum(roster_sizes) / len(roster_sizes) if roster_sizes else 0
    total_roster_players = sum(roster_sizes)

    report["evidence"] = {
        "draft_rounds": draft_rounds,
        "n_teams": n_teams,
        "n_draft_picks_made": n_picks,
        "expected_picks_if_full_redraft": expected_picks_if_full_redraft,
        "max_keepers_setting": max_keepers,
        "avg_roster_size": avg_roster_size,
        "total_roster_players": total_roster_players,
        "league_type_setting": settings.get("type"),
        "previous_league_id": league.get("previous_league_id"),
    }

    # Core test from the design doc: rosters can't be near-full off `draft_rounds`
    # rounds alone unless players carried over from the prior season.
    if draft_rounds and total_roster_players > expected_picks_if_full_redraft * 1.3:
        report["verdict"] = (
            f"CARRYOVER/KEEPER — rosters average {avg_roster_size:.1f} players but only "
            f"{draft_rounds} draft rounds ({expected_picks_if_full_redraft} total picks "
            f"for {n_teams} teams) occurred. Rosters must have carried over."
        )
    elif draft_rounds and total_roster_players <= expected_picks_if_full_redraft * 1.1:
        report["verdict"] = (
            f"REDRAFT-CONSISTENT — roster sizes ({avg_roster_size:.1f} avg) roughly match "
            f"what {draft_rounds} draft rounds alone would produce."
        )
    else:
        report["verdict"] = "AMBIGUOUS — numbers don't cleanly support either read, inspect evidence manually."

    print(f"  draft_rounds={draft_rounds}, teams={n_teams}, picks_made={n_picks}, "
          f"avg_roster_size={avg_roster_size:.1f}")
    print(f"  VERDICT: {report['verdict']}")
    print("  ACTION: confirm with the commissioner regardless — don't ship on this inference alone.")
    return report


def step3_probe_endpoints(client: SleeperClient, cfg: dict) -> dict:
    print("\n[3/6] Probing undocumented/ancillary endpoints...")
    results = {}
    for name, fn in UNDOCUMENTED_PROBES.items():
        try:
            data = fn(client, cfg)
            n = len(data) if isinstance(data, (list, dict)) else "n/a"
            results[name] = {"ok": True, "size": n}
            print(f"  {name}: OK (size={n})")
        except SleeperError as e:
            results[name] = {"ok": False, "error": str(e)}
            print(f"  {name}: FAILED — {e}")
        except Exception as e:  # noqa: BLE001 — recon should never crash mid-probe
            results[name] = {"ok": False, "error": repr(e)}
            print(f"  {name}: ERROR — {e!r}")
    dump("endpoint_probes", results)
    return results


def step4_waiver_timing(client: SleeperClient, cfg: dict) -> dict:
    print("\n[4/6] Pulling 2025 transactions to find real waiver processing times...")
    prev_league_id = cfg["league"]["previous_league_id"]
    waiver_events = []
    for week in range(1, 18):
        try:
            txns = client.get_transactions(prev_league_id, week)
        except SleeperError as e:
            print(f"  week {week}: FAILED — {e}")
            continue
        for t in txns or []:
            if t.get("type") == "waiver":
                waiver_events.append(
                    {
                        "week": week,
                        "status": t.get("status"),
                        "created": t.get("created"),
                        "status_updated": t.get("status_updated"),
                    }
                )

    report = {"n_waiver_events": len(waiver_events), "events": waiver_events}
    if waiver_events:
        # status_updated is epoch ms when Sleeper actually processed the claim.
        by_week = {}
        for e in waiver_events:
            if e["status_updated"]:
                dt = datetime.fromtimestamp(e["status_updated"] / 1000, tz=timezone.utc)
                by_week.setdefault(e["week"], []).append(dt)
        summary = {
            wk: {"n": len(times), "earliest": min(times).isoformat(), "latest": max(times).isoformat()}
            for wk, times in by_week.items()
        }
        report["processing_times_by_week_utc"] = summary
        print(f"  found {len(waiver_events)} waiver events across {len(by_week)} weeks")
        for wk, s in sorted(summary.items()):
            print(f"    week {wk}: {s['n']} claims, processed {s['earliest']} .. {s['latest']} UTC")
    else:
        print("  no waiver transactions found (empty league history or all FAAB went through free_agent adds)")
    dump("waiver_timing", report)
    return report


def step5_flex_eligibility(core: dict) -> dict:
    print("\n[5/6] Confirming FLEX slot eligibility...")
    league = core.get("league")
    if not league:
        print("  INCONCLUSIVE — league object unavailable")
        return {"verdict": "INCONCLUSIVE"}

    positions = league.get("roster_positions", [])
    flex_tokens = [p for p in positions if p != "BN" and p not in ("QB", "RB", "WR", "TE", "K", "DEF")]
    report = {"roster_positions_raw": positions, "flex_tokens_found": flex_tokens, "eligibility": {}}

    for tok in flex_tokens:
        elig = KNOWN_FLEX_TOKENS.get(tok)
        report["eligibility"][tok] = elig or "UNKNOWN TOKEN — verify manually in Sleeper app settings"
        print(f"  {tok} -> {elig or 'UNKNOWN, check manually'}")

    if not flex_tokens:
        print("  no flex-style tokens found in roster_positions — league may not use FLEX at all.")
    dump("flex_eligibility", report)
    return report


def step6_fuzzy_resolver_test(client: SleeperClient) -> dict:
    print("\n[6/6] Building player index and testing fuzzy resolver against 20 players...")
    idx = PlayerIndex(client)
    idx.load()

    known_pairs = []
    missing = []
    for canonical, _ in KNOWN_PLAYER_PROBES:
        hit = idx.resolve(canonical, score_cutoff=90)
        if hit.player_id:
            known_pairs.append((canonical, hit.player_id))
        else:
            missing.append(canonical)

    if missing:
        print(f"  NOTE: {len(missing)} probe names not found in current blob (retired/renamed?): {missing}")

    messy_cases = []
    for canonical, messy in KNOWN_PLAYER_PROBES:
        expected = dict(known_pairs).get(canonical)
        if expected:
            messy_cases.append((messy, expected))

    report = idx.self_test(messy_cases)
    print(f"  resolver accuracy: {report['n_correct']}/{report['n']} ({report['accuracy']:.0%})")
    for r in report["results"]:
        mark = "OK  " if r["ok"] else "MISS"
        print(f"    [{mark}] '{r['input']}' -> {r['resolved']} (score={r['score']:.0f}) expected {r['expected']}")
    dump("fuzzy_resolver_test", report)
    return report


def main() -> int:
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    client = SleeperClient(cache_dir=cfg["cache"]["dir"])

    print("=" * 70)
    print("PHASE 0 RECON —", cfg["league"]["name"], cfg["league"]["season"])
    print("=" * 70)

    try:
        core = step1_pull_core(client, cfg)
        step2_redraft_vs_carryover(core)
        step3_probe_endpoints(client, cfg)
        step4_waiver_timing(client, cfg)
        step5_flex_eligibility(core)
        step6_fuzzy_resolver_test(client)
    except SleeperError as e:
        print(f"\nFATAL: {e}")
        print("If every step above failed with a connection error, api.sleeper.app is")
        print("unreachable from this shell (sandboxed environment?). Re-run from a")
        print("regular terminal: python scripts/recon.py")
        return 1

    print("\n" + "=" * 70)
    print("Recon complete. Full output in data/recon/*.json")
    print("Read data/recon/report review before starting Phase 1.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
