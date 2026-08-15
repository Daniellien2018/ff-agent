#!/usr/bin/env python3
"""Sunday ~11:45am conditional alert — design doc §5.

"The 11:45am conditional is the highest-value job in the system... because it
only fires when it matters, its arrival is itself the signal." This only
sends an email when a starter's injury status has gotten worse since the
Sunday-morning lineup email (weekly_lineup.py) was sent — diffed against the
snapshot that script writes.

Silent by design on a no-op run: exits 0 with a log line, no email. Exits
non-zero on unexpected failure so a scheduler (GitHub Actions) surfaces it,
per §9 "silent email failure."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.email_report import EmailSendError, render_inactives_email, send_email
from src.season_sim import determine_current_week
from src.sleeper_client import SleeperClient

MY_ROSTER_ID = 5

# Rough badness ordering -- only alert on a move that plausibly changes a
# start/sit decision, not e.g. "Questionable" -> "Questionable" noise.
_SEVERITY = {None: 0, "Questionable": 1, "Doubtful": 2, "Out": 3, "IR": 3, "PUP": 3, "NA": 3, "DNR": 3, "Sus": 3, "COV": 3}


def _severity(status: str | None) -> int:
    return _SEVERITY.get(status, 1)  # unknown statuses treated as "worth a look", not ignored


def main() -> int:
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    client = SleeperClient(cache_dir=cfg["cache"]["dir"])
    league_id = cfg["league"]["league_id"]

    state = client.get_nfl_state()
    week = determine_current_week(state)

    snapshot_path = Path(cfg["cache"]["dir"]) / f"inactives_snapshot_week{week}.json"
    if not snapshot_path.exists():
        print(f"No snapshot for week {week} — run weekly_lineup.py first. Nothing to compare, exiting quietly.")
        return 0

    snapshot = json.loads(snapshot_path.read_text())

    rosters = client.get_rosters(league_id)
    my_roster = next(r for r in rosters if r["roster_id"] == MY_ROSTER_ID)
    players_blob = client.get_players(force_refresh=True)  # freshness matters here more than the 24h cache

    alerts = []
    for pid, old_status in snapshot.items():
        info = players_blob.get(pid) or {}
        new_status = info.get("injury_status")
        if _severity(new_status) > _severity(old_status):
            name = info.get("full_name") or pid
            alerts.append(f"{name}: {old_status or 'no designation'} -> {new_status}. Check your lineup.")

    if not alerts:
        print(f"Week {week}: no status changes since the morning snapshot. No email sent.")
        return 0

    subject, html_body, text = render_inactives_email(week, alerts, cfg)

    try:
        email_id = send_email(subject, html_body, text, cfg)
        print(f"Sent: {email_id}")
    except EmailSendError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
