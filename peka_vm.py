from __future__ import annotations
"""
peka_vm.py – rezerwowe pobieranie danych o pojazdach z peka.poznan.pl/vm

Używany jako fallback gdy ZTM GTFS-RT nie zwraca pojazdu dla odjazdu.
Dopasowanie po: numer linii + okno czasowe ±5 min.

Format żądania:
  POST https://www.peka.poznan.pl/vm/method.vm?ts={ms}
  Body (x-www-form-urlencoded): method=getTimes&p0={"symbol":"LUKLL02"}

Format odpowiedzi:
  {"success": {"times": [{realTime, line, minutes, vehicle, lfRamp, airCnd,
                          charger, lowFloorBus, lowEntranceBus, ...}], "bollard": {...}}}
  Wpisy z realTime=false nie mają pola `vehicle`.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

log = logging.getLogger(__name__)

PEKA_VM_URL  = "https://www.peka.poznan.pl/vm/method.vm"
CACHE_TTL    = 60   # sekund
MATCH_WINDOW = 5    # minuty — tolerancja dopasowania linia+czas


def _minutes_until(time_str: str) -> Optional[float]:
    """Minuty od teraz do HH:MM (obsługuje godziny >23 i midnight-wrap)."""
    try:
        parts = time_str.split(":")
        h, m  = int(parts[0]), int(parts[1])
        now   = datetime.now()
        base  = now.replace(hour=0, minute=0, second=0, microsecond=0)
        dep   = base + timedelta(hours=h, minutes=m)
        diff  = (dep - now).total_seconds() / 60
        if diff < -720:   # odjazd był "wczoraj" w danych overnight
            dep  += timedelta(days=1)
            diff  = (dep - now).total_seconds() / 60
        return diff
    except Exception:
        return None


class PekaVM:
    def __init__(self):
        # symbol → (timestamp, list[dict])
        self._cache: dict[str, tuple[float, list]] = {}
        self._last_error: Optional[str] = None
        self._last_update: float = 0.0

    # ── Pobieranie danych ─────────────────────────────────────────────────────

    def _fetch_times(self, symbol: str) -> list[dict]:
        ts   = int(time.time() * 1000)
        resp = requests.post(
            f"{PEKA_VM_URL}?ts={ts}",
            data={"method": "getTimes", "p0": json.dumps({"symbol": symbol})},
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()

        raw_times = body.get("success", {}).get("times", [])
        result = []
        for t in raw_times:
            if not t.get("realTime"):
                continue   # brak danych RT = brak vehicle
            vid = str(t.get("vehicle", "")).strip()
            if not vid:
                continue
            result.append({
                "line":     str(t.get("line", "")).strip(),
                "minutes":  float(t.get("minutes", -1)),
                "vehicle_id": vid,
                "vehicle_info": {
                    "low_floor":       bool(t.get("lfRamp") or t.get("lowFloorBus")),
                    "low_floor_level": 1 if (t.get("lfRamp") or t.get("lowFloorBus")) else 0,
                    "low_entrance":    bool(t.get("lowEntranceBus", False)),
                    "air_conditioner": bool(t.get("airCnd",         False)),
                    "ramp":            bool(t.get("lfRamp",         False)),
                    "ticket_machine":  bool(t.get("ticketMachine",  False)),
                    "usb":             bool(t.get("charger",        False)),
                },
            })
        return result

    def get_times(self, symbol: str) -> list[dict]:
        """Zwróć (z cache) listę pojazdów RT dla bolardu."""
        now    = time.time()
        cached = self._cache.get(symbol)
        if cached and (now - cached[0]) < CACHE_TTL:
            return cached[1]

        try:
            times = self._fetch_times(symbol)
            self._cache[symbol] = (now, times)
            self._last_error  = None
            self._last_update = now
            log.debug("VM %s: %d pojazdów RT", symbol, len(times))
            return times
        except Exception as e:
            self._last_error = str(e)
            log.warning("Błąd VM dla %s: %s", symbol, e)
            return cached[1] if cached else []

    # ── Wzbogacanie odjazdów ──────────────────────────────────────────────────

    def enrich_missing(self, departures: list[dict], bollard_symbol: str) -> None:
        """Uzupełnij vehicle_id dla odjazdów bez danych RT (dopasowanie linia+czas)."""
        missing = [d for d in departures if not d.get("realtime")]
        if not missing:
            return

        times = self.get_times(bollard_symbol)
        if not times:
            return

        for dep in missing:
            line      = dep.get("line", "")
            sched_str = dep.get("scheduled_departure_str", "")
            if not line or not sched_str:
                continue

            min_until = _minutes_until(sched_str)
            if min_until is None or min_until < -1:
                continue

            for vt in times:
                if vt["line"] != line:
                    continue
                if abs(vt["minutes"] - min_until) <= MATCH_WINDOW:
                    dep["vehicle_id"]      = vt["vehicle_id"]
                    dep["vehicle_label"]   = ""
                    dep["realtime"]        = True
                    dep["realtime_approx"] = True
                    dep["current_stop"]    = "brak trasy"
                    dep["delay_seconds"]   = None
                    # vehicle_info z VM jako wstępne; nadpisane przez gtfs_static jeśli dostępne
                    if not dep.get("vehicle_info"):
                        dep["vehicle_info"] = vt["vehicle_info"]
                    break

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_update(self) -> str:
        if self._last_update == 0:
            return "—"
        return datetime.fromtimestamp(self._last_update).strftime("%H:%M:%S")
