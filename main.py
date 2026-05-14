from __future__ import annotations
"""
main.py – FastAPI backend tablicy PEKA
"""

import asyncio
import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import secrets

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gtfs_static import GTFSStatic
from gtfs_rt import GTFSRealtime
from waste_schedule import WasteSchedule
from photos import PhotoManager
from weather import Weather
from calendar_ical import CalendarICal
from sports import Sports

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONFIG_FILE       = Path("config.json")
BOARD_CONFIG_PATH = Path("board_config.json")
MAX_DEPARTURES_PER_STOP = 20

app = FastAPI(title="PEKA Board")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

_http_security = HTTPBasic()

gtfs_static    = GTFSStatic()
gtfs_rt        = GTFSRealtime()
waste_schedule = WasteSchedule()
photos         = PhotoManager()
weather        = Weather()
calendar       = CalendarICal()
sports_data    = Sports()


def _get_config_password() -> str:
    """Wczytaj hasło do config-page z board_config.json."""
    if BOARD_CONFIG_PATH.exists():
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            pwd = cfg.get("config_password", "")
            if pwd:
                return pwd
        except Exception:
            pass
    return "admin"  # domyślne, jeśli brak konfiguracji


def verify_auth(credentials: HTTPBasicCredentials = Depends(_http_security)):
    """Dependency sprawdzające HTTP Basic Auth dla config-page."""
    correct_password = _get_config_password()
    correct_username = "peka"
    ok = (
        secrets.compare_digest(credentials.username.encode(), correct_username.encode())
        and secrets.compare_digest(credentials.password.encode(), correct_password.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nieprawidłowe hasło",
            headers={"WWW-Authenticate": 'Basic realm="PEKA Board Config"'},
        )
    return credentials


def load_config() -> list[dict]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_config(data: list[dict]):
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
        photos.load_config()
        weather.load_config()
        calendar.load_config()
        sports_data.load_config()
    except Exception as e:
        log.error("Błąd ładowania konfiguracji: %s", e)
    asyncio.create_task(_gtfs_watcher())


async def _gtfs_watcher():
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


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = load_config()
    return templates.TemplateResponse(request, "index.html", {
        "has_config": bool(config),
        "theme":     "dark",
    })


@app.get("/light", response_class=HTMLResponse)
async def dashboard_light(request: Request):
    config = load_config()
    return templates.TemplateResponse(request, "index.html", {
        "has_config": bool(config),
        "theme":     "light",
    })


@app.get("/dark", response_class=HTMLResponse)
async def dashboard_dark(request: Request):
    config = load_config()
    return templates.TemplateResponse(request, "index.html", {
        "has_config": bool(config),
        "theme":     "dark",
    })


@app.get("/config-page", response_class=HTMLResponse)
async def config_page(request: Request, _auth=Depends(verify_auth)):
    config = load_config()
    return templates.TemplateResponse(request, "config.html", {
        "current_config": json.dumps(config, ensure_ascii=False),
    })


@app.get("/api/config")
async def api_get_config():
    return load_config()


@app.post("/api/config")
async def api_set_config(request: Request, _auth=Depends(verify_auth)):
    data = await request.json()
    if not isinstance(data, list):
        return JSONResponse({"error": "Oczekiwano listy bollardów"}, status_code=400)
    save_config(data)
    return {"ok": True, "count": len(data)}


# ── Ustawienia ogólne (sport / zdjęcia / kalendarz) ────────────────────────────

@app.get("/api/board-settings")
async def api_get_board_settings(_auth=Depends(verify_auth)):
    """Zwróć edytowalne sekcje board_config.json (bez hasła)."""
    cfg: dict = {}
    if BOARD_CONFIG_PATH.exists():
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "sports":   cfg.get("sports",   {}),
        "photos":   cfg.get("photos",   {}),
        "calendar": cfg.get("calendar", {}),
        "synology": cfg.get("synology", {}),
        "weather":  cfg.get("weather",  {"lat": 52.4064, "lon": 16.9252}),
        "board":    cfg.get("board",    {"max_bollards": 6, "max_rows": 16}),
    }


@app.post("/api/board-settings")
async def api_save_board_settings(request: Request, _auth=Depends(verify_auth)):
    """Zapisz sekcje konfiguracyjne do board_config.json i przeładuj moduły."""
    data = await request.json()
    # Wczytaj istniejący config lub zacznij od pustego słownika
    cfg: dict = {}
    if BOARD_CONFIG_PATH.exists():
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Nadpisz przesłane sekcje
    for section in ("sports", "photos", "calendar", "synology", "weather", "board"):
        if section in data:
            cfg[section] = data[section]
    # Hasło — tylko jeśli niepuste
    if data.get("config_password"):
        cfg["config_password"] = data["config_password"]
    BOARD_CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Przeładuj wszystkie moduły
    try:
        photos.load_config()
        weather.load_config()
        calendar.load_config()
        sports_data.load_config()
    except Exception as e:
        log.warning("Błąd przeładowania po zapisie ustawień: %s", e)
    return {"ok": True}


@app.get("/api/stops/search")
async def search_stops(q: str = ""):
    if len(q) < 2:
        return []
    try:
        gtfs_static.ensure_loaded()
        return gtfs_static.search_stops(q)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/stops/bollards")
async def get_bollards(stop_name: str = ""):
    if not stop_name:
        return []
    try:
        gtfs_static.ensure_loaded()
        return gtfs_static.get_bollards_for_stop(stop_name)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/departures")
