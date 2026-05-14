"""
calendar_ical.py – pobieranie i parsowanie kalendarza iCal

Obsługuje URL iCal z Google Calendar, Apple Calendar, Synology Calendar itp.
Konfiguracja w board_config.json:
{
  "calendar": {
    "ical_url": "https://calendar.google.com/calendar/ical/..."
  }
}
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

BOARD_CONFIG_PATH = Path("board_config.json")
CACHE_PATH        = Path("calendar_cache.json")
CACHE_TTL         = 3600  # 1 godzina

DAYS_PL   = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"]
MONTHS_PL = ["stycznia","lutego","marca","kwietnia","maja","czerwca",
             "lipca","sierpnia","września","października","listopada","grudnia"]


def _easter(year: int) -> date:
    """Wielkanoc wg algorytmu anonimowego Gregoirańskiego."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day   = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_polish_holiday(d: date) -> bool:
    """Zwraca True jeśli podana data to polskie święto ustawowo wolne od pracy."""
    # Stałe święta
    fixed = {(1, 1), (6, 1), (1, 5), (3, 5), (15, 8), (1, 11), (11, 11), (25, 12), (26, 12)}
    if (d.day, d.month) in fixed:
        return True
    # Ruchome: Wielkanoc, Poniedziałek Wielkanocny, Zielone Świątki, Boże Ciało
    easter = _easter(d.year)
    moveable = {
        easter,
        easter + timedelta(days=1),   # Poniedziałek Wielkanocny
        easter + timedelta(days=49),  # Zielone Świątki (Niedziela)
        easter + timedelta(days=60),  # Boże Ciało
    }
    return d in moveable


