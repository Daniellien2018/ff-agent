#!/usr/bin/env python3
"""Sunday lineup email — design doc §5. The main weekly entrypoint.

Pulls live league state, runs the season simulator for the mode label and
Δ P(title) baseline, checks DEF/K streaming against a Vegas-informed ranking,
and flags roster discipline issues (IR, backups, bye crunch). Renders and
sends the email, or prints it if RESEND_API_KEY isn't set / --dry-run is
passed.

Also writes an injury-status snapshot for inactives_check.py to diff against
later in the day (§5: the Sunday 11:45am conditional alert).

RB/WR/TE start-sit (Phase 4, mean-projection slice -- see optimizer.py's
docstring for what's not modeled yet) is included alongside DEF/K streaming.
Per design doc §4.6, swaps below the "marginal" Δ P(title) threshold are
folded into a single summary line instead of listed individually -- most
weeks most decisions are genuine near-ties (§3), and listing them as if they
mattered just trains you to stop reading.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse

import yaml

from src.decisions import build_lineup_swap_decisions, build_streaming_decisions
from src.email_report import EmailSendError, WeeklyLineupContext, render_lineup_email, send_email
from src.explain import classify_leverage
from src.league_data import (
    compute_current_week_overrides,
    fetch_current_pts_index,
    fetch_prior_from_previous_season,
    name_by_roster_id,
)
from src.players import PlayerIndex
from src.roster import bye_week_crunch, flag_backup_violations, flag_ir_candidates, ir_slot_usage
from src.season_sim import build_season_data, determine_current_week, determine_mode, estimate_team_strengths, simulate_season
from src.sleeper_client import SleeperClient

MY_ROSTER_ID = 5  # confirmed via recon: owner_id 1129342415627833344 ("wapen")


def write_inactives_snapshot(cache_dir: str, week: int, roster: dict, players_blob: dict) -> None:
    snapshot = {
        pid: (players_blob.get(pid) or {}).get("injury_status")
        for pid in (roster.get("starters") or [])
        if pid != "0"
    }
    path = Path(cache_dir) / f"inactives_snapshot_week{week}.json"
    path.write_text(json.dumps(snapshot))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending email")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    client = SleeperClient(cache_dir=cfg["cache"]["dir"])
    league_id = cfg["league"]["league_id"]

    print("Fetching live league state...")
    state = client.get_nfl_state()
    current_week = determine_current_week(state)

    rosters = client.get_rosters(league_id)
    users = client.get_users(league_id)
    players_blob = client.get_players()
    names = name_by_roster_id(rosters, users)
    my_roster = next(r for r in rosters if r["roster_id"] == MY_ROSTER_ID)

    idx = PlayerIndex(client)
    idx.load()

    print("Running season simulator...")
    season_data = build_season_data(client, league_id, current_week)
    prior = fetch_prior_from_previous_season(client, cfg["league"]["previous_league_id"], list(range(1, 15)))
    strengths = estimate_team_strengths(season_data, prior, cfg["simulation"]["early_season_shrinkage_weeks"])

    opponent_roster_id = None
    for a, b in season_data.schedule.get(current_week, []):
        if a == MY_ROSTER_ID:
            opponent_roster_id = b
        elif b == MY_ROSTER_ID:
            opponent_roster_id = a
    opponent_name = names.get(opponent_roster_id, "BYE") if opponent_roster_id else "BYE"

    # The team-level historical model above has zero signal to distinguish
    # teams before any games are played -- every team gets the same shrinkage
    # prior mean. Fold in this week's real per-player projections (§4.3) as a
    # one-week override so this week's numbers (and the P(playoffs)/P(title)
    # baseline) reflect actual rosters, not a leaguewide average. Fetched once
    # here and reused for the start-sit optimizer below.
    pts_index = fetch_current_pts_index(cfg, current_week)
    base_overrides, my_real_proj, opp_real_proj = compute_current_week_overrides(
        cfg, rosters, strengths, MY_ROSTER_ID, opponent_roster_id, current_week, pts_index
    )
    if not base_overrides:
        print("  (real per-player projections unavailable this run -- falling back to the historical team-level model)")

    baseline = simulate_season(season_data, strengths, n_trials=cfg["simulation"]["n_trials"], weekly_mean_overrides=base_overrides)

    p_playoffs = baseline.p_playoffs[MY_ROSTER_ID]
    p_bye = baseline.p_bye[MY_ROSTER_ID]
    p_title = baseline.p_title[MY_ROSTER_ID]
    mode = determine_mode(p_playoffs, current_week)

    my_strength = strengths[MY_ROSTER_ID]
    opp_strength = strengths.get(opponent_roster_id) if opponent_roster_id else None
    my_projected = my_real_proj if my_real_proj is not None else my_strength.mean
    opp_projected = (opp_real_proj if opp_real_proj is not None else opp_strength.mean) if opp_strength else None

    win_prob = 0.5
    if opp_strength:
        diff_mean = my_projected - opp_projected
        diff_sd = (my_strength.var + opp_strength.var) ** 0.5
        # P(my_score > opp_score) where the difference is ~Normal(diff_mean, diff_sd^2)
        win_prob = 0.5 * (1 + math.erf(diff_mean / (diff_sd * math.sqrt(2)))) if diff_sd > 0 else 0.5

    streaming_changes, watch_items = build_streaming_decisions(
        cfg, rosters, players_blob, MY_ROSTER_ID, season_data, strengths, p_title, current_week, base_overrides
    )
    lineup_changes, lineup_watch = build_lineup_swap_decisions(
        cfg, my_roster, pts_index, idx, MY_ROSTER_ID, season_data, strengths, p_title, current_week, base_overrides
    )
    watch_items.extend(lineup_watch)
    all_changes = streaming_changes + lineup_changes

    # --- roster discipline ---
    ir_candidates = flag_ir_candidates(my_roster, idx)
    for c in ir_candidates:
        watch_items.append(f"{c.name} is {c.status} and IR-eligible — moving them frees a bench spot.")

    ir_usage = ir_slot_usage(my_roster, cfg["roster"]["ir_slots"])
    if ir_usage["empty_slots"] > 0 and not ir_candidates:
        watch_items.append(f"IR SLOT EMPTY ({ir_usage['empty_slots']} open) — no current IR-eligible player, but check back as news breaks.")

    backup_violations = flag_backup_violations(my_roster, idx)
    for v in backup_violations:
        watch_items.append(f"Carrying {len(v.cut_candidates) + 1} {v.position}s — drop the backup(s), never roster a backup {v.position}.")

    bye_weeks_cfg = cfg.get("bye_weeks") or {}
    if bye_weeks_cfg:
        roster_players = []
        for pid in my_roster.get("players") or []:
            info = players_blob.get(pid) or {}
            position = info.get("position") or ("DEF" if pid.isalpha() and len(pid) <= 3 else None)
            team = pid if position == "DEF" else info.get("team")
            if position and team:
                roster_players.append({"player_id": pid, "position": position, "team": team})
        lookahead = list(range(current_week, min(current_week + 4, 15)))
        crunches = bye_week_crunch(
            roster_players, bye_weeks_cfg, cfg["roster"]["starting_positions"],
            {"FLEX": cfg["roster"]["flex_eligible_positions"]}, lookahead,
        )
        for week, issues in sorted(crunches.items()):
            watch_items.append(f"WEEK {week} BYE CRUNCH: " + "; ".join(issues))
    else:
        watch_items.append("Bye-week crunch check skipped: config.yaml's bye_weeks isn't filled in yet.")

    # §4.6: below "moderate", a swap is a genuine near-tie -- list it in one
    # summary line rather than as a full decision block with its own rationale.
    significant_changes = [c for c in all_changes if classify_leverage(c.delta_p_title, cfg["leverage_thresholds"]) != "marginal"]
    marginal_changes = [c for c in all_changes if c not in significant_changes]

    if marginal_changes:
        forced_note = "Also near-ties (<0.2%): " + ", ".join(c.action.replace("START ", "").split(" over ")[0] for c in marginal_changes) + "."
    elif not all_changes:
        forced_note = "Lineup is already optimal by projection, and no DEF/K stream beats what's rostered. Nothing to decide this week."
    else:
        forced_note = "Rest of lineup is forced -- no other live decisions this week."

    ctx = WeeklyLineupContext(
        week=current_week,
        opponent_name=opponent_name,
        mode=mode,
        projected=round(my_projected, 1),
        opponent_projected=round(opp_projected, 1) if opp_projected is not None else 0.0,
        win_prob=win_prob,
        p_playoffs=p_playoffs,
        p_bye=p_bye,
        p_title=p_title,
        changes=significant_changes,
        forced_note=forced_note,
        watch_items=watch_items,
        mode_note=f"{mode}: see design doc §2.3 for what this optimizes for.",
    )

    subject, html_body, text = render_lineup_email(ctx, cfg)
    write_inactives_snapshot(cfg["cache"]["dir"], current_week, my_roster, players_blob)

    if args.dry_run or not os.environ.get("RESEND_API_KEY"):
        print("\n[DRY RUN — no RESEND_API_KEY or --dry-run set]\n")
        print(subject)
        print()
        print(text)
        return 0

    try:
        email_id = send_email(subject, html_body, text, cfg)
        print(f"Sent: {email_id}")
    except EmailSendError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