async def api_departures():
    config = load_config()
    if not config:
        return []

    max_rows = 16
    if BOARD_CONFIG_PATH.exists():
        try:
            bcfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            max_rows = int(bcfg.get("board", {}).get("max_rows", 16))
        except Exception:
            pass

    total_rows = sum(max(1, min(5, int(b.get("rows", 2)))) for b in config)
    extra_rows = max(0, max_rows - total_rows)
    extra_per_bollard = extra_rows // len(config) if config else 0
    extra_remainder = extra_rows % len(config) if config else 0

    try:
        gtfs_static.ensure_loaded()
    except Exception as e:
        log.error("GTFS nie załadowany: %s", e)
        return JSONResponse({"error": str(e)}, status_code=503)

    result = []
    for i, bollard in enumerate(config):
        symbol           = bollard.get("symbol", "")
        rows_per_bollard = max(1, min(5, int(bollard.get("rows", 2))))
        deps = gtfs_static.get_departures_for_stop(
            symbol, limit=MAX_DEPARTURES_PER_STOP
        )

        try:
            gtfs_rt.enrich_departures(
                deps, gtfs_static.stop_code_to_id, symbol, gtfs_static)
        except Exception as e:
            log.warning("RT enrich błąd: %s", e)

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


@app.get("/api/board-config")
async def api_board_config():
    if BOARD_CONFIG_PATH.exists():
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            board = cfg.get("board", {})
            return {
                "max_bollards": int(board.get("max_bollards", 6)),
                "max_rows":     int(board.get("max_rows", 16)),
            }
        except Exception:
            pass
    return {"max_bollards": 6, "max_rows": 16}


@app.get("/api/calendar")
async def api_calendar():
    try:
        return calendar.get_upcoming(days_ahead=30)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/weather")
async def api_weather():
    try:
        return weather.get_all()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/sports")
async def api_sports():
    try:
        if sports_data.is_disabled:
            return {"disabled": True}
        if not sports_data.is_configured:
            return {"soccer": [], "nfl": [], "mlb": [], "nfl_team": "", "mlb_team": ""}
        return sports_data.get_all()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/sports/soccer-league")
async def api_soccer_league_info(id: str):
    """Zwróć nazwę ligi soccer na podstawie ID ESPN (np. eng.1, ned.2)."""
    try:
        url = f"https://site.api.espn.com/apis/v2/sports/soccer/{id}/standings"
        data = sports_data._espn_get(url)
        if not data:
            return JSONResponse({"error": "not found"}, status_code=404)
        name = data.get("name") or data.get("shortName") or id
        return {"id": id, "name": name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/sports/nfl-teams")
async def api_nfl_teams():
    try:
        return sports_data.get_team_suggestions("nfl")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/sports/mlb-teams")
async def api_mlb_teams():
    try:
        return sports_data.get_team_suggestions("mlb")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/sports/soccer-teams")
async def api_soccer_teams(league: str = ""):
    try:
        if not league:
            return []
        return sports_data.get_team_suggestions("soccer", league)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/sports/scores")
async def api_sports_scores():
    """Zwróć ostatni i następny mecz dla każdej drużyny."""
    try:
        if sports_data.is_disabled:
            return {}
        if not sports_data.is_configured:
            return {}
        return sports_data.get_scores()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/photo/random")
async def api_photo_random():
    if not photos.is_configured:
        return JSONResponse({"error": "Źródło zdjęć nie skonfigurowane"}, status_code=503)
    photo = photos.get_random_photo()
    if not photo:
        return JSONResponse({"error": "Brak zdjęć"}, status_code=404)
    return photo


@app.get("/api/photo/local/{filename}")
async def api_photo_local(filename: str):
    """Serwuj zdjęcie z lokalnego folderu."""
    from fastapi.responses import FileResponse
    import re
    # Zabezpieczenie przed path traversal
    if re.search(r"[/\\]|\.\.", filename):
        return JSONResponse({"error": "Nieprawidłowa nazwa pliku"}, status_code=400)
    if photos.local_path is None:
        return JSONResponse({"error": "Lokalny folder nie skonfigurowany"}, status_code=503)
    file_path = photos.local_path / filename
    if not file_path.is_file():
        return JSONResponse({"error": "Nie znaleziono pliku"}, status_code=404)
    return FileResponse(str(file_path))


@app.get("/api/photo/{photo_id}")
async def api_photo_proxy(photo_id: int, cache_key: str = ""):
    """Proxy zdjęcia z Synology."""
    from fastapi.responses import Response
    data = photos.fetch_photo_bytes(photo_id, cache_key)
    if not data:
        return JSONResponse({"error": "Nie znaleziono zdjęcia"}, status_code=404)
    return Response(content=data, media_type="image/jpeg")


@app.get("/api/waste")
async def api_waste():
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


def _calc_minutes(scheduled: str, delay_seconds: Optional[int],
                  scheduled_str: Optional[str] = None) -> int:
    try:
        from datetime import date, timedelta
        now = datetime.now()
        time_str = scheduled_str if scheduled_str else scheduled
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        dep_dt = base + timedelta(hours=h, minutes=m, seconds=s)
        diff = (dep_dt - now).total_seconds()
        if diff < -43200:
            dep_dt += timedelta(days=1)
            diff = (dep_dt - now).total_seconds()
        if delay_seconds is not None:
            dep_dt += timedelta(seconds=delay_seconds)
            diff = (dep_dt - now).total_seconds()
        if diff < -60:
            return -1
        return max(0, int(diff // 60))
    except Exception:
        return -1
