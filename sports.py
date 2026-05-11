"""
sports.py – dane sportowe dla PEKA Board

Źródła:
- NFL/MLB: ESPN nieoficjalne API
- Piłka nożna (Championship, Eerste Divisie): ESPN nieoficjalne API

Konfiguracja w board_config.json:
{
  "sports": {
    "nfl_team":     "Seattle Seahawks",
    "nfl_division": "NFC West",
    "mlb_team":     "Seattle Mariners",
    "mlb_division": "West",
    "mlb_league":   "American League",
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

ESPN_SOCCER   = "https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings"
ESPN_NFL      = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
ESPN_MLB      = "https://site.api.espn.com/apis/v2/sports/baseball/mlb/standings"
ESPN_NFL_SCO  = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_MLB_SCO  = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_SOC_SCO  = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"

SCORES_CACHE_TTL = 120  # 2 minuty dla wyników

HEADERS = {
    "Accept-Encoding": "identity",
    "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
}


class Sports:
    def __init__(self):
        self._nfl_team:     str = ""
        self._nfl_division: str = "NFC West"
        self._mlb_team:     str = ""
        self._mlb_division: str = "West"
        self._mlb_league:   str = "American League"
        self._soccer:       list[dict] = []
        self._cache:        dict = {}
        self._last_fetch:   float = 0.0
        self._configured    = False

    def load_config(self):
        if not BOARD_CONFIG_PATH.exists():
            return
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            sp = cfg.get("sports", {})
            self._nfl_team     = sp.get("nfl_team", "")
            self._nfl_division = sp.get("nfl_division", "NFC West")
            self._mlb_team     = sp.get("mlb_team", "")
            self._mlb_division = sp.get("mlb_division", "West")
            self._mlb_league   = sp.get("mlb_league", "American League")
            self._soccer       = sp.get("soccer", [])
            self._configured   = bool(self._nfl_team or self._mlb_team or self._soccer)
            if self._configured:
                log.info("Sports: NFL=%s MLB=%s soccer=%d",
                         self._nfl_team, self._mlb_team, len(self._soccer))
        except Exception as e:
            log.warning("Sports: błąd konfiguracji: %s", e)

    def ensure_fresh(self):
        if self._cache and time.time() - self._last_fetch < CACHE_TTL:
            return
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
        data = {"ts": time.time(), "soccer": [], "nfl": [], "mlb": []}

        # Piłka nożna
        for sc in self._soccer:
            result = self._fetch_soccer(sc["league"], sc["team"], sc.get("name", ""))
            if result:
                data["soccer"].append(result)

        # NFL
        if self._nfl_team:
            data["nfl"] = self._fetch_nfl_division()

        # MLB
        if self._mlb_team:
            data["mlb"] = self._fetch_mlb_division()

        self._cache = data
        self._last_fetch = data["ts"]
        try:
            CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log.warning("Sports: błąd zapisu cache: %s", e)

    def _espn_get(self, url: str) -> Optional[dict]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            content = r.content
            if content[:2] == b'\x1f\x8b':
                content = gzip.decompress(content)
            return json.loads(content)
        except Exception as e:
            log.warning("ESPN: błąd %s: %s", url[-40:], e)
            return None

    def _fetch_soccer(self, league: str, team_name: str, league_display: str) -> Optional[dict]:
        data = self._espn_get(ESPN_SOCCER.format(league=league))
        if not data:
            return None
        try:
            entries = data["children"][0]["standings"]["entries"]
            for e in entries:
                name = e["team"].get("displayName", "")
                if team_name.lower() in name.lower():
                    stats = {s["name"]: s.get("value", 0) for s in e.get("stats", [])}
                    return {
                        "league":        league_display or league,
                        "team":          name,
                        "rank":          int(stats.get("rank", 0)),
                        "played":        int(stats.get("gamesPlayed", 0)),
                        "wins":          int(stats.get("wins", 0)),
                        "draws":         int(stats.get("ties", 0)),
                        "losses":        int(stats.get("losses", 0)),
                        "goals_for":     int(stats.get("pointsFor", 0)),
                        "goals_against": int(stats.get("pointsAgainst", 0)),
                        "goal_diff":     int(stats.get("pointDifferential", 0)),
                        "points":        int(stats.get("points", 0)),
                    }
        except Exception as e:
            import traceback
            log.warning("Sports: błąd parsowania %s: %s\n%s", league, e, traceback.format_exc())
        return None

    def _fetch_nfl_division(self) -> list[dict]:
        data = self._espn_get(ESPN_NFL)
        if not data:
            return []
        try:
            all_entries = []
            for conf in data.get("children", []):
                all_entries.extend(conf.get("standings", {}).get("entries", []))

            for e in all_entries:
                name = e["team"].get("displayName", "")
                if self._nfl_team.lower() not in name.lower():
                    continue
                stats = {s["name"]: s.get("value", 0) for s in e.get("stats", [])}
                log.info("Sports: NFL %s %s-%s", name,
                         int(stats.get("wins", 0)), int(stats.get("losses", 0)))
                return [{
                    "team":    name,
                    "wins":    int(stats.get("wins", 0)),
                    "losses":  int(stats.get("losses", 0)),
                    "pct":     round(float(stats.get("winPercent", 0)), 3),
                    "is_ours": True,
                }]
        except Exception as e:
            log.warning("Sports: błąd NFL: %s", e)
        return []

    def _fetch_mlb_division(self) -> list[dict]:
        data = self._espn_get(ESPN_MLB)
        if not data:
            return []
        try:
            all_entries = []
            for league in data.get("children", []):
                if self._mlb_league.lower() in league.get("name", "").lower():
                    all_entries.extend(league.get("standings", {}).get("entries", []))

            for e in all_entries:
                name = e["team"].get("displayName", "")
                if self._mlb_team.lower() not in name.lower():
                    continue
                stats = {s["name"]: s.get("value", 0) for s in e.get("stats", [])}
                log.info("Sports: MLB %s %s-%s", name,
                         int(stats.get("wins", 0)), int(stats.get("losses", 0)))
                return [{
                    "team":    name,
                    "wins":    int(stats.get("wins", 0)),
                    "losses":  int(stats.get("losses", 0)),
                    "pct":     round(float(stats.get("winPercent", 0)), 3),
                    "is_ours": True,
                }]
        except Exception as e:
            log.warning("Sports: błąd MLB: %s", e)
        return []

    def get_all(self) -> dict:
        self.ensure_fresh()
        return {
            "nfl_team":     self._nfl_team,
            "nfl_division": self._nfl_division,
            "nfl":          self._cache.get("nfl", []),
            "mlb_team":     self._mlb_team,
            "mlb_division": self._mlb_division,
            "mlb":          self._cache.get("mlb", []),
            "soccer":       self._cache.get("soccer", []),
            "updated":      _fmt_time(self._last_fetch),
        }

    def get_scores(self) -> dict:
        """Pobierz ostatni i następny mecz dla każdej drużyny."""
        result = {}

        if self._nfl_team:
            result["nfl"] = self._fetch_scores(
                ESPN_NFL_SCO, self._nfl_team, "nfl"
            )

        if self._mlb_team:
            result["mlb"] = self._fetch_scores(
                ESPN_MLB_SCO, self._mlb_team, "mlb"
            )

        for sc in self._soccer:
            from datetime import datetime, timedelta
            today = datetime.now()
            date_from = (today - timedelta(days=7)).strftime("%Y%m%d")
            date_to   = (today + timedelta(days=30)).strftime("%Y%m%d")
            url = ESPN_SOC_SCO.format(league=sc["league"]) + f"?dates={date_from}-{date_to}"
            key = sc["league"]
            result[key] = self._fetch_scores(url, sc["team"], "soccer")

        return result

    def _fetch_scores(self, url: str, team_name: str, sport: str) -> dict:
        """Pobierz ostatni i następny mecz drużyny."""
        data = self._espn_get(url)
        if not data:
            return {}
        try:
            events = data.get("events", [])
            last  = None
            next_ = None

            for ev in events:
                comps = ev.get("competitions", [{}])
                comp  = comps[0] if comps else {}
                teams = comp.get("competitors", [])
                # Sprawdź czy nasza drużyna gra
                our_team = None
                opp_team = None
                for t in teams:
                    tname = t.get("team", {}).get("displayName", "")
                    if team_name.lower() in tname.lower():
                        our_team = t
                    else:
                        opp_team = t

                if not our_team or not opp_team:
                    continue

                status = ev.get("status", {}).get("type", {}).get("state", "")
                our_score = our_team.get("score", "")
                opp_score = opp_team.get("score", "")
                our_home  = our_team.get("homeAway", "") == "home"
                opp_name  = opp_team.get("team", {}).get("abbreviation", "?")
                date_str  = ev.get("date", "")
                name      = ev.get("name", "")

                game = {
                    "status":   status,
                    "our_score": our_score,
                    "opp_score": opp_score,
                    "opp":      opp_name,
                    "home":     our_home,
                    "date":     date_str,
                    "won":      None,
                }

                if status == "post":
                    try:
                        game["won"] = int(our_score) > int(opp_score)
                    except Exception:
                        pass
                    last = game
                elif status == "in" and next_ is None:
                    next_ = game
                elif status == "pre" and next_ is None:
                    next_ = game

            return {"last": last, "next": next_}
        except Exception as e:
            log.warning("Sports scores błąd %s: %s", url[-40:], e)
            return {}

    @property
    def is_configured(self) -> bool:
        return self._configured


def _fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%H:%M")
