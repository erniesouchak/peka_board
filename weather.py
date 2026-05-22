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

# Kody WMO → opis i plik SVG (amCharts animated icons)
WMO_CODES = {
    0:  ("Bezchmurnie",           "day",          "night"),
    1:  ("Przeważnie pogodnie",   "cloudy-day-1", "cloudy-night-1"),
    2:  ("Częściowe zachmurzenie","cloudy-day-2", "cloudy-night-2"),
    3:  ("Zachmurzenie",          "cloudy",       "cloudy"),
    45: ("Mgła",                  "cloudy-day-3", "cloudy-night-3"),
    48: ("Mgła oszroniona",       "cloudy-day-3", "cloudy-night-3"),
    51: ("Mżawka lekka",         "rainy-1",      "rainy-1"),
    53: ("Mżawka",               "rainy-2",      "rainy-2"),
    55: ("Mżawka gęsta",         "rainy-3",      "rainy-3"),
    61: ("Deszcz lekki",         "rainy-1",      "rainy-1"),
    63: ("Deszcz",               "rainy-4",      "rainy-4"),
    65: ("Deszcz ulewny",        "rainy-5",      "rainy-5"),
    71: ("Śnieg lekki",          "snowy-1",      "snowy-1"),
    73: ("Śnieg",                "snowy-3",      "snowy-3"),
    75: ("Śnieg gęsty",          "snowy-5",      "snowy-5"),
    77: ("Ziarna śniegu",        "snowy-2",      "snowy-2"),
    80: ("Przelotny deszcz",     "rainy-3",      "rainy-3"),
    81: ("Deszcz przelotny",     "rainy-5",      "rainy-5"),
    82: ("Deszcz ulewny",        "rainy-6",      "rainy-6"),
    85: ("Opady śniegu",         "snowy-4",      "snowy-4"),
    86: ("Obfite śniegu",        "snowy-6",      "snowy-6"),
    95: ("Burza",                "thunder",      "thunder"),
    96: ("Burza z gradem",       "thunder",      "thunder"),
    99: ("Burza z gradem",       "thunder",      "thunder"),
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
                    "hourly":             [
                        "temperature_2m",
                        "weather_code",
                        "precipitation_probability",
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
        entry = WMO_CODES.get(code, ("Nieznana", "cloudy", "cloudy"))
        desc, icon_day, icon_night = entry
        is_day = c.get("is_day", 1)
        icon = icon_day if is_day else icon_night
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
            entry = WMO_CODES.get(code, ("Nieznana", "cloudy", "cloudy"))
            desc, icon_day, _ = entry  # prognoza zawsze ikona dzienna
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
                "icon":        icon_day,
            })

        return result

    def get_hourly(self, count: int = 4) -> list[dict]:
        """Zwróć prognozę godzinową: teraz + kolejne (count-1) godziny."""
        self.ensure_fresh()
        if not self._data or "hourly" not in self._data:
            return []

        h = self._data["hourly"]
        times   = h.get("time", [])
        temps   = h.get("temperature_2m", [])
        codes   = h.get("weather_code", [])
        probs   = h.get("precipitation_probability", [])

        now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
        try:
            start = times.index(now_str)
        except ValueError:
            return []

        result = []
        for offset in range(count):
            i = start + offset
            if i >= len(times):
                break
            code  = codes[i] if i < len(codes) else 0
            entry = WMO_CODES.get(code, ("Nieznana", "cloudy", "cloudy"))
            desc, icon_day, icon_night = entry
            hour  = int(times[i][11:13])
            is_day = 6 <= hour < 21
            icon  = icon_day if is_day else icon_night

            if offset == 0:
                label = "Teraz"
            elif offset == 1:
                label = "Za godz."
            else:
                label = f"Za {offset}h"

            result.append({
                "label":       label,
                "time":        f"{hour:02d}:00",
                "temp":        round(temps[i]) if i < len(temps) else None,
                "description": desc,
                "icon":        icon,
                "precip_prob": round(probs[i]) if i < len(probs) else 0,
            })

        return result

    def get_all(self) -> dict:
        """Zwróć aktualne + prognozę."""
        return {
            "current":  self.get_current(),
            "hourly":   self.get_hourly(),
            "forecast": self.get_forecast(days=3),
            "updated":  datetime.fromtimestamp(self._last_fetch).strftime("%H:%M")
                        if self._last_fetch else "—",
        }

    def _wind_direction(self, degrees: float) -> str:
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[round(degrees / 45) % 8]
