"""Read-only, disk-cached wrapper over the Sleeper API.

Sleeper's public API (https://docs.sleeper.com) is unauthenticated and
read-only for our purposes. This module only ever issues GET requests —
there is no write path here by design, and none should ever be added.
Sleeper asks that the ~5MB /players/nfl blob not be hammered, so every
call goes through a disk cache with a per-endpoint TTL.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.sleeper.app/v1"
USER_AGENT = "ff-agent/0.1 (personal, read-only; github.com/none)"

# Default TTLs, seconds. Overridable per-call via `ttl_hours`.
_TTL_PLAYERS = 24 * 3600
_TTL_LEAGUE = 3600


class SleeperError(RuntimeError):
    """Raised on a non-2xx response or malformed payload from Sleeper."""


class SleeperClient:
    def __init__(self, cache_dir: str | Path = "data/cache", session: requests.Session | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    # ---------------------------------------------------------------- core

    def _cache_path(self, key: str) -> Path:
        safe = key.replace("/", "_").strip("_")
        return self.cache_dir / f"{safe}.json"

    def _read_cache(self, key: str, ttl_seconds: float) -> Any | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            wrapper = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - wrapper.get("fetched_at", 0) > ttl_seconds:
            return None
        return wrapper.get("data")

    def _write_cache(self, key: str, data: Any) -> None:
        path = self._cache_path(key)
        wrapper = {"fetched_at": time.time(), "data": data}
        path.write_text(json.dumps(wrapper))

    def _get(
        self,
        endpoint: str,
        *,
        ttl_seconds: float = _TTL_LEAGUE,
        force_refresh: bool = False,
        params: dict | None = None,
    ) -> Any:
        cache_key = endpoint
        if not force_refresh:
            cached = self._read_cache(cache_key, ttl_seconds)
            if cached is not None:
                return cached

        url = f"{BASE_URL}{endpoint}"
        resp = self._session.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            raise SleeperError(f"GET {endpoint} -> {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise SleeperError(f"GET {endpoint}: non-JSON response") from exc

        self._write_cache(cache_key, data)
        return data

    # ------------------------------------------------------------ league

    def get_league(self, league_id: str, *, force_refresh: bool = False) -> dict:
        return self._get(f"/league/{league_id}", force_refresh=force_refresh)

    def get_rosters(self, league_id: str, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/league/{league_id}/rosters", force_refresh=force_refresh)

    def get_users(self, league_id: str, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/league/{league_id}/users", force_refresh=force_refresh)

    def get_matchups(self, league_id: str, week: int, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/league/{league_id}/matchups/{week}", force_refresh=force_refresh)

    def get_transactions(self, league_id: str, week: int, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/league/{league_id}/transactions/{week}", force_refresh=force_refresh)

    def get_traded_picks(self, league_id: str, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/league/{league_id}/traded_picks", force_refresh=force_refresh)

    def get_winners_bracket(self, league_id: str, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/league/{league_id}/winners_bracket", force_refresh=force_refresh)

    def get_losers_bracket(self, league_id: str, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/league/{league_id}/losers_bracket", force_refresh=force_refresh)

    # ------------------------------------------------------------- draft

    def get_draft(self, draft_id: str, *, force_refresh: bool = False) -> dict:
        return self._get(f"/draft/{draft_id}", force_refresh=force_refresh)

    def get_draft_picks(self, draft_id: str, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/draft/{draft_id}/picks", force_refresh=force_refresh)

    def get_league_drafts(self, league_id: str, *, force_refresh: bool = False) -> list[dict]:
        return self._get(f"/league/{league_id}/drafts", force_refresh=force_refresh)

    # ----------------------------------------------------------- players

    def get_players(self, sport: str = "nfl", *, force_refresh: bool = False) -> dict:
        """The big blob: {player_id: {...}}. Cached 24h minimum per design doc §9."""
        return self._get(f"/players/{sport}", ttl_seconds=_TTL_PLAYERS, force_refresh=force_refresh)

    def get_trending(
        self,
        sport: str = "nfl",
        trend_type: str = "add",
        lookback_hours: int = 24,
        limit: int = 25,
        *,
        force_refresh: bool = False,
    ) -> list[dict]:
        return self._get(
            f"/players/{sport}/trending/{trend_type}",
            params={"lookback_hours": lookback_hours, "limit": limit},
            force_refresh=force_refresh,
        )

    # -------------------------------------------------------------- misc

    def get_nfl_state(self, sport: str = "nfl", *, force_refresh: bool = False) -> dict:
        return self._get(f"/state/{sport}", ttl_seconds=300, force_refresh=force_refresh)

    def get_user(self, username_or_id: str, *, force_refresh: bool = False) -> dict:
        return self._get(f"/user/{username_or_id}", force_refresh=force_refresh)

    def get_user_leagues(
        self, user_id: str, season: int, sport: str = "nfl", *, force_refresh: bool = False
    ) -> list[dict]:
        return self._get(f"/user/{user_id}/leagues/{sport}/{season}", force_refresh=force_refresh)