class CalendarICal:
    def __init__(self):
        self._ical_urls: list[str] = []
        self._events: list[dict] = []
        self._last_fetch: float = 0.0
        self._configured = False

    def load_config(self):
        if not BOARD_CONFIG_PATH.exists():
            return
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            cal = cfg.get("calendar", {})
            # Obsługa pojedynczego URL lub listy
            url = cal.get("ical_url", "")
            urls = cal.get("ical_urls", [])
            if url:
                urls.append(url)
            # Podmień webcal:// na https://
            self._ical_urls = [u.replace("webcal://", "https://") for u in urls if u]
            self._configured = bool(self._ical_urls)
            if self._configured:
                log.info("Kalendarz iCal skonfigurowany: %d źródeł", len(self._ical_urls))
        except Exception as e:
            log.warning("Błąd odczytu konfiguracji kalendarza: %s", e)

    def ensure_fresh(self):
        if self._events and time.time() - self._last_fetch < CACHE_TTL:
            return
        # Spróbuj z cache
        if CACHE_PATH.exists():
            try:
                cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                if time.time() - cached.get("ts", 0) < CACHE_TTL:
                    self._events = cached.get("events", [])
                    self._last_fetch = cached["ts"]
                    return
            except Exception:
                pass
        self._fetch()

    def _fetch(self):
        if not self._configured:
            return
        all_events = []
        for url in self._ical_urls:
            try:
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                events = self._parse(r.text)
                all_events.extend(events)
                log.info("Kalendarz: pobrano %d wydarzeń z %s", len(events), url[:50])
            except Exception as e:
                log.warning("Błąd pobierania kalendarza %s: %s", url[:50], e)
        # Posortuj i usuń duplikaty po summary+start_date
        seen = set()
        unique = []
        for ev in sorted(all_events, key=lambda e: e.get("start_dt", "")):
            key = (ev.get("summary"), ev.get("start_date"))
            if key not in seen:
                seen.add(key)
                unique.append(ev)
        self._events = unique
        self._last_fetch = time.time()
        CACHE_PATH.write_text(json.dumps({
            "ts":     self._last_fetch,
            "events": self._events,
        }, ensure_ascii=False), encoding="utf-8")
        log.info("Kalendarz: łącznie %d wydarzeń", len(self._events))

    def _parse(self, ical_text: str) -> list[dict]:
        """Parsuj iCal i zwróć listę wydarzeń."""
        events = []
        current = {}
        in_event = False
        in_description = False
        description_lines = []

        for line in ical_text.splitlines():
            # Obsługa zawijania wierszy (spacja lub tab na początku = kontynuacja)
            if line.startswith((" ", "\t")) and current:
                key = list(current.keys())[-1] if current else None
                if key:
                    current[key] += line.lstrip()
                continue

            if in_description and not line.startswith("DESCRIPTION"):
                if line.startswith((" ", "\t")):
                    description_lines.append(line.lstrip())
                    continue

            if line == "BEGIN:VEVENT":
                in_event = True
                current = {}
                description_lines = []
            elif line == "END:VEVENT":
                if current:
                    event = self._process_event(current)
                    if event:
                        events.append(event)
                in_event = False
                current = {}
            elif in_event:
                if ":" in line:
                    key, _, val = line.partition(":")
                    # Usuń parametry (np. DTSTART;TZID=Europe/Warsaw)
                    key_base = key.split(";")[0]
                    current[key_base] = val
                    current[key] = val  # zachowaj też oryginalny klucz

        return sorted(events, key=lambda e: e.get("start_dt", ""))

    def _process_event(self, raw: dict) -> Optional[dict]:
        """Przetwórz surowe pola iCal na słownik wydarzenia."""
        summary = raw.get("SUMMARY", "Bez tytułu")
        # Odkoduj escaped znaki
        summary = summary.replace("\\,", ",").replace("\\n", " ").replace("\\;", ";")

        # Parsuj datę startu
        dtstart_raw = None
        for key, val in raw.items():
            if key.startswith("DTSTART"):
                dtstart_raw = val
                break

        if not dtstart_raw:
            return None

        start_dt, all_day = self._parse_dt(dtstart_raw)
        if not start_dt:
            return None

        # Parsuj datę końca
        dtend_raw = None
        for key, val in raw.items():
            if key.startswith("DTEND"):
                dtend_raw = val
                break

        end_dt = None
        if dtend_raw:
            end_dt, _ = self._parse_dt(dtend_raw)

        # Filtruj stare wydarzenia (starsze niż wczoraj)
        today = date.today()
        end_date = end_dt.date() if end_dt else start_dt.date()
        if end_date < today - timedelta(days=1):
            return None

        return {
            "summary":  summary,
            "start_dt": start_dt.isoformat(),
            "end_dt":   end_dt.isoformat() if end_dt else None,
            "all_day":  all_day,
            "start_date": start_dt.date().isoformat(),
            "start_time": start_dt.strftime("%H:%M") if not all_day else None,
        }

    def _parse_dt(self, val: str) -> tuple[Optional[datetime], bool]:
        """Parsuj datę iCal. Zwraca (datetime, all_day)."""
        val = val.strip()
        try:
            if len(val) == 8 and val.isdigit():
                # Format: 20260506 — cały dzień
                dt = datetime.strptime(val, "%Y%m%d")
                return dt, True
            elif "T" in val:
                if val.endswith("Z"):
                    # UTC — konwertuj na czas lokalny (Europe/Warsaw = UTC+1/+2)
                    val_clean = val[:-1]
                    dt_utc = datetime.strptime(val_clean[:15], "%Y%m%dT%H%M%S")
                    # Prosta konwersja: sprawdź czy czas letni (marzec-październik)
                    month = dt_utc.month
                    offset = 2 if 3 <= month <= 10 else 1
                    dt = dt_utc + timedelta(hours=offset)
                else:
                    # Czas lokalny (z TZID)
                    val_clean = val[:15]
                    dt = datetime.strptime(val_clean, "%Y%m%dT%H%M%S")
                return dt, False
        except Exception as e:
            log.debug("Błąd parsowania daty %r: %s", val, e)
        return None, False

    def get_upcoming(self, days_ahead: int = 14) -> list[dict]:
        """Zwróć nadchodzące wydarzenia z etykietami dat."""
        self.ensure_fresh()
        today = date.today()
        cutoff = today + timedelta(days=days_ahead)
        result = []

        for ev in self._events:
            try:
                start_date = date.fromisoformat(ev["start_date"])
            except Exception:
                continue

            if start_date > cutoff:
                continue

            diff = (start_date - today).days
            if diff == 0:
                date_label = "Dziś"
            elif diff == 1:
                date_label = "Jutro"
            elif diff < 0:
                # Wydarzenie trwające — sprawdź koniec
                if ev.get("end_dt"):
                    try:
                        end_date = date.fromisoformat(ev["end_dt"][:10])
                        if end_date < today:
                            continue
                        date_label = "Trwa"
                    except Exception:
                        continue
                else:
                    continue
            else:
                day_name = DAYS_PL[start_date.weekday()]
                month    = MONTHS_PL[start_date.month - 1]
                date_label = f"{day_name} {start_date.day} {month}"

            # Dla wydarzeń wielodniowych dodaj datę końca
            if ev.get("all_day") and ev.get("end_dt"):
                try:
                    end_date = date.fromisoformat(ev["end_dt"][:10])
                    # iCal end date dla all-day jest exclusive (dzień po ostatnim)
                    end_date = end_date - timedelta(days=1)
                    if end_date > start_date:
                        end_day  = DAYS_PL[end_date.weekday()]
                        end_month = MONTHS_PL[end_date.month - 1]
                        end_label = f"{end_day} {end_date.day} {end_month}"
                        if diff == 0:
                            date_label = f"Dziś – {end_label}"
                        elif diff == 1:
                            date_label = f"Jutro – {end_label}"
                        elif diff < 0:
                            date_label = f"Trwa – {end_label}"
                        else:
                            date_label = f"{date_label} – {end_label}"
                except Exception:
                    pass

            result.append({
                **ev,
                "date_label": date_label,
                "days_until": diff,
                "is_holiday": is_polish_holiday(start_date),
            })

        return result[:20]  # max 20 wydarzeń

    @property
    def is_configured(self) -> bool:
        return self._configured
