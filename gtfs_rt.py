from __future__ import annotations
"""
gtfs_rt.py – pobieranie i parsowanie GTFS-RT ZTM Poznań

Problem: trip_id i route_id w RT nie pasują do GTFS statycznego.
Rozwiązanie: mapowanie po vehicle.label w formacie LINIA/BRYGADA
  np. "610/2" → linia 610, brygada 2
  Dopasowujemy odjazd po: numer linii + najbliższy czas odjazdu.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional
import requests
from google.transit import gtfs_realtime_pb2

log = logging.getLogger(__name__)

RT_BASE               = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGtfsRtFile/?file="
TRIP_UPDATES_URL      = RT_BASE + "vehicle_positions.pb"   # używamy vehicle_positions jako główne
VEHICLE_POSITIONS_URL = RT_BASE + "vehicle_positions.pb"

CACHE_TTL = 60  # sekund


class GTFSRealtime:
    def __init__(self):
        # linia (str) → lista vehicle_label, np. "610" → ["610/1", "610/2"]
        self._line_vehicles: dict[str, list[str]] = {}

        # vehicle_label → delay_seconds (z trip_updates jeśli dostępne)
        self._vehicle_delays: dict[str, int] = {}

        self._last_fetch: float = 0.0
        self._fetch_error: Optional[str] = None

    # ── Odświeżanie ───────────────────────────────────────────────────────────

    def refresh_if_stale(self):
        if time.time() - self._last_fetch < CACHE_TTL:
            return
        try:
            self._fetch_vehicle_positions()
            self._fetch_trip_updates()
            self._last_fetch = time.time()
            self._fetch_error = None
            log.debug("GTFS-RT odświeżony o %s", datetime.now().strftime("%H:%M:%S"))
        except Exception as e:
            self._fetch_error = str(e)
            log.warning("Błąd GTFS-RT: %s", e)

    def _fetch_pb(self, url: str) -> gtfs_realtime_pb2.FeedMessage:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
        return feed

    def _fetch_vehicle_positions(self):
        """Buduj mapowanie linia → [(vehicle_label, vehicle_id)]."""
        feed = self._fetch_pb(VEHICLE_POSITIONS_URL)
        line_vehicles: dict[str, list[tuple]] = {}

        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            vp    = entity.vehicle
            label = vp.vehicle.label  # np. "610/2" = linia/brygada
            vid   = vp.vehicle.id     # np. "6060" = numer taborowy
            if not label or "/" not in label:
                continue
            line = label.split("/")[0]  # "610"
            line_vehicles.setdefault(line, []).append((label, vid))

        # Sortuj po numerze brygady
        for line in line_vehicles:
            line_vehicles[line].sort(
                key=lambda x: int(x[0].split("/")[1])
                if x[0].split("/")[1].isdigit() else 0
            )

        self._line_vehicles = line_vehicles
        log.debug("Linie z RT: %d", len(line_vehicles))

    def _fetch_trip_updates(self):
        """Spróbuj pobrać opóźnienia z trip_updates.pb."""
        try:
            url  = RT_BASE + "trip_updates.pb"
            feed = self._fetch_pb(url)
            delays: dict[str, int] = {}

            for entity in feed.entity:
                if not entity.HasField("trip_update"):
                    continue
                tu    = entity.trip_update
                label = tu.vehicle.label  # np. "610/2"
                if not label:
                    continue

                # Weź opóźnienie z pierwszego stop_time_update
                for stu in tu.stop_time_update:
                    delay = None
                    if stu.HasField("departure") and stu.departure.HasField("delay"):
                        delay = stu.departure.delay
                    elif stu.HasField("arrival") and stu.arrival.HasField("delay"):
                        delay = stu.arrival.delay
                    if delay is not None:
                        delays[label] = delay
                        break

            self._vehicle_delays = delays
        except Exception as e:
            log.debug("trip_updates niedostępne: %s", e)
            self._vehicle_delays = {}

    # ── Wzbogacanie odjazdów ──────────────────────────────────────────────────

    def enrich_departures(
        self,
        departures: list[dict],
        stop_code_to_id: dict[str, str],
        stop_code: str,
    ) -> list[dict]:
        """
        Dopasuj pojazdy RT do odjazdów GTFS statycznego.
        Mapowanie: numer linii z vehicle.label → odjazd z tym samym numerem linii.
        Przypisuje kolejne brygady do kolejnych odjazdów tej samej linii.
        """
        self.refresh_if_stale()

        # Grupuj odjazdy po linii
        line_deps: dict[str, list[dict]] = {}
        for dep in departures:
            line = dep.get("line", "")
            line_deps.setdefault(line, []).append(dep)

        # Dla każdej linii przypisz pojazdy RT
        for line, deps in line_deps.items():
            vehicles = self._line_vehicles.get(line, [])
            # vehicles to lista (label, vid) posortowana po brygadzie

            for i, dep in enumerate(deps):
                if i < len(vehicles):
                    label, vid = vehicles[i]
                    dep["vehicle_id"]    = vid    # numer taborowy → słownik cech
                    dep["vehicle_label"] = label  # linia/brygada → wyświetlanie
                    dep["realtime"]      = True
                    delay = self._vehicle_delays.get(label)
                    dep["delay_seconds"] = delay
                else:
                    dep["vehicle_id"]    = ""
                    dep["vehicle_label"] = ""
                    dep["realtime"]      = False
                    dep["delay_seconds"] = None

        return departures

    @property
    def last_update(self) -> str:
        if self._last_fetch == 0:
            return "—"
        return datetime.fromtimestamp(self._last_fetch).strftime("%H:%M:%S")

    @property
    def error(self) -> Optional[str]:
        return self._fetch_error
