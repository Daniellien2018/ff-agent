"""Vegas implied team totals — priority projection source per design doc §4.3.

implied_team_total ≈ total/2 - team_spread/2

where `total` is the game over/under and `team_spread` is that team's own
signed point spread (negative = favored). This is the highest-value signal
for DEF/K streaming (§4.2): a team's defense fantasy output is driven almost
entirely by how many points the *opposing* offense is expected to score,
and Vegas prices that far better than any positional stat we could compute
ourselves.

Uses the-odds-api.com (free tier: 500 req/month — one weekly pull is plenty).
Degrades gracefully with no API key or on request failure: callers get an
empty result and must fall back to a non-Vegas method rather than crashing.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from src.players import TEAM_ALIASES, normalize

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_nfl"
_TTL_SECONDS = 3 * 3600  # lines move; keep this short relative to players/league caches

# Reverse-lookup: "kansas city chiefs" -> "KC", built once from players.TEAM_ALIASES.
_FULLNAME_TO_ABBR = {normalize(alias): abbr for abbr, aliases in TEAM_ALIASES.items() for alias in aliases}


class VegasUnavailable(RuntimeError):
    """Raised when odds can't be fetched — missing key, request failure, empty slate."""


@dataclass
class TeamGameContext:
    team: str  # our team abbreviation, e.g. "KC"
    opponent: str
    is_home: bool
    implied_team_total: float
    implied_opponent_total: float
    game_total: float
    commence_time: str


def _team_abbr(odds_api_team_name: str) -> str | None:
    return _FULLNAME_TO_ABBR.get(normalize(odds_api_team_name))


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / "vegas_odds.json"


def fetch_raw_odds(cache_dir: str | Path = "data/cache", *, force_refresh: bool = False) -> list[dict]:
    """Raw odds-api response for NFL spreads+totals. Cached ~3h. Raises VegasUnavailable."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir)

    if not force_refresh and path.exists():
        wrapper = json.loads(path.read_text())
        if time.time() - wrapper.get("fetched_at", 0) < _TTL_SECONDS:
            return wrapper["data"]

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise VegasUnavailable("ODDS_API_KEY not set — see env.template")

    url = f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds"
    params = {"apiKey": api_key, "regions": "us", "markets": "spreads,totals", "oddsFormat": "american"}
    try:
        resp = requests.get(url, params=params, timeout=15)
    except requests.RequestException as e:
        raise VegasUnavailable(f"request failed: {e}") from e

    if resp.status_code != 200:
        raise VegasUnavailable(f"odds-api -> {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    path.write_text(json.dumps({"fetched_at": time.time(), "data": data}))
    return data


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def compute_implied_totals(raw_games: list[dict]) -> dict[str, TeamGameContext]:
    """Consensus (median across books) implied totals per team abbreviation for this week's slate."""
    contexts: dict[str, TeamGameContext] = {}

    for game in raw_games:
        home_name, away_name = game.get("home_team"), game.get("away_team")
        home_abbr, away_abbr = _team_abbr(home_name), _team_abbr(away_name)
        if not home_abbr or not away_abbr:
            continue  # unmapped team name — skip rather than guess

        totals, home_spreads = [], []
        for book in game.get("bookmakers", []):
            for market in book.get("markets", []):
                if market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over":
                            totals.append(outcome["point"])
                elif market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home_name:
                            home_spreads.append(outcome["point"])

        total = _median(totals)
        home_spread = _median(home_spreads)
        if total is None or home_spread is None:
            continue

        away_spread = -home_spread
        home_implied = total / 2 - home_spread / 2
        away_implied = total / 2 - away_spread / 2
        commence = game.get("commence_time", "")

        contexts[home_abbr] = TeamGameContext(
            team=home_abbr, opponent=away_abbr, is_home=True,
            implied_team_total=round(home_implied, 2), implied_opponent_total=round(away_implied, 2),
            game_total=total, commence_time=commence,
        )
        contexts[away_abbr] = TeamGameContext(
            team=away_abbr, opponent=home_abbr, is_home=False,
            implied_team_total=round(away_implied, 2), implied_opponent_total=round(home_implied, 2),
            game_total=total, commence_time=commence,
        )

    return contexts


def get_week_context(cache_dir: str | Path = "data/cache", *, force_refresh: bool = False) -> dict[str, TeamGameContext]:
    """Convenience: fetch + parse in one call. Raises VegasUnavailable on failure."""
    raw = fetch_raw_odds(cache_dir, force_refresh=force_refresh)
    contexts = compute_implied_totals(raw)
    if not contexts:
        raise VegasUnavailable("odds fetched but no games could be parsed (mapping or market gap)")
    return contexts
