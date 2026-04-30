from __future__ import annotations
"""
gtfs_rt.py – pobieranie i parsowanie GTFS-RT ZTM Poznań

Pliki aktualizowane co ~60 sekund:
  trip_updates.pb       – opóźnienia: trip_id + stop_id → delay_seconds
  vehicle_positions.pb  – pozycje: trip_id → vehicle_id (numer boczny)
"""

import logging
import time
from datetime import datetime
from typing import Optional
import requests
from google.transit import gtfs_realtime_pb2

log = logging.getLogger(__name__)

RT_BASE = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGtfsRtFile/?file="
TRIP_UPDATES_URL     = RT_BASE + "trip_updates.pb"
VEHICLE_POSITIONS_URL = RT_BASE + "vehicle_positions.pb"

CACHE_TTL = 60  # sekund


class GTFSRealtime:
    def __init__(self):
        # trip_id → { stop_id → delay_seconds }
        self._delays: dict[str, dict[str, int]] = {}

        # trip_id → vehicle_id
        self._vehicle_ids: dict[str, str] = {}

        self._last_fetch: float = 0.0
        self._fetch_error: str | None = None

    # ── Odświeżanie ───────────────────────────────────────────────────────────

    def refresh_if_stale(self):
        """Pobierz nowe dane RT jeśli cache wygasł (>60s)."""
        if time.time() - self._last_fetch < CACHE_TTL:
            return
        try:
            self._fetch_trip_updates()
            self._fetch_vehicle_positions()
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

    def _fetch_trip_updates(self):
        feed = self._fetch_pb(TRIP_UPDATES_URL)
        delays: dict[str, dict[str, int]] = {}
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            trip_id = tu.trip.trip_id
            stop_delays: dict[str, int] = {}
            for stu in tu.stop_time_update:
                sid = str(stu.stop_id)
                delay = None
                if stu.HasField("departure") and stu.departure.HasField("delay"):
                    delay = stu.departure.delay
                elif stu.HasField("arrival") and stu.arrival.HasField("delay"):
                    delay = stu.arrival.delay
                if delay is not None:
                    stop_delays[sid] = delay
            if stop_delays:
                delays[trip_id] = stop_delays
        self._delays = delays

    def _fetch_vehicle_positions(self):
        feed = self._fetch_pb(VEHICLE_POSITIONS_URL)
        vids: dict[str, str] = {}
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            vp = entity.vehicle
            trip_id  = vp.trip.trip_id
            label    = vp.vehicle.label  # numer boczny
            if trip_id and label:
                vids[trip_id] = label
        self._vehicle_ids = vids

    # ── Zapytania ─────────────────────────────────────────────────────────────

    def get_delay(self, trip_id: str, stop_id: str) -> Optional[int]:
        """Opóźnienie w sekundach dla kursu na przystanku. None = brak danych."""
        return self._delays.get(trip_id, {}).get(stop_id)

    def get_vehicle_id(self, trip_id: str) -> str:
        """Numer boczny pojazdu dla danego kursu."""
        return self._vehicle_ids.get(trip_id, "")

    def enrich_departures(
        self,
        departures: list[dict],
        stop_code_to_id: dict[str, str],
        stop_code: str,
    ) -> list[dict]:
        """
        Wzbogać listę odjazdów z GTFS statycznego o dane RT:
        - delay_seconds, realtime, vehicle_id, vehicle_info
        Modyfikuje listę in-place i zwraca ją.
        """
        self.refresh_if_stale()
        stop_id = stop_code_to_id.get(stop_code, "")

        for dep in departures:
            trip_id = dep["trip_id"]

            # Opóźnienie
            delay = self.get_delay(trip_id, stop_id)
            if delay is not None:
                dep["delay_seconds"] = delay
                dep["realtime"] = True
            else:
                dep["delay_seconds"] = None
                dep["realtime"] = False

            # Numer boczny
            vid = self.get_vehicle_id(trip_id)
            dep["vehicle_id"] = vid

        return departures

    @property
    def last_update(self) -> str:
        if self._last_fetch == 0:
            return "—"
        return datetime.fromtimestamp(self._last_fetch).strftime("%H:%M:%S")

    @property
    def error(self) -> Optional[str]:
        return self._fetch_error
