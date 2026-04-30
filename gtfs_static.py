from __future__ import annotations
"""
gtfs_static.py – ładowanie i parsowanie statycznego rozkładu GTFS ZTM Poznań

Pliki w paczce ZIP:
  stops.txt        – przystanki: stop_id, stop_code (=symbol bollardu), stop_name
  stop_times.txt   – godziny: trip_id, arrival_time, departure_time, stop_id, stop_sequence
  trips.txt        – kursy: trip_id, route_id, service_id, trip_headsign, direction_id
  routes.txt       – linie: route_id, route_short_name
  calendar.txt     – kiedy kursuje: service_id, monday..sunday, start_date, end_date
  calendar_dates.txt – wyjątki (święta, korekty)
  feed_info.txt    – feed_end_date (do sprawdzania ważności)
"""

import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
import requests

log = logging.getLogger(__name__)

GTFS_LIST_URL  = "https://www.ztm.poznan.pl/otwarte-dane/gtfsfiles/"
GTFS_BASE_URL  = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile/?file="
CACHE_PATH     = Path("gtfs_cache.zip")
VEHICLE_DICT_URL = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGtfsRtFile/?file=vehicle_dictionary.csv"


class GTFSStatic:
    def __init__(self):
        self.stop_code_to_id: dict[str, str] = {}   # LUKLL02 → "1234"
        self.stop_id_to_code: dict[str, str] = {}
        self.stop_id_to_name: dict[str, str] = {}

        # stop_id → [ {trip_id, departure_time, arrival_time, stop_seq} ]
        self.stop_times: dict[str, list[dict]] = {}

        # trip_id → {route_id, service_id, headsign, direction_id}
        self.trips: dict[str, dict] = {}

        # route_id → route_short_name (numer linii)
        self.routes: dict[str, str] = {}

        # service_id → {days: set, start, end}
        self.calendar: dict[str, dict] = {}

        # service_id → { date_str: exception_type }  (1=dodany, 2=usunięty)
        self.calendar_dates: dict[str, dict[str, int]] = {}

        # vehicle_id → {low_floor, air_conditioner, ramp, ticket_machine, usb}
        self.vehicles: dict[str, dict] = {}

        self._feed_end_date: Optional[date] = None
        self._loaded = False

    # ── Pobieranie i ładowanie ────────────────────────────────────────────────

    def ensure_loaded(self):
        """Załaduj dane jeśli brak lub przeterminowane."""
        if self._loaded and self._feed_end_date and self._feed_end_date >= date.today():
            return
        log.info("Pobieram paczkę GTFS…")
        self._download_latest()
        self._parse_zip()
        self._load_vehicle_dict()
        self._loaded = True
        log.info("GTFS załadowany. Ważny do: %s", self._feed_end_date)

    def _download_latest(self):
        """Pobierz najnowszy plik GTFS (bez parametru file= = najnowszy)."""
        url = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile"
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(CACHE_PATH, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        log.info("Zapisano GTFS → %s (%.1f MB)", CACHE_PATH, CACHE_PATH.stat().st_size / 1e6)

    def _parse_zip(self):
        """Parsuj wszystkie pliki CSV z ZIP-a."""
        with zipfile.ZipFile(CACHE_PATH) as zf:
            names = zf.namelist()
            log.debug("Pliki w ZIP: %s", names)

            self._parse_feed_info(zf)
            self._parse_stops(zf)
            self._parse_routes(zf)
            self._parse_trips(zf)
            self._parse_calendar(zf)
            self._parse_calendar_dates(zf)
            self._parse_stop_times(zf)

    def _csv_reader(self, zf: zipfile.ZipFile, filename: str):
        """Otwórz plik CSV z ZIP-a jako DictReader."""
        with zf.open(filename) as f:
            content = f.read().decode("utf-8-sig")  # BOM-safe
            return list(csv.DictReader(io.StringIO(content)))

    def _parse_feed_info(self, zf: zipfile.ZipFile):
        if "feed_info.txt" not in zf.namelist():
            return
        rows = self._csv_reader(zf, "feed_info.txt")
        if rows:
            end_str = rows[0].get("feed_end_date", "")
            try:
                self._feed_end_date = datetime.strptime(end_str, "%Y%m%d").date()
            except ValueError:
                self._feed_end_date = date.today() + timedelta(days=1)

    def _parse_stops(self, zf: zipfile.ZipFile):
        self.stop_code_to_id.clear()
        self.stop_id_to_code.clear()
        self.stop_id_to_name.clear()
        for row in self._csv_reader(zf, "stops.txt"):
            sid  = row["stop_id"].strip()
            code = row.get("stop_code", "").strip()
            name = row.get("stop_name", "").strip()
            self.stop_id_to_name[sid] = name
            if code:
                self.stop_code_to_id[code] = sid
                self.stop_id_to_code[sid]  = code

    def _parse_routes(self, zf: zipfile.ZipFile):
        self.routes.clear()
        for row in self._csv_reader(zf, "routes.txt"):
            self.routes[row["route_id"].strip()] = row["route_short_name"].strip()

    def _parse_trips(self, zf: zipfile.ZipFile):
        self.trips.clear()
        for row in self._csv_reader(zf, "trips.txt"):
            self.trips[row["trip_id"].strip()] = {
                "route_id":   row["route_id"].strip(),
                "service_id": row["service_id"].strip(),
                "headsign":   row.get("trip_headsign", "").strip(),
                "direction":  row.get("direction_id", "0").strip(),
            }

    def _parse_calendar(self, zf: zipfile.ZipFile):
        self.calendar.clear()
        if "calendar.txt" not in zf.namelist():
            return
        day_names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
        for row in self._csv_reader(zf, "calendar.txt"):
            active_days = {
                i for i, d in enumerate(day_names)
                if row.get(d, "0").strip() == "1"
            }
            try:
                start = datetime.strptime(row["start_date"].strip(), "%Y%m%d").date()
                end   = datetime.strptime(row["end_date"].strip(),   "%Y%m%d").date()
            except ValueError:
                continue
            self.calendar[row["service_id"].strip()] = {
                "days": active_days, "start": start, "end": end
            }

    def _parse_calendar_dates(self, zf: zipfile.ZipFile):
        self.calendar_dates.clear()
        if "calendar_dates.txt" not in zf.namelist():
            return
        for row in self._csv_reader(zf, "calendar_dates.txt"):
            sid  = row["service_id"].strip()
            dt   = row["date"].strip()
            exc  = int(row.get("exception_type", "1").strip())
            self.calendar_dates.setdefault(sid, {})[dt] = exc

    def _parse_stop_times(self, zf: zipfile.ZipFile):
        self.stop_times.clear()
        for row in self._csv_reader(zf, "stop_times.txt"):
            sid = row["stop_id"].strip()
            self.stop_times.setdefault(sid, []).append({
                "trip_id":   row["trip_id"].strip(),
                "arrival":   row.get("arrival_time",   "").strip(),
                "departure": row.get("departure_time", "").strip(),
                "seq":       int(row.get("stop_sequence", "0").strip()),
            })
        # Sortuj po godzinie odjazdu
        for sid in self.stop_times:
            self.stop_times[sid].sort(key=lambda x: x["departure"])

    def _load_vehicle_dict(self):
        """Pobierz i załaduj słownik pojazdów."""
        self.vehicles.clear()
        try:
            r = requests.get(VEHICLE_DICT_URL, timeout=15)
            r.raise_for_status()
            reader = csv.DictReader(io.StringIO(r.text))
            for row in reader:
                vid = row.get("vehicle", "").strip()
                if not vid:
                    continue
                hf_lf = row.get("hf_lf_le", "0").strip()
                self.vehicles[vid] = {
                    "low_floor":      hf_lf in ("1", "2"),
                    "low_entrance":   hf_lf == "2",
                    "air_conditioner": row.get("air_conditioner", "0").strip() == "1",
                    "ramp":           row.get("ramp", "0").strip() == "1",
                    "ticket_machine": row.get("ticket_machine", "0").strip() == "1",
                    "usb":            row.get("usb_charger", "0").strip() == "1",
                }
        except Exception as e:
            log.warning("Nie udało się pobrać vehicle_dictionary: %s", e)

    # ── Zapytania ─────────────────────────────────────────────────────────────

    def is_service_active(self, service_id: str, for_date: Optional[date] = None) -> bool:
        """Czy dany service_id jest aktywny w podanym dniu?"""
        d = for_date or date.today()
        date_str = d.strftime("%Y%m%d")
        dow = d.weekday()  # 0=pon, 6=nie

        # Wyjątki (calendar_dates)
        exc = self.calendar_dates.get(service_id, {}).get(date_str)
        if exc == 1:
            return True
        if exc == 2:
            return False

        # Główny kalendarz
        cal = self.calendar.get(service_id)
        if not cal:
            return False
        return cal["start"] <= d <= cal["end"] and dow in cal["days"]

    def get_departures_for_stop(
        self,
        stop_code: str,
        from_time: Optional[datetime] = None,
        limit: int = 20,
    ) -> list:
        """
        Zwróć listę planowych odjazdów dla bollardu (stop_code) od from_time.
        Każdy element: {
            trip_id, line, direction, scheduled_departure,
            scheduled_departure_str, vehicle_id (puste bez RT)
        }
        """
        self.ensure_loaded()
        now = from_time or datetime.now()
        now_str = now.strftime("%H:%M:%S")
        today = now.date()

        stop_id = self.stop_code_to_id.get(stop_code)
        if not stop_id:
            log.warning("Nieznany stop_code: %s", stop_code)
            return []

        times = self.stop_times.get(stop_id, [])
        results = []

        for st in times:
            dep = st["departure"]
            if not dep:
                continue

            trip = self.trips.get(st["trip_id"])
            if not trip:
                continue

            if not self.is_service_active(trip["service_id"], today):
                continue

            # GTFS pozwala na godziny >24:00 dla kursów po północy
            dep_norm, dep_date = self._normalize_time(dep, today)
            if dep_norm < now_str and dep_date <= today:
                continue

            line = self.routes.get(trip["route_id"], "?")
            results.append({
                "trip_id":                st["trip_id"],
                "line":                   line,
                "direction":              trip["headsign"],
                "scheduled_departure":    dep,
                "scheduled_departure_str": dep[:5],   # HH:MM
                "vehicle_id":             "",          # uzupełni GTFS-RT
                "vehicle_info":           {},          # uzupełni GTFS-RT
                "delay_seconds":          None,        # uzupełni GTFS-RT
                "realtime":               False,
            })

            if len(results) >= limit:
                break

        return results

    def _normalize_time(self, gtfs_time: str, base_date: date) -> tuple[str, date]:
        """Obsługa godzin >24:00 (kursy po północy)."""
        parts = gtfs_time.split(":")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
        if h >= 24:
            return f"{h-24:02d}:{m:02d}:{s:02d}", base_date + timedelta(days=1)
        return f"{h:02d}:{m:02d}:{s:02d}", base_date

    def get_vehicle_info(self, vehicle_id: str) -> dict:
        return self.vehicles.get(vehicle_id, {})

    def search_stops(self, pattern: str) -> list:
        """
        Wyszukaj unikalne nazwy przystanków pasujące do wzorca.
        Zwraca listę unikalnych nazw przystanków.
        """
        self.ensure_loaded()
        pattern_lower = pattern.lower()
        seen = set()
        results = []
        for code, sid in self.stop_code_to_id.items():
            name = self.stop_id_to_name.get(sid, "")
            if pattern_lower in name.lower() and name not in seen:
                seen.add(name)
                results.append({"name": name})
        return sorted(results, key=lambda x: x["name"])

    def get_bollards_for_stop(self, stop_name: str) -> list:
        """
        Zwróć wszystkie bollardy dla przystanku o podanej nazwie.
        Dla każdego bollardu dołącz linie które przez niego przejeżdżają.
        """
        self.ensure_loaded()
        results = []
        for code, sid in self.stop_code_to_id.items():
            name = self.stop_id_to_name.get(sid, "")
            if name != stop_name:
                continue

            # Znajdź linie przejeżdżające przez ten bollard
            lines = set()
            for st in self.stop_times.get(sid, []):
                trip = self.trips.get(st["trip_id"])
                if trip:
                    line = self.routes.get(trip["route_id"], "")
                    if line:
                        lines.add(line)

            # Znajdź dominujący kierunek (headsign) dla tego bollardu
            directions = {}
            for st in self.stop_times.get(sid, []):
                trip = self.trips.get(st["trip_id"])
                if trip and trip["headsign"]:
                    directions[trip["headsign"]] = directions.get(trip["headsign"], 0) + 1

            direction = max(directions, key=directions.get) if directions else "—"
            sorted_lines = sorted(lines, key=lambda x: (len(x), x))

            results.append({
                "symbol":    code,
                "stop_name": stop_name,
                "direction": direction,
                "label":     f"{stop_name} → {direction}",
                "lines":     sorted_lines[:8],
            })

        # Sortuj po symbolu
        return sorted(results, key=lambda x: x["symbol"])
