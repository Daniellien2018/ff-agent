#!/usr/bin/env python3
"""Sanity check: prints exactly what the system thinks your team/roster is,
so you can eyeball it against the Sleeper app and confirm MY_ROSTER_ID (and
everything downstream) points at the right team.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.league_data import name_by_roster_id
from src.players import PlayerIndex
from src.sleeper_client import SleeperClient

MY_ROSTER_ID = 5


def main() -> int:
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    client = SleeperClient(cache_dir=cfg["cache"]["dir"])
    league_id = cfg["league"]["league_id"]

    rosters = client.get_rosters(league_id)
    users = client.get_users(league_id)
    names = name_by_roster_id(rosters, users)
    my_roster = next(r for r in rosters if r["roster_id"] == MY_ROSTER_ID)

    idx = PlayerIndex(client)
    idx.load()

    print(f"Team: {names.get(MY_ROSTER_ID)}  (roster_id={MY_ROSTER_ID})")
    print(f"Record: {my_roster.get('settings', {}).get('wins', 0)}-{my_roster.get('settings', {}).get('losses', 0)}")
    print()

    starters = set(my_roster.get("starters") or [])
    reserve = set(my_roster.get("reserve") or [])

    print("STARTERS:")
    for pid in my_roster.get("starters") or []:
        if pid == "0":
            print("  (empty slot)")
            continue
        info = idx.get(pid) or {}
        name = info.get("full_name") or pid
        pos = info.get("position", "?")
        status = info.get("injury_status")
        print(f"  {pos:4s} {name}" + (f"  [{status}]" if status else ""))

    print("\nBENCH:")
    for pid in my_roster.get("players") or []:
        if pid in starters or pid in reserve:
            continue
        info = idx.get(pid) or {}
        name = info.get("full_name") or pid
        pos = info.get("position", "?")
        status = info.get("injury_status")
        print(f"  {pos:4s} {name}" + (f"  [{status}]" if status else ""))

    if reserve:
        print("\nIR/RESERVE:")
        for pid in reserve:
            info = idx.get(pid) or {}
            print(f"  {info.get('position', '?'):4s} {info.get('full_name') or pid}  [{info.get('injury_status')}]")
    else:
        print("\nIR/RESERVE: (empty)")

    print("\nIf this doesn't match what you see in the Sleeper app, MY_ROSTER_ID")
    print("in scripts/*.py is wrong -- tell me your correct roster_id and I'll fix it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
