from __future__ import annotations
"""
main.py – FastAPI backend tablicy PEKA

Uruchomienie:
    pip install fastapi uvicorn requests gtfs-realtime-bindings protobuf jinja2
    uvicorn main:app --host 0.0.0.0 --port 8080 --reload

Endpointy:
    GET  /                    – dashboard HTML
    GET  /api/departures      – odjazdy JSON dla wszystkich bollardów
    GET  /api/status          – status GTFS (ważność, ostatni RT)
    POST /api/config          – zapis konfiguracji bollardów
    GET  /api/config          – odczyt konfiguracji
"""

import asyncio
import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gtfs_static import GTFSStatic
from gtfs_rt import GTFSRealtime
from waste_schedule import WasteSchedule
from synology_photos import SynologyPhotos
from weather import Weather
from calendar_ical import CalendarICal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONFIG_FILE = Path("config.json")
MAX_DEPARTURES_PER_STOP = 20

app = FastAPI(title="PEKA Board")

# Statyczne pliki i szablony
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Globalne instancje GTFS
gtfs_static    = GTFSStatic()
gtfs_rt        = GTFSRealtime()
waste_schedule = WasteSchedule()
synology       = SynologyPhotos()
weather        = Weather()
calendar       = CalendarICal()


# ── Konfiguracja ──────────────────────────────────────────────────────────────

def load_config() -> list[dict]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_config(data: list[dict]):
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    log.info("Startuję PEKA Board…")
    try:
        gtfs_static.ensure_loaded()
    except Exception as e:
        log.error("Błąd ładowania GTFS: %s", e)
    try:
        waste_schedule.ensure_loaded(rejon="V")
    except Exception as e:
        log.error("Błąd ładowania harmonogramu wywozów: %s", e)
    try:
        synology.load_config()
        weather.load_config()
        calendar.load_config()
    except Exception as e:
        log.error("Błąd ładowania konfiguracji: %s", e)
    # Uruchom zadanie sprawdzające ważność paczki co godzinę
    asyncio.create_task(_gtfs_watcher())


async def _gtfs_watcher():
    """Co godzinę sprawdza czy paczka GTFS nadal obowiązuje."""
    try:
        while True:
            await asyncio.sleep(3600)
            try:
                today = date.today()
                if (gtfs_static._feed_start_date is None
                        or gtfs_static._feed_end_date is None
                        or not (gtfs_static._feed_start_date <= today
                                <= gtfs_static._feed_end_date)):
                    log.info("Paczka GTFS wygasła — pobieram nową…")
                    gtfs_static._loaded = False
                    gtfs_static.ensure_loaded()
                    log.info("Paczka GTFS zaktualizowana automatycznie.")
                else:
                    log.debug("Paczka GTFS aktualna do %s.", gtfs_static._feed_end_date)
            except Exception as e:
                log.error("Błąd auto-aktualizacji GTFS: %s", e)
    except asyncio.CancelledError:
        log.debug("GTFS watcher zatrzymany.")


# ── Endpointy HTML ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = load_config()
    return templates.TemplateResponse("index.html", {
        "request":   request,
        "has_config": bool(config),
        "theme":     "dark",
    })


@app.get("/light", response_class=HTMLResponse)
async def dashboard_light(request: Request):
    config = load_config()
    return templates.TemplateResponse("index.html", {
        "request":   request,
        "has_config": bool(config),
        "theme":     "light",
    })


@app.get("/dark", response_class=HTMLResponse)
async def dashboard_dark(request: Request):
    config = load_config()
    return templates.TemplateResponse("index.html", {
        "request":   request,
        "has_config": bool(config),
        "theme":     "dark",
    })


@app.get("/config-page", response_class=HTMLResponse)
async def config_page(request: Request):
    config = load_config()
    return templates.TemplateResponse("config.html", {
        "request": request,
        "current_config": json.dumps(config, ensure_ascii=False),
    })


# ── API – konfiguracja ────────────────────────────────────────────────────────

@app.get("/api/config")
async def api_get_config():
    return load_config()


@app.post("/api/config")
async def api_set_config(request: Request):
    data = await request.json()
    if not isinstance(data, list):
        return JSONResponse({"error": "Oczekiwano listy bollardów"}, status_code=400)
    save_config(data)
    return {"ok": True, "count": len(data)}


# ── API – wyszukiwanie przystanków (GTFS) ────────────────────────────────────

@app.get("/api/stops/search")
async def search_stops(q: str = ""):
    """Wyszukaj przystanek po nazwie używając danych GTFS."""
    if len(q) < 2:
        return []
    try:
        gtfs_static.ensure_loaded()
        return gtfs_static.search_stops(q)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/stops/bollards")
async def get_bollards(stop_name: str = ""):
    """Pobierz bollardy dla przystanku używając danych GTFS."""
    if not stop_name:
        return []
    try:
        gtfs_static.ensure_loaded()
        return gtfs_static.get_bollards_for_stop(stop_name)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── API – odjazdy ─────────────────────────────────────────────────────────────

