"""Sleeper's undocumented per-player projections endpoint — design doc §4.3:
"Sleeper projections (undocumented) — Baseline; matches the app."

Confirmed live for the 2026 season (not under /v1, a separate path):
  GET https://api.sleeper.app/projections/nfl/{season}/{week}
      ?season_type=regular&position[]=QB&position[]=RB&...

Sourced from RotoWire and surfaced through Sleeper's own app, so summing a
roster's actual SET starters' `pts_ppr` here is the closest match to what a
manager sees as "projected points" in the Sleeper UI itself -- this is what
closes the gap between our team-level historical model (season_sim.py, which
has zero signal before any games are played) and a real per-player estimate
for the CURRENT week.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECTIONS_BASE = "https://api.sleeper.app/projections/nfl"
ALL_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
_TTL_SECONDS = 6 * 3600  # projections shift through the week as news breaks


class ProjectionsUnavailable(RuntimeError):
    pass


def _cache_path(cache_dir: Path, season: int, week: int) -> Path:
    return cache_dir / f"sleeper_projections_{season}_{week}.json"


def fetch_projections(season: int, week: int, cache_dir: str | Path = "data/cache", *, force_refresh: bool = False) -> list[dict]:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, season, week)

    if not force_refresh and path.exists():
        wrapper = json.loads(path.read_text())
        if time.time() - wrapper.get("fetched_at", 0) < _TTL_SECONDS:
            return wrapper["data"]

    url = f"{PROJECTIONS_BASE}/{season}/{week}"
    params = {"season_type": "regular", "position[]": ALL_POSITIONS}
    try:
        resp = requests.get(url, params=params, timeout=15)
    except requests.RequestException as e:
        raise ProjectionsUnavailable(f"request failed: {e}") from e

    if resp.status_code != 200:
        raise ProjectionsUnavailable(f"sleeper projections -> {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    path.write_text(json.dumps({"fetched_at": time.time(), "data": data}))
    return data


def build_pts_ppr_index(projections: list[dict]) -> dict[str, float]:
    """player_id -> projected PPR points. Players with no meaningful
    projection (deep bench, practice squad) simply aren't in this dict --
    callers should treat a missing id as 0.0, not an error."""
    index = {}
    for entry in projections:
        stats = entry.get("stats") or {}
        pts = stats.get("pts_ppr")
        pid = entry.get("player_id")
        if pid is not None and pts is not None:
            index[pid] = float(pts)
    return index


def roster_projected_total(starter_ids: list[str], pts_index: dict[str, float]) -> float:
    """Sum of a roster's currently-SET starters' projected PPR points --
    matches what Sleeper's own app displays (it projects the lineup you've
    set, not some hypothetical optimal one). Missing/bye/empty-slot ("0")
    entries contribute 0, not an error, since a real team can legitimately
    have an empty or non-playing starter slot.
    """
    return round(sum(pts_index.get(pid, 0.0) for pid in starter_ids if pid != "0"), 2)
