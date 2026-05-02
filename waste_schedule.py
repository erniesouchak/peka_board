"""
waste_schedule.py – harmonogram wywozu odpadów KOM-LUB Luboń

Pobiera i parsuje harmonogram z kom-lub.com.pl dla zabudowy jednorodzinnej.
Dane są aktualizowane kwartalnie — plik cache jest ważny 80 dni.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

SCHEDULE_URL   = "https://kom-lub.com.pl/aktualny-harmonogram-wywozow/"
CACHE_PATH     = Path("waste_cache.json")
CACHE_TTL_DAYS = 80

# Mapowanie nagłówków kolumn → typ odpadu
WASTE_TYPES = {
    "odpady zmieszane":          "zmieszane",
    "szkło":                     "szkło",
    "tworzywa sztuczne":         "tworzywa",
    "papier":                    "papier",
    "wielko gabarytowe":         "wielkogabarytowe",
    "zielone (bio)":             "bio",
}

# Mapowanie nazw miesięcy PL → numer
MONTHS_PL = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
    "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12,
}

# Ikony dla typów odpadów
WASTE_ICONS = {
    "zmieszane":        ("fa-trash",        "#888888"),
    "szkło":            ("fa-wine-bottle",   "#2ecc71"),
    "tworzywa":         ("fa-recycle",       "#f39c12"),
    "papier":           ("fa-newspaper",     "#3498db"),
    "wielkogabarytowe": ("fa-couch",         "#9b59b6"),
    "bio":              ("fa-leaf",          "#27ae60"),
}


class WasteSchedule:
    def __init__(self):
        self._schedule: dict = {}   # date_str → [waste_type, ...]
        self._loaded = False
        self._loaded_at: Optional[date] = None

    def ensure_loaded(self, rejon: str = "V"):
        """Załaduj dane jeśli brak lub cache starszy niż 80 dni."""
        if self._loaded and self._loaded_at:
            if (date.today() - self._loaded_at).days < CACHE_TTL_DAYS:
                return

        # Spróbuj z cache
        if CACHE_PATH.exists():
            try:
                data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                loaded_at = datetime.fromisoformat(data.get("loaded_at", "2000-01-01")).date()
                if (date.today() - loaded_at).days < CACHE_TTL_DAYS:
                    self._schedule = data.get("schedule", {})
                    self._loaded_at = loaded_at
                    self._loaded = True
                    log.info("Harmonogram wywozów z cache (rejon %s)", rejon)
                    return
            except Exception as e:
                log.warning("Błąd odczytu cache harmonogramu: %s", e)

        log.info("Pobieram harmonogram wywozów dla rejonu %s…", rejon)
        self._fetch_and_parse(rejon)

    def _fetch_and_parse(self, rejon: str):
        try:
            r = requests.get(SCHEDULE_URL, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            self._schedule = self._parse(soup, rejon)
            self._loaded_at = date.today()
            self._loaded = True

            # Zapisz cache
            CACHE_PATH.write_text(json.dumps({
                "loaded_at": self._loaded_at.isoformat(),
                "rejon":     rejon,
                "schedule":  self._schedule,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            log.info("Harmonogram załadowany — %d dni z wywozem", len(self._schedule))
        except Exception as e:
            log.error("Błąd pobierania harmonogramu: %s", e)
            self._schedule = {}
            self._loaded = True

    def _parse(self, soup: BeautifulSoup, rejon: str) -> dict[str, list[str]]:
        """
        Parsuj tabele harmonogramu dla danego rejonu.
        Zwraca słownik: "2026-05-06" → ["zmieszane", "bio"]
        """
        schedule: dict[str, list[str]] = {}
        rejon_tag = f"R {rejon}"
        current_year = date.today().year

        tables = soup.find_all("table")

        for table in tables:
            rows = table.find_all("tr")
            if not rows:
                continue

            # Znajdź nagłówek kolumn (pierwszy wiersz tabeli)
            header_row = rows[0]
            header_cells = header_row.find_all(["td", "th"])
            if not header_cells:
                continue

            # Odczytaj typy odpadów z nagłówka
            waste_cols = []
            for cell in header_cells[1:]:
                text = cell.get_text(strip=True).lower()
                waste_type = next(
                    (v for k, v in WASTE_TYPES.items() if k in text), None
                )
                waste_cols.append(waste_type)

            # Szukaj wiersza z naszym rejonem
            in_our_rejon = False
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if not cells:
                    continue

                first_cell = cells[0].get_text(strip=True)

                # Sprawdź czy to wiersz z nagłówkiem rejonu
                if first_cell == rejon_tag:
                    # Zaktualizuj nagłówek kolumn dla tego rejonu
                    waste_cols = []
                    for cell in cells[1:]:
                        text = cell.get_text(strip=True).lower()
                        waste_type = next(
                            (v for k, v in WASTE_TYPES.items() if k in text), None
                        )
                        waste_cols.append(waste_type)
                    in_our_rejon = True
                    continue

                # Jeśli jesteśmy w naszym rejonie i to wiersz z miesiącem
                if in_our_rejon:
                    # Jeśli to nowy nagłówek rejonu — koniec naszego rejonu
                    if first_cell.startswith("R ") and len(first_cell) <= 6:
                        in_our_rejon = False
                        continue

                    month_text = first_cell.upper()
                    month_num = MONTHS_PL.get(month_text)
                    if not month_num:
                        continue

                    # Dla każdej kolumny odpadów
                    for col_idx, waste_type in enumerate(waste_cols):
                        if waste_type is None:
                            continue
                        cell_idx = col_idx + 1
                        if cell_idx >= len(cells):
                            continue

                        cell_text = cells[cell_idx].get_text(strip=True)
                        if not cell_text:
                            continue

                        days = self._parse_days(cell_text)
                        for day in days:
                            try:
                                d = date(current_year, month_num, day)
                                key = d.isoformat()
                                if key not in schedule:
                                    schedule[key] = []
                                if waste_type not in schedule[key]:
                                    schedule[key].append(waste_type)
                            except ValueError:
                                pass

        return schedule

    def _parse_days(self, text: str) -> list[int]:
        """Parsuj tekst z dniami np. '6,7 i 20,21' → [6, 7, 20, 21]"""
        # Usuń wszystko oprócz cyfr, przecinków i spacji
        text = text.replace(" i ", ",").replace("i", ",")
        parts = re.split(r"[,\s]+", text)
        days = []
        for p in parts:
            p = p.strip()
            if p.isdigit():
                days.append(int(p))
        return days

    # ── Zapytania ─────────────────────────────────────────────────────────────

    def get_upcoming(self, days_ahead: int = 3) -> list[dict]:
        """
        Zwróć wywozy w ciągu najbliższych X dni.
        Każdy element: { date, date_str, days_until, waste_types, icons }
        """
        today = date.today()
        result = []

        for i in range(days_ahead + 1):
            d = today + timedelta(days=i)
            key = d.isoformat()
            waste_types = self._schedule.get(key, [])
            if waste_types:
                result.append({
                    "date":       key,
                    "date_label": self._date_label(d, today),
                    "days_until": i,
                    "waste_types": waste_types,
                    "items": [
                        {
                            "type":  wt,
                            "icon":  WASTE_ICONS.get(wt, ("fa-recycle", "#888"))[0],
                            "color": WASTE_ICONS.get(wt, ("fa-recycle", "#888"))[1],
                            "label": wt.capitalize(),
                        }
                        for wt in waste_types
                    ],
                })

        return result

    def _date_label(self, d: date, today: date) -> str:
        diff = (d - today).days
        date_str = f"{d.day} maja" if d.month == 5 else f"{d.day}.{d.month:02d}"
        days_pl = ["pon.","wt.","śr.","czw.","pt.","sob.","niedz."]
        day_name = days_pl[d.weekday()]
        if diff == 0:
            return f"Dziś, {date_str}"
        elif diff == 1:
            return f"Jutro, {date_str}"
        else:
            return f"{day_name} {date_str}"

    @property
    def is_loaded(self) -> bool:
        return self._loaded
