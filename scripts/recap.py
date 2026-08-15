#!/usr/bin/env python3
"""Pre-waiver recap + FAAB targets email — design doc §5.

Timing: confirmed via recon (data/recon/waiver_timing.json) that this league's
waivers actually clear Tuesday ~07:00-08:00 UTC (~3am ET), not a naive "12h
before the configured waiver_day_of_week" calculation. Schedule this for
Monday afternoon/evening ET, comfortably ahead of that.

Shares the DEF/K streaming logic with weekly_lineup.py (src/decisions.py) so
the two emails can't recommend different things, and adds bench-cut
candidates + trending-add context that a Sunday-morning lineup email doesn't
need but a pre-waiver email does.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import os

import yaml

from src.decisions import build_streaming_decisions
from src.email_report import EmailSendError, RecapContext, render_recap_email, send_email
from src.league_data import compute_current_week_overrides, fetch_prior_from_previous_season, name_by_roster_id
from src.players import PlayerIndex
from src.roster import rank_bench_by_recent_value
from src.season_sim import build_season_data, determine_current_week, determine_mode, estimate_team_strengths, simulate_season
from src.sleeper_client import SleeperClient

MY_ROSTER_ID = 5


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    client = SleeperClient(cache_dir=cfg["cache"]["dir"])
    league_id = cfg["league"]["league_id"]

    state = client.get_nfl_state()
    current_week = determine_current_week(state)

    rosters = client.get_rosters(league_id)
    users = client.get_users(league_id)
    players_blob = client.get_players()
    idx = PlayerIndex(client)
    idx.load()

    season_data = build_season_data(client, league_id, current_week)
    prior = fetch_prior_from_previous_season(client, cfg["league"]["previous_league_id"], list(range(1, 15)))
    strengths = estimate_team_strengths(season_data, prior, cfg["simulation"]["early_season_shrinkage_weeks"])

    opponent_roster_id = None
    for a, b in season_data.schedule.get(current_week, []):
        if a == MY_ROSTER_ID:
            opponent_roster_id = b
        elif b == MY_ROSTER_ID:
            opponent_roster_id = a

    # Same real-projection fold-in as weekly_lineup.py -- keeps the two
    # emails' Δ P(title) numbers consistent rather than quietly disagreeing.
    base_overrides, _, _ = compute_current_week_overrides(cfg, rosters, strengths, MY_ROSTER_ID, opponent_roster_id, current_week)

    baseline = simulate_season(season_data, strengths, n_trials=cfg["simulation"]["n_trials"], weekly_mean_overrides=base_overrides)
    p_title = baseline.p_title[MY_ROSTER_ID]
    mode = determine_mode(baseline.p_playoffs[MY_ROSTER_ID], current_week)

    faab_targets, stream_notes = build_streaming_decisions(
        cfg, rosters, players_blob, MY_ROSTER_ID, season_data, strengths, p_title, current_week, base_overrides
    )

    my_roster = next(r for r in rosters if r["roster_id"] == MY_ROSTER_ID)
    other_notes = list(stream_notes)

    bench = rank_bench_by_recent_value(client, league_id, my_roster, idx, current_week)
    weak_bench = [b for b in bench if b["avg_pts_last_n"] is not None][:3]
    if weak_bench:
        other_notes.append(
            "Weakest bench bodies right now: "
            + ", ".join(f"{b['name']} ({b['avg_pts_last_n']:.1f} pts/wk)" for b in weak_bench)
        )

    try:
        trending = client.get_trending(trend_type="add", limit=10)
        rostered_ids = {pid for r in rosters for pid in (r.get("players") or [])}
        hot = [t for t in trending if t.get("player_id") not in rostered_ids][:5]
        if hot:
            names = []
            for t in hot:
                info = players_blob.get(t["player_id"]) or {}
                names.append(info.get("full_name") or t["player_id"])
            other_notes.append("Trending adds league-wide, still available: " + ", ".join(names))
    except Exception:
        pass  # trending is a nice-to-have; never block the recap on it

    ctx = RecapContext(week=current_week, mode=mode, faab_targets=faab_targets, other_notes=other_notes)
    subject, html_body, text = render_recap_email(ctx, cfg)

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
