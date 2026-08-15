"""Player cache + fuzzy name -> player_id resolver.

Sleeper's /players/nfl blob is the source of truth for player_ids, but every
other data source (Vegas odds feeds, nflverse, FantasyPros, your own emails
you type "start Kenneth Walker" into) refers to players by name. Names collide
across suffixes (Jr./Sr./III), nicknames, and DST naming conventions ("Bears"
vs "Chicago Bears" vs "CHI" vs "Chicago" all mean the same defense). This
module normalizes that mess into a single resolver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from src.sleeper_client import SleeperClient

_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^a-z0-9 ]")

# Sleeper represents team defenses as player_id == team abbreviation, with
# position "DEF" and a "full_name" like "Chicago Bears". Keep a manual
# abbreviation table since it's small, stable, and not worth fuzzy-matching.
TEAM_ALIASES = {
    "ARI": ["arizona cardinals", "cardinals", "arizona"],
    "ATL": ["atlanta falcons", "falcons", "atlanta"],
    "BAL": ["baltimore ravens", "ravens", "baltimore"],
    "BUF": ["buffalo bills", "bills", "buffalo"],
    "CAR": ["carolina panthers", "panthers", "carolina"],
    "CHI": ["chicago bears", "bears", "chicago"],
    "CIN": ["cincinnati bengals", "bengals", "cincinnati"],
    "CLE": ["cleveland browns", "browns", "cleveland"],
    "DAL": ["dallas cowboys", "cowboys", "dallas"],
    "DEN": ["denver broncos", "broncos", "denver"],
    "DET": ["detroit lions", "lions", "detroit"],
    "GB": ["green bay packers", "packers", "green bay"],
    "HOU": ["houston texans", "texans", "houston"],
    "IND": ["indianapolis colts", "colts", "indianapolis"],
    "JAX": ["jacksonville jaguars", "jaguars", "jacksonville"],
    "KC": ["kansas city chiefs", "chiefs", "kansas city"],
    "LAC": ["los angeles chargers", "chargers", "la chargers"],
    "LAR": ["los angeles rams", "rams", "la rams"],
    "LV": ["las vegas raiders", "raiders", "las vegas", "oakland raiders"],
    "MIA": ["miami dolphins", "dolphins", "miami"],
    "MIN": ["minnesota vikings", "vikings", "minnesota"],
    "NE": ["new england patriots", "patriots", "new england"],
    "NO": ["new orleans saints", "saints", "new orleans"],
    "NYG": ["new york giants", "giants", "ny giants"],
    "NYJ": ["new york jets", "jets", "ny jets"],
    "PHI": ["philadelphia eagles", "eagles", "philadelphia"],
    "PIT": ["pittsburgh steelers", "steelers", "pittsburgh"],
    "SEA": ["seattle seahawks", "seahawks", "seattle"],
    "SF": ["san francisco 49ers", "49ers", "niners", "san francisco"],
    "TB": ["tampa bay buccaneers", "buccaneers", "bucs", "tampa bay"],
    "TEN": ["tennessee titans", "titans", "tennessee"],
    "WAS": ["washington commanders", "commanders", "washington"],
}


def normalize(name: str) -> str:
    name = name.lower().strip()
    name = _SUFFIX_RE.sub("", name).strip()
    name = _PUNCT_RE.sub("", name)
    name = re.sub(r"\s+", " ", name)
    return name


@dataclass
class ResolveResult:
    player_id: str | None
    matched_name: str | None
    score: float
    candidates: list[tuple[str, float]]


class PlayerIndex:
    def __init__(self, client: SleeperClient | None = None):
        self.client = client or SleeperClient()
        self._players: dict[str, dict] = {}
        self._name_to_id: dict[str, str] = {}  # normalized name -> player_id
        self._loaded = False

    def load(self, *, force_refresh: bool = False) -> None:
        blob = self.client.get_players(force_refresh=force_refresh)
        self._players = blob
        self._name_to_id = {}

        for pid, info in blob.items():
            if not info:
                continue
            position = info.get("position")

            if position == "DEF":
                # player_id is already the team abbreviation for defenses.
                for alias in TEAM_ALIASES.get(pid, []):
                    self._name_to_id[normalize(alias)] = pid
                self._name_to_id[normalize(pid)] = pid
                continue

            full_name = info.get("full_name") or f"{info.get('first_name', '')} {info.get('last_name', '')}"
            full_name = full_name.strip()
            if full_name:
                self._name_to_id[normalize(full_name)] = pid

            search_name = info.get("search_full_name")
            if search_name:
                self._name_to_id.setdefault(normalize(search_name), pid)

        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def get(self, player_id: str) -> dict | None:
        self._ensure_loaded()
        return self._players.get(player_id)

    def resolve(self, name: str, *, score_cutoff: float = 80.0, limit: int = 3) -> ResolveResult:
        """Fuzzy-resolve a free-text name to a Sleeper player_id.

        Exact normalized match short-circuits fuzzy scoring. Otherwise uses
        rapidfuzz's token_sort_ratio, which tolerates word-order and minor
        spelling differences ("Kenneth Walker" vs "Kenneth Walker III").
        """
        self._ensure_loaded()
        norm = normalize(name)

        if norm in self._name_to_id:
            return ResolveResult(self._name_to_id[norm], norm, 100.0, [(norm, 100.0)])

        choices = list(self._name_to_id.keys())
        matches = process.extract(norm, choices, scorer=fuzz.token_sort_ratio, limit=limit)
        if not matches:
            return ResolveResult(None, None, 0.0, [])

        best_name, best_score, _ = matches[0]
        candidates = [(m[0], m[1]) for m in matches]

        if best_score >= score_cutoff:
            return ResolveResult(self._name_to_id[best_name], best_name, best_score, candidates)
        return ResolveResult(None, None, best_score, candidates)

    def self_test(self, known: list[tuple[str, str]]) -> dict:
        """known: list of (free_text_name, expected_player_id). Returns a report dict."""
        self._ensure_loaded()
        results = []
        n_correct = 0
        for name, expected_id in known:
            r = self.resolve(name)
            ok = r.player_id == expected_id
            n_correct += int(ok)
            results.append(
                {
                    "input": name,
                    "expected": expected_id,
                    "resolved": r.player_id,
                    "matched_name": r.matched_name,
                    "score": r.score,
                    "ok": ok,
                }
            )
        return {"n": len(known), "n_correct": n_correct, "accuracy": n_correct / len(known) if known else 0.0, "results": results}
