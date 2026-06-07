from __future__ import annotations
"""
gtfs_rt.py – pobieranie i parsowanie GTFS-RT ZTM Poznań

Mapowanie: trip_id z RT pasuje bezpośrednio do trip_id w GTFS statycznym
(kursy z prefixem 4_ są aktywne i obecne w obu źródłach).

vehicle.id (np. 6057) = numer taborowy = klucz w vehicle_dictionary.csv
vehicle.label (np. 610/2) = linia/brygada (do wyświetlania)
"""

import logging
import time
from datetime import datetime
from typing import Optional
import requests
from google.transit import gtfs_realtime_pb2

log = logging.getLogger(__name__)

RT_BASE               = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGtfsRtFile/?file="
VEHICLE_POSITIONS_URL = RT_BASE + "vehicle_positions.pb"
TRIP_UPDATES_URL      = RT_BASE + "trip_updates.pb"

CACHE_TTL = 60  # sekund


class GTFSRealtime:
    def __init__(self):
        # trip_id → (vehicle_label, vehicle_id)
        # np. "4_2281694^+" → ("610/2", "6057")
        self._trip_vehicles: dict[str, tuple[str, str]] = {}

        # trip_id → [(stop_sequence, delay_seconds), ...] posortowane rosnąco
        self._trip_delays: dict[str, list[tuple[int, int]]] = {}

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
        """Buduj mapowanie trip_id → (vehicle_label, vehicle_id, current_stop_seq)."""
        feed = self._fetch_pb(VEHICLE_POSITIONS_URL)
        trip_vehicles: dict[str, tuple[str, str, int]] = {}

        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            vp      = entity.vehicle
            trip_id = vp.trip.trip_id
            label   = vp.vehicle.label
            vid     = vp.vehicle.id
            cur_seq = vp.current_stop_sequence  # gdzie teraz jest pojazd
            if trip_id and label:
                trip_vehicles[trip_id] = (label, vid, cur_seq)

        self._trip_vehicles = trip_vehicles
        log.debug("Pojazdów w RT: %d", len(trip_vehicles))

    def _fetch_trip_updates(self):
        """Pobierz opóźnienia z trip_updates.pb."""
        try:
            feed = self._fetch_pb(TRIP_UPDATES_URL)
            delays: dict[str, int] = {}

            for entity in feed.entity:
                if not entity.HasField("trip_update"):
                    continue
                tu      = entity.trip_update
                trip_id = tu.trip.trip_id
                if not trip_id:
                    continue

                # Zbierz opóźnienia dla każdego stop_time_update osobno
                entries: list[tuple[int, int]] = []
                for stu in tu.stop_time_update:
                    delay = None
                    if stu.HasField("departure") and stu.departure.HasField("delay"):
                        delay = stu.departure.delay
                    elif stu.HasField("arrival") and stu.arrival.HasField("delay"):
                        delay = stu.arrival.delay
                    if delay is not None:
                        entries.append((stu.stop_sequence, delay))
                if entries:
                    delays[trip_id] = sorted(entries)

            self._trip_delays = delays
            log.debug("Opóźnień w RT: %d", len(delays))
        except Exception as e:
            log.debug("trip_updates niedostępne: %s", e)
            self._trip_delays = {}

    def _get_delay_for_stop(self, trip_id: str, stop_seq: int) -> Optional[int]:
        """Zwróć opóźnienie dla konkretnego stop_seq zgodnie z propagacją GTFS-RT.

        Bierze ostatni wpis o stop_sequence <= stop_seq (opóźnienie propaguje się
        do przodu aż do kolejnego wpisu, który je nadpisuje).
        """
        entries = self._trip_delays.get(trip_id)
        if not entries:
            return None
        result = None
        for seq, delay in entries:
            if seq <= stop_seq:
                result = delay
            else:
                break
        return result

    # ── Wzbogacanie odjazdów ──────────────────────────────────────────────────

    def enrich_departures(
        self,
        departures: list[dict],
        gtfs_static=None,
    ) -> list[dict]:
        """
        Wzbogać odjazdy o dane RT używając trip_id jako klucza.
        Nocne kursy mają prefix prev_ w GTFS — szukamy bez niego w RT.
        """
        self.refresh_if_stale()

        for dep in departures:
            trip_id  = dep.get("trip_id", "")
            stop_seq = dep.get("seq", 0)

            # Nocne kursy: zamapuj prev_trip_id → current_trip_id (jeśli dostępne)
            # ZTM zmienia trip_idy między paczkami GTFS, więc prev_id ≠ current RT id
            rt_trip_id = trip_id.replace("prev_", "", 1)
            if trip_id.startswith("prev_") and gtfs_static is not None:
                mapped = getattr(gtfs_static, "_overnight_trip_map", {}).get(trip_id)
                if mapped:
                    rt_trip_id = mapped

            vehicle = self._trip_vehicles.get(rt_trip_id)
            if vehicle:
                label, vid, cur_seq = vehicle
                dep["vehicle_label"] = label
                dep["vehicle_id"]    = vid
                dep["realtime"]      = True

                # Nazwa przystanku gdzie aktualnie jest pojazd
                if gtfs_static and cur_seq is not None:
                    dep["current_stop"] = gtfs_static.get_stop_name_at_seq(
                        rt_trip_id, cur_seq)
                else:
                    dep["current_stop"] = ""

                # Opóźnienie tylko gdy pojazd jeszcze nie minął naszego przystanku
                if cur_seq > 0 and cur_seq <= stop_seq:
                    dep["delay_seconds"] = self._get_delay_for_stop(rt_trip_id, stop_seq)
                else:
                    dep["delay_seconds"] = None
            else:
                # Fallback dla overnight: szukaj po numerze linii w vehicle_label
                if dep.get("overnight"):
                    line = dep.get("line", "")
                    candidates = [
                        (lbl, vid, seq)
                        for lbl, vid, seq in self._trip_vehicles.values()
                        if line and lbl.split("/")[0] == line
                    ]
                    if len(candidates) == 1:
                        label, vid, cur_seq = candidates[0]
                        dep["vehicle_label"]   = label
                        dep["vehicle_id"]      = vid
                        dep["realtime"]        = True
                        dep["realtime_approx"] = True
                        dep["delay_seconds"]   = None
                        if gtfs_static and cur_seq is not None:
                            dep["current_stop"] = gtfs_static.get_stop_name_at_seq(
                                rt_trip_id, cur_seq)
                        else:
                            dep["current_stop"] = ""
                        continue

                dep["vehicle_label"] = ""
                dep["vehicle_id"]    = ""
                dep["realtime"]      = False
                dep["current_stop"]  = ""
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
