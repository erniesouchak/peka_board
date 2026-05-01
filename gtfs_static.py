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
CACHE_PATH      = Path("gtfs_cache.zip")
CACHE_PREV_PATH = Path("gtfs_cache_prev.zip")
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

        # trip_id → {seq → stop_name}  (do wyświetlania "gdzie jest pojazd")
        self.trip_stop_names: dict[str, dict[int, str]] = {}

        # Kalendarz z poprzedniej paczki (dla kursów overnight)
        self.prev_calendar: dict[str, dict] = {}
        self.prev_calendar_dates: dict[str, dict[str, int]] = {}

        self._feed_end_date: Optional[date] = None
        self._feed_start_date: Optional[date] = None
        self._loaded = False

    # ── Pobieranie i ładowanie ────────────────────────────────────────────────

    def ensure_loaded(self):
        """Załaduj dane jeśli brak lub paczka nie obejmuje dzisiaj."""
        today = date.today()
        if (self._loaded
                and self._feed_end_date
                and self._feed_start_date
                and self._feed_start_date <= today <= self._feed_end_date):
            return
        log.info("Pobieram paczkę GTFS na %s…", today)
        self._download_latest()
        self._parse_zip()
        self._load_vehicle_dict()
        self._loaded = True
        log.info("GTFS załadowany. Ważny: %s – %s",
                 self._feed_start_date, self._feed_end_date)

    def ensure_loaded(self):
        """Załaduj dane jeśli brak lub paczka nie obejmuje dzisiaj."""
        today = date.today()
        if (self._loaded
                and self._feed_end_date
                and self._feed_start_date
                and self._feed_start_date <= today <= self._feed_end_date):
            return
        log.info("Pobieram paczkę GTFS na %s…", today)
        prev_url, curr_url = self._find_package_urls()
        self._download_file(curr_url, CACHE_PATH)
        if prev_url:
            self._download_file(prev_url, CACHE_PREV_PATH)
        else:
            CACHE_PREV_PATH.unlink(missing_ok=True)
        self._parse_zip()
        self._load_vehicle_dict()
        self._loaded = True
        log.info("GTFS załadowany. Ważny: %s – %s",
                 self._feed_start_date, self._feed_end_date)

    def _find_package_urls(self) -> tuple:
        """Znajdź URL paczki na dziś i poprzedniej (dla nocnych)."""
        from bs4 import BeautifulSoup
        today = date.today()
        today_str = today.strftime("%Y%m%d")

        r = requests.get("https://www.ztm.poznan.pl/otwarte-dane/gtfsfiles/", timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        links = [a["href"] for a in soup.find_all("a", href=True) if ".zip" in a["href"]]

        # Zbierz wszystkie paczki z datami
        packages = []
        for link in links:
            filename = link.split("file=")[-1].replace(".zip", "")
            parts = filename.split("_")
            if len(parts) != 2:
                continue
            try:
                start_str, end_str = parts[0], parts[1]
                packages.append((start_str, end_str, link))
            except Exception:
                continue

        # Znajdź paczkę na dziś
        curr_url = None
        curr_start = None
        curr_end = None
        for start_str, end_str, link in packages:
            if start_str <= today_str <= end_str:
                if curr_start is None or start_str > curr_start:
                    curr_start = start_str
                    curr_end   = end_str
                    curr_url   = link

        if not curr_url:
            log.warning("Brak paczki na dziś, używam najnowszej")
            curr_url = packages[0][2] if packages else None
            curr_start = packages[0][0] if packages else None

        # Znajdź poprzednią paczkę (kończy się dzień przed startem aktualnej)
        prev_url = None
        if curr_start:
            yesterday_str = (datetime.strptime(curr_start, "%Y%m%d").date()
                             - timedelta(days=1)).strftime("%Y%m%d")
            for start_str, end_str, link in packages:
                if start_str <= yesterday_str <= end_str:
                    prev_url = link
                    break

        log.info("Paczka bieżąca: %s", curr_url)
        log.info("Paczka poprzednia: %s", prev_url)
        return prev_url, curr_url

    def _download_file(self, url: str, path: Path):
        """Pobierz plik GTFS pod wskazaną ścieżkę."""
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        log.info("Zapisano %s (%.1f MB)", path, path.stat().st_size / 1e6)

    def _parse_zip(self):
        """Parsuj pliki CSV z ZIP-a. Dodatkowo doładuj nocne z poprzedniej paczki."""
        with zipfile.ZipFile(CACHE_PATH) as zf:
            log.debug("Pliki w ZIP: %s", zf.namelist())
            self._parse_feed_info(zf)
            self._parse_stops(zf)
            self._parse_routes(zf)
            self._parse_trips(zf)
            self._parse_calendar(zf)
            self._parse_calendar_dates(zf)
            self._parse_stop_times(zf)

        # Doładuj nocne kursy z poprzedniej paczki (godziny >= 24:00)
        if CACHE_PREV_PATH.exists():
            log.info("Doładowuję nocne kursy z poprzedniej paczki…")
            self._merge_overnight_from_prev()
            log.info("Nocne kursy doładowane.")

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
            end_str   = rows[0].get("feed_end_date",   "")
            start_str = rows[0].get("feed_start_date", "")
            try:
                self._feed_end_date = datetime.strptime(end_str, "%Y%m%d").date()
            except ValueError:
                self._feed_end_date = date.today() + timedelta(days=1)
            try:
                self._feed_start_date = datetime.strptime(start_str, "%Y%m%d").date()
            except ValueError:
                self._feed_start_date = date.today()

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
        self.trip_stop_names.clear()
        for row in self._csv_reader(zf, "stop_times.txt"):
            sid     = row["stop_id"].strip()
            trip_id = row["trip_id"].strip()
            seq     = int(row.get("stop_sequence", "0").strip())
            self.stop_times.setdefault(sid, []).append({
                "trip_id":   trip_id,
                "arrival":   row.get("arrival_time",   "").strip(),
                "departure": row.get("departure_time", "").strip(),
                "seq":       seq,
            })
            # Buduj mapowanie trip_id → seq → stop_name
            name = self.stop_id_to_name.get(sid, "")
            if name:
                self.trip_stop_names.setdefault(trip_id, {})[seq] = name

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
                    "low_floor":       hf_lf in ("1", "2"),
                    "low_floor_level": int(hf_lf) if hf_lf in ("0","1","2") else 0,
                    "low_entrance":    hf_lf == "2",
                    "air_conditioner": row.get("air_conditioner", "0").strip() == "1",
                    "ramp":            row.get("ramp", "0").strip() == "1",
                    "ticket_machine":  row.get("ticket_machine", "0").strip() == "1",
                    "usb":             row.get("usb_charger", "0").strip() == "1",
                }
        except Exception as e:
            log.warning("Nie udało się pobrać vehicle_dictionary: %s", e)

    # ── Zapytania ─────────────────────────────────────────────────────────────

    def is_service_active_prev(self, service_id: str, for_date: Optional[date] = None) -> bool:
        """Sprawdź aktywność kursu w poprzedniej paczce GTFS."""
        d   = for_date or date.today()
        dow = d.weekday()
        date_str = d.strftime("%Y%m%d")

        exc = self.prev_calendar_dates.get(service_id, {}).get(date_str)
        if exc == 1:
            return True
        if exc == 2:
            return False

        cal = self.prev_calendar.get(service_id)
        if not cal:
            return False
        return cal["start"] <= d <= cal["end"] and dow in cal["days"]

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

            # Kursy nocne (overnight=True) startowały wczoraj
            is_overnight = st.get("overnight", False)
            check_date = today - timedelta(days=1) if is_overnight else today

            if is_overnight:
                active = self.is_service_active_prev(trip["service_id"], check_date)
            else:
                active = self.is_service_active(trip["service_id"], check_date)

            if not active:
                continue

            # GTFS pozwala na godziny >24:00 dla kursów po północy
            # Dla overnight używamy wczoraj jako base_date
            dep_norm, dep_date = self._normalize_time(dep, check_date)

            # Odrzuć kursy które już minęły
            if dep_date == today and dep_norm < now_str:
                continue
            if dep_date < today:
                continue

            line = self.routes.get(trip["route_id"], "?")
            # Dla overnight oblicz rzeczywistą godzinę (np. 26:33 → 02:33)
            sched_display = dep_norm[:5] if is_overnight else dep[:5]
            results.append({
                "trip_id":                st["trip_id"],
                "line":                   line,
                "direction":              trip["headsign"],
                "scheduled_departure":    dep,
                "scheduled_departure_str": sched_display,
                "seq":                    st["seq"],
                "vehicle_id":             "",
                "vehicle_info":           {},
                "delay_seconds":          None,
                "realtime":               False,
                "overnight":              is_overnight,  # True/False dla wszystkich
            })

            if len(results) >= limit * 10:  # bezpieczny limit górny
                break

        # Sortuj po rzeczywistej godzinie odjazdu
        # Nocne (26:33 → 02:33 następnego dnia) muszą być przed kursami rannymi (06:00)
        # Używamy dep_norm który już jest po przeliczeniu >24h
        def sort_key(r):
            # Użyj scheduled_departure_str który już zawiera przeliczoną godzinę
            t = r["scheduled_departure_str"]
            try:
                h, m = int(t[:2]), int(t[3:5])
                # Godziny nocne (00-05) traktuj jako < godziny dzienne (06+)
                # ale tylko dla kursów overnight
                if r.get("overnight") and h < 6:
                    return h * 60 + m  # np. 02:33 → 153
                return h * 60 + m
            except Exception:
                return 9999

        results.sort(key=sort_key)

        # Teraz odetnij do limitu
        return results[:limit]

    def _merge_overnight_from_prev(self):
        """
        Z poprzedniej paczki GTFS załaduj tylko kursy nocne (departure >= 24:00).
        Dodaj je do istniejących stop_times, trips i routes.
        """
        with zipfile.ZipFile(CACHE_PREV_PATH) as zf:
            # Trips z poprzedniej paczki
            prev_trips = {}
            for row in self._csv_reader(zf, "trips.txt"):
                prev_trips[row["trip_id"].strip()] = {
                    "route_id":   row["route_id"].strip(),
                    "service_id": row["service_id"].strip(),
                    "headsign":   row.get("trip_headsign", "").strip(),
                    "direction":  row.get("direction_id", "0").strip(),
                }

            # Routes z poprzedniej paczki
            prev_routes = {}
            for row in self._csv_reader(zf, "routes.txt"):
                prev_routes[row["route_id"].strip()] = row["route_short_name"].strip()

            # Calendar z poprzedniej paczki
            prev_calendar = {}
            if "calendar.txt" in zf.namelist():
                day_names = ["monday","tuesday","wednesday","thursday",
                             "friday","saturday","sunday"]
                for row in self._csv_reader(zf, "calendar.txt"):
                    active_days = {
                        i for i, d in enumerate(day_names)
                        if row.get(d, "0").strip() == "1"
                    }
                    try:
                        start = datetime.strptime(
                            row["start_date"].strip(), "%Y%m%d").date()
                        end   = datetime.strptime(
                            row["end_date"].strip(),   "%Y%m%d").date()
                    except ValueError:
                        continue
                    prev_calendar[row["service_id"].strip()] = {
                        "days": active_days, "start": start, "end": end
                    }

            # Calendar dates z poprzedniej paczki
            prev_cal_dates: dict[str, dict[str, int]] = {}
            if "calendar_dates.txt" in zf.namelist():
                for row in self._csv_reader(zf, "calendar_dates.txt"):
                    sid = row["service_id"].strip()
                    dt  = row["date"].strip()
                    exc = int(row.get("exception_type", "1").strip())
                    prev_cal_dates.setdefault(sid, {})[dt] = exc

            # Wczoraj = dzień dla którego szukamy nocnych
            yesterday = date.today() - timedelta(days=1)

            self.prev_calendar       = prev_calendar
            self.prev_calendar_dates = prev_cal_dates

            added = 0
            for row in self._csv_reader(zf, "stop_times.txt"):
                dep = row.get("departure_time", "").strip()
                if not dep:
                    continue

                # Tylko kursy po północy (>= 24:00)
                try:
                    h = int(dep.split(":")[0])
                except ValueError:
                    continue
                if h < 24:
                    continue

                trip_id = row["trip_id"].strip()
                trip    = prev_trips.get(trip_id)
                if not trip:
                    continue

                # Sprawdź czy kurs był aktywny wczoraj
                service_id = trip["service_id"]
                date_str   = yesterday.strftime("%Y%m%d")
                dow        = yesterday.weekday()

                exc = prev_cal_dates.get(service_id, {}).get(date_str)
                if exc == 2:
                    continue
                if exc != 1:
                    cal = prev_calendar.get(service_id)
                    if not cal:
                        continue
                    if not (cal["start"] <= yesterday <= cal["end"]
                            and dow in cal["days"]):
                        continue

                # Dodaj trip i route do głównych słowników (z prefiksem prev_)
                prefixed_trip_id = f"prev_{trip_id}"
                if prefixed_trip_id not in self.trips:
                    self.trips[prefixed_trip_id] = trip
                    route_id = trip["route_id"]
                    if route_id not in self.routes:
                        self.routes[route_id] = prev_routes.get(route_id, "?")

                # Dodaj stop_time
                sid = row["stop_id"].strip()
                self.stop_times.setdefault(sid, []).append({
                    "trip_id":   prefixed_trip_id,
                    "arrival":   row.get("arrival_time", "").strip(),
                    "departure": dep,
                    "seq":       int(row.get("stop_sequence", "0").strip()),
                    "overnight": True,
                })
                added += 1

        # Posortuj ponownie po doładowaniu
        for sid in self.stop_times:
            self.stop_times[sid].sort(key=lambda x: x["departure"])

        log.info("Dodano %d nocnych wpisów stop_times", added)

    def _normalize_time(self, gtfs_time: str, base_date: date) -> tuple:
        """Obsługa godzin >24:00 (kursy po północy)."""
        parts = gtfs_time.split(":")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
        if h >= 24:
            return f"{h-24:02d}:{m:02d}:{s:02d}", base_date + timedelta(days=1)
        return f"{h:02d}:{m:02d}:{s:02d}", base_date

    def get_vehicle_info(self, vehicle_id: str) -> dict:
        return self.vehicles.get(vehicle_id, {})

    def get_stop_name_at_seq(self, trip_id: str, seq: int) -> str:
        """Zwróć nazwę przystanku dla danego kursu i sekwencji."""
        stops = self.trip_stop_names.get(trip_id, {})
        if not stops:
            return ""
        # Jeśli dokładna sekwencja nie istnieje, weź najbliższą mniejszą
        if seq in stops:
            return stops[seq]
        lower = [s for s in stops if s <= seq]
        if lower:
            return stops[max(lower)]
        return stops[min(stops.keys())]

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
