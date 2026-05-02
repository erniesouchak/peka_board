"""
weather.py – pobieranie pogody z Open-Meteo API

Bez klucza API, dane z modelu meteorologicznego.
Odświeżanie co 30 minut.

Domyślna lokalizacja: Luboń k. Poznania
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

# Domyślna lokalizacja — Luboń k. Poznania
DEFAULT_LAT = 52.345
DEFAULT_LON = 16.875

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_PATH  = Path("weather_cache.json")
CACHE_TTL   = 1800  # 30 minut

# Kody WMO → opis i ikona FA
WMO_CODES = {
    0:  ("Bezchmurnie",      "fa-sun"),
    1:  ("Przeważnie pogodnie", "fa-sun"),
    2:  ("Częściowe zachmurzenie", "fa-cloud-sun"),
    3:  ("Zachmurzenie",     "fa-cloud"),
    45: ("Mgła",             "fa-smog"),
    48: ("Mgła oszroniona",  "fa-smog"),
    51: ("Mżawka lekka",     "fa-cloud-drizzle"),
    53: ("Mżawka",           "fa-cloud-drizzle"),
    55: ("Mżawka gęsta",     "fa-cloud-drizzle"),
    61: ("Deszcz lekki",     "fa-cloud-rain"),
    63: ("Deszcz",           "fa-cloud-rain"),
    65: ("Deszcz ulewny",    "fa-cloud-showers-heavy"),
    71: ("Śnieg lekki",      "fa-snowflake"),
    73: ("Śnieg",            "fa-snowflake"),
    75: ("Śnieg gęsty",      "fa-snowflake"),
    77: ("Ziarna śniegu",    "fa-snowflake"),
    80: ("Przelotny deszcz", "fa-cloud-rain"),
    81: ("Deszcz przelotny", "fa-cloud-showers-heavy"),
    82: ("Burza z deszczem", "fa-cloud-showers-heavy"),
    85: ("Opady śniegu",     "fa-snowflake"),
    86: ("Obfite opady śniegu", "fa-snowflake"),
    95: ("Burza",            "fa-bolt"),
    96: ("Burza z gradem",   "fa-bolt"),
    99: ("Burza z gradem",   "fa-bolt"),
}

DAYS_PL = ["Niedz.", "Pon.", "Wt.", "Śr.", "Czw.", "Pt.", "Sob."]
MONTHS_PL = ["sty", "lut", "mar", "kwi", "maj", "cze",
             "lip", "sie", "wrz", "paź", "lis", "gru"]


class Weather:
    def __init__(self):
        self._data: Optional[dict] = None
        self._last_fetch: float = 0.0
        self._lat = DEFAULT_LAT
        self._lon = DEFAULT_LON

    def load_config(self, board_config_path: Path = Path("board_config.json")):
        """Wczytaj lokalizację z board_config.json jeśli podana."""
        if board_config_path.exists():
            try:
                cfg = json.loads(board_config_path.read_text(encoding="utf-8"))
                weather = cfg.get("weather", {})
                self._lat = float(weather.get("lat", DEFAULT_LAT))
                self._lon = float(weather.get("lon", DEFAULT_LON))
                log.info("Pogoda: lokalizacja %.3f, %.3f", self._lat, self._lon)
            except Exception as e:
                log.warning("Błąd odczytu lokalizacji pogody: %s", e)

    def ensure_fresh(self):
        """Pobierz dane jeśli cache starszy niż 30 minut."""
        if self._data and time.time() - self._last_fetch < CACHE_TTL:
            return
        # Spróbuj z cache plikowego
        if CACHE_PATH.exists():
            try:
                cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                if time.time() - cached.get("ts", 0) < CACHE_TTL:
                    self._data = cached
                    self._last_fetch = cached["ts"]
                    return
            except Exception:
                pass
        self._fetch()

    def _fetch(self):
        """Pobierz dane z Open-Meteo."""
        try:
            r = requests.get(
                WEATHER_URL,
                params={
                    "latitude":           self._lat,
                    "longitude":          self._lon,
                    "current":            [
                        "temperature_2m",
                        "apparent_temperature",
                        "relative_humidity_2m",
                        "precipitation",
                        "wind_speed_10m",
                        "wind_direction_10m",
                        "weather_code",
                        "is_day",
                    ],
                    "daily":              [
                        "weather_code",
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "precipitation_sum",
                        "precipitation_probability_max",
                    ],
                    "timezone":           "Europe/Warsaw",
                    "forecast_days":      4,
                    "wind_speed_unit":    "ms",
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            data["ts"] = time.time()
            self._data = data
            self._last_fetch = data["ts"]
            CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
            log.info("Pogoda pobrana pomyślnie")
        except Exception as e:
            log.warning("Błąd pobierania pogody: %s", e)

    def get_current(self) -> Optional[dict]:
        """Zwróć aktualną pogodę."""
        self.ensure_fresh()
        if not self._data or "current" not in self._data:
            return None

        c = self._data["current"]
        code = c.get("weather_code", 0)
        desc, icon = WMO_CODES.get(code, ("Nieznana", "fa-question"))
        is_day = c.get("is_day", 1)

        # W nocy słońce → księżyc
        if code == 0 and not is_day:
            icon = "fa-moon"
        elif code == 1 and not is_day:
            icon = "fa-cloud-moon"

        wind_dir = self._wind_direction(c.get("wind_direction_10m", 0))

        return {
            "temp":        round(c.get("temperature_2m", 0)),
            "feels_like":  round(c.get("apparent_temperature", 0)),
            "humidity":    round(c.get("relative_humidity_2m", 0)),
            "precip":      round(c.get("precipitation", 0), 1),
            "wind_speed":  round(c.get("wind_speed_10m", 0), 1),
            "wind_dir":    wind_dir,
            "description": desc,
            "icon":        icon,
            "is_day":      bool(is_day),
        }

    def get_forecast(self, days: int = 3) -> list[dict]:
        """Zwróć prognozę na X dni (bez dziś)."""
        self.ensure_fresh()
        if not self._data or "daily" not in self._data:
            return []

        d = self._data["daily"]
        dates   = d.get("time", [])
        codes   = d.get("weather_code", [])
        t_max   = d.get("temperature_2m_max", [])
        t_min   = d.get("temperature_2m_min", [])
        precip  = d.get("precipitation_sum", [])
        precip_prob = d.get("precipitation_probability_max", [])

        result = []
        # Pomiń pierwszy dzień (dziś) — zacznij od jutra
        for i in range(1, min(days + 1, len(dates))):
            code = codes[i] if i < len(codes) else 0
            desc, icon = WMO_CODES.get(code, ("Nieznana", "fa-question"))
            try:
                dt = datetime.strptime(dates[i], "%Y-%m-%d")
                day_name = DAYS_PL[dt.weekday()]  # pon-nie = 0-6, ale weekday() 0=pon
                # Konwertuj weekday() na indeks DAYS_PL (0=niedz)
                day_name = DAYS_PL[(dt.weekday() + 1) % 7]
                date_str = f"{dt.day} {MONTHS_PL[dt.month-1]}"
            except Exception:
                day_name = ""
                date_str = dates[i]

            result.append({
                "day":        day_name,
                "date":       date_str,
                "t_max":      round(t_max[i]) if i < len(t_max) else None,
                "t_min":      round(t_min[i]) if i < len(t_min) else None,
                "precip":     round(precip[i], 1) if i < len(precip) else 0,
                "precip_prob": round(precip_prob[i]) if i < len(precip_prob) else 0,
                "description": desc,
                "icon":        icon,
            })

        return result

    def get_all(self) -> dict:
        """Zwróć aktualne + prognozę."""
        return {
            "current":  self.get_current(),
            "forecast": self.get_forecast(days=3),
            "updated":  datetime.fromtimestamp(self._last_fetch).strftime("%H:%M")
                        if self._last_fetch else "—",
        }

    def _wind_direction(self, degrees: float) -> str:
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[round(degrees / 45) % 8]