@app.get("/api/departures")
async def api_departures():
    """
    Zwróć odjazdy dla wszystkich skonfigurowanych bollardów.
    Format: [ { bollard: {...}, departures: [...] }, ... ]
    """
    config = load_config()
    if not config:
        return []

    # Liczba wierszy z konfiguracji każdego bollardu (domyślnie 2)
    # Fallback: automatyczne jeśli brak w config

    try:
        gtfs_static.ensure_loaded()
    except Exception as e:
        log.error("GTFS nie załadowany: %s", e)
        return JSONResponse({"error": str(e)}, status_code=503)

    result = []
    for bollard in config:
        symbol           = bollard.get("symbol", "")
        rows_per_bollard = max(1, min(5, int(bollard.get("rows", 2))))
        deps = gtfs_static.get_departures_for_stop(
            symbol, limit=MAX_DEPARTURES_PER_STOP
        )

        # Wzbogać o GTFS-RT
        try:
            gtfs_rt.enrich_departures(
                deps, gtfs_static.stop_code_to_id, symbol, gtfs_static)
        except Exception as e:
            log.warning("RT enrich błąd: %s", e)

        # Dodaj informacje o pojeździe i minuty
        for dep in deps:
            vid = dep.get("vehicle_id", "")
            dep["vehicle_info"] = gtfs_static.get_vehicle_info(vid) if vid else {}
            dep["minutes"] = _calc_minutes(
                dep["scheduled_departure"],
                dep.get("delay_seconds"),
                dep.get("scheduled_departure_str"),
            )

        result.append({
            "bollard":          bollard,
            "departures":       deps[:rows_per_bollard],
            "rows_per_bollard": rows_per_bollard,
            "error":            None,
        })

    return result


@app.get("/api/calendar")
async def api_calendar():
    """Zwróć nadchodzące wydarzenia z kalendarza iCal."""
    try:
        return calendar.get_upcoming(days_ahead=14)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/weather")
async def api_weather():
    """Zwróć aktualną pogodę i prognozę 3-dniową."""
    try:
        return weather.get_all()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/photo/random")
async def api_photo_random():
    """Zwróć dane losowego zdjęcia z Synology Photos."""
    if not synology.is_configured:
        return JSONResponse({"error": "Synology Photos nie skonfigurowany"}, status_code=503)
    photo = synology.get_random_photo()
    if not photo:
        return JSONResponse({"error": "Brak zdjęć"}, status_code=404)
    return photo


@app.get("/api/photo/{photo_id}")
async def api_photo_proxy(photo_id: int, cache_key: str = ""):
    """Proxy dla zdjęć Synology — ukrywa token sesji przed przeglądarką."""
    from fastapi.responses import Response
    data = synology.fetch_photo_bytes(photo_id, cache_key)
    if not data:
        return JSONResponse({"error": "Nie znaleziono zdjęcia"}, status_code=404)
    return Response(content=data, media_type="image/jpeg")


@app.get("/api/waste")
async def api_waste():
    """Zwróć najbliższe wywozy odpadów (3 dni do przodu)."""
    try:
        waste_schedule.ensure_loaded(rejon="V")
        return waste_schedule.get_upcoming(days_ahead=4)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/status")
async def api_status():
    return {
        "gtfs_valid_from":  str(gtfs_static._feed_start_date or "—"),
        "gtfs_valid_until": str(gtfs_static._feed_end_date   or "—"),
        "gtfs_loaded":      gtfs_static._loaded,
        "rt_last_update":   gtfs_rt.last_update,
        "rt_error":         gtfs_rt.error,
        "time":             datetime.now().strftime("%H:%M:%S"),
    }


# ── Helper ────────────────────────────────────────────────────────────────────

def _calc_minutes(scheduled: str, delay_seconds: Optional[int],
                  scheduled_str: Optional[str] = None) -> int:
    """Oblicz ile minut do odjazdu (z uwzględnieniem opóźnienia)."""
    try:
        from datetime import date, timedelta
        now = datetime.now()

        # Użyj scheduled_str jeśli dostępny (już przeliczony dla nocnych)
        # scheduled_str to HH:MM po przeliczeniu >24h
        time_str = scheduled_str if scheduled_str else scheduled
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0

        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        dep_dt = base + timedelta(hours=h, minutes=m, seconds=s)

        # Jeśli godzina odjazdu jest wcześniejsza niż teraz
        # i różnica > 12h — kurs jest na następny dzień
        diff = (dep_dt - now).total_seconds()
        if diff < -43200:  # -12h
            dep_dt += timedelta(days=1)
            diff = (dep_dt - now).total_seconds()

        # Dodaj opóźnienie
        if delay_seconds is not None:
            dep_dt += timedelta(seconds=delay_seconds)
            diff = (dep_dt - now).total_seconds()

        if diff < -60:
            return -1
        return max(0, int(diff // 60))
    except Exception:
        return -1
