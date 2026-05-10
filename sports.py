"""
sports.py – dane sportowe dla PEKA Board

Źródła:
- NFL/MLB: Sportradar API (przez narzędzie wbudowane w serwer)
- Piłka nożna (Championship, Eerste Divisie): ESPN nieoficjalne API
  Endpoint: https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings

Konfiguracja w board_config.json:
{
  "sports": {
    "nfl_team":    "Seattle Seahawks",
    "mlb_team":    "Seattle Mariners",
    "soccer": [
      {"league": "eng.2", "team": "Southampton",     "name": "Championship"},
      {"league": "ned.2", "team": "Vitesse",         "name": "Eerste Divisie"}
    ]
  }
}
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

BOARD_CONFIG_PATH = Path("board_config.json")
CACHE_PATH        = Path("sports_cache.json")
CACHE_TTL         = 3600  # 1 godzina

ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings"


class Sports:
    def __init__(self):
        self._nfl_team:   str = ""
        self._mlb_team:   str = ""
        self._soccer:     list[dict] = []
        self._cache:      dict = {}
        self._last_fetch: float = 0.0
        self._configured  = False

    def load_config(self):
        if not BOARD_CONFIG_PATH.exists():
            return
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            sp = cfg.get("sports", {})
            self._nfl_team = sp.get("nfl_team", "")
            self._mlb_team = sp.get("mlb_team", "")
            self._soccer   = sp.get("soccer", [])
            self._configured = bool(self._nfl_team or self._mlb_team or self._soccer)
            if self._configured:
                log.info("Sports: skonfigurowano NFL=%s MLB=%s soccer=%d",
                         self._nfl_team, self._mlb_team, len(self._soccer))
        except Exception as e:
            log.warning("Sports: błąd odczytu konfiguracji: %s", e)

    def ensure_fresh(self):
        """Odśwież dane jeśli cache starszy niż 1 godzina."""
        if self._cache and time.time() - self._last_fetch < CACHE_TTL:
            return
        # Spróbuj z cache plikowego
        if CACHE_PATH.exists():
            try:
                cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                if time.time() - cached.get("ts", 0) < CACHE_TTL:
                    self._cache = cached
                    self._last_fetch = cached["ts"]
                    return
            except Exception:
                pass
        self._fetch_all()

    def _fetch_all(self):
        """Pobierz wszystkie dane sportowe."""
        data = {"ts": time.time()}

        # Piłka nożna przez ESPN
        data["soccer"] = []
        for sc in self._soccer:
            result = self._fetch_soccer_standing(sc["league"], sc["team"], sc.get("name", ""))
            if result:
                data["soccer"].append(result)

        self._cache = data
        self._last_fetch = data["ts"]
        try:
            CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log.warning("Sports: błąd zapisu cache: %s", e)

    def _espn_get(self, url: str) -> Optional[dict]:
        """Pobierz JSON z ESPN — obsłuż gzip."""
        try:
            r = requests.get(url, headers={"Accept-Encoding": "identity"}, timeout=10)
            content = r.content
            if content[:2] == b'\x1f\x8b':
                content = gzip.decompress(content)
            return json.loads(content)
        except Exception as e:
            log.warning("ESPN: błąd pobierania %s: %s", url, e)
            return None

    def _fetch_soccer_standing(self, league: str, team_name: str, league_display: str) -> Optional[dict]:
        """Pobierz pozycję drużyny w tabeli z ESPN."""
        url = ESPN_STANDINGS.format(league=league)
        data = self._espn_get(url)
        if not data:
            return None

        try:
            entries = data["children"][0]["standings"]["entries"]
            for e in entries:
                name = e["team"].get("displayName", "")
                if team_name.lower() in name.lower():
                    stats = {s["name"]: s["value"] for s in e["stats"]}
                    return {
                        "league":    league_display or league,
                        "team":      name,
                        "rank":      int(stats.get("rank", 0)),
                        "played":    int(stats.get("gamesPlayed", 0)),
                        "wins":      int(stats.get("wins", 0)),
                        "draws":     int(stats.get("ties", 0)),
                        "losses":    int(stats.get("losses", 0)),
                        "goals_for": int(stats.get("pointsFor", 0)),
                        "goals_against": int(stats.get("pointsAgainst", 0)),
                        "goal_diff": int(stats.get("pointDifferential", 0)),
                        "points":    int(stats.get("points", 0)),
                    }
            log.warning("Sports: nie znaleziono %s w %s", team_name, league)
        except Exception as e:
            log.warning("Sports: błąd parsowania %s: %s", league, e)
        return None

    def get_all(self) -> dict:
        """Zwróć wszystkie dane sportowe."""
        self.ensure_fresh()
        return {
            "nfl_team":  self._nfl_team,
            "mlb_team":  self._mlb_team,
            "soccer":    self._cache.get("soccer", []),
            "updated":   _fmt_time(self._last_fetch),
        }

    @property
    def is_configured(self) -> bool:
        return self._configured


def _fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%H:%M")
