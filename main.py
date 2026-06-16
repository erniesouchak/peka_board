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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gtfs_static import GTFSStatic
from gtfs_rt import GTFSRealtime
from peka_vm import PekaVM
from waste_schedule import WasteSchedule
from photos import PhotoManager
from weather import Weather
from calendar_ical import CalendarICal
from sports import Sports

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONFIG_FILE       = Path("config.json")
BOARD_CONFIG_PATH = Path("board_config.json")
BOARDS_PATH       = Path("boards.json")
MAX_DEPARTURES_PER_STOP = 20
MAX_BOARDS        = 3

app = FastAPI(title="PEKA Board")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def _initial_theme() -> str:
    dark_from, dark_until = 21, 7
    if BOARD_CONFIG_PATH.exists():
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            t = cfg.get("theme", {})
            dark_from = int(t.get("dark_from", 21))
            dark_until = int(t.get("dark_until", 7))
        except Exception:
            pass
    h = datetime.now().hour
    return "dark" if h >= dark_from or h < dark_until else "light"

_current_theme: str = _initial_theme()
_theme_subscribers: list[asyncio.Queue] = []

# Tablice (a'la HA dashboard) — układ + aktywna tablica
_board_subscribers: list[asyncio.Queue] = []

_http_security = HTTPBasic()

gtfs_static    = GTFSStatic()
gtfs_rt        = GTFSRealtime()
peka_vm        = PekaVM()
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


# ── Tablice ─────────────────────────────────────────────────────────────────────

def _default_boards() -> dict:
    return {"active": 0, "boards": []}


def load_boards() -> dict:
    if BOARDS_PATH.exists():
        try:
            data = json.loads(BOARDS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("boards"), list):
                data.setdefault("active", 0)
                return data
        except Exception as e:
            log.warning("Błąd wczytywania boards.json: %s", e)
    return _default_boards()


def save_boards(data: dict):
    BOARDS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _broadcast_boards(event: dict):
    """Wyślij event do wszystkich subskrybentów strumienia tablic (SSE)."""
    for q in _board_subscribers:
        await q.put(event)


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
    asyncio.create_task(_theme_watcher())


async def _theme_watcher():
    try:
        while True:
            await asyncio.sleep(60)
            expected = _initial_theme()
            global _current_theme
            if expected != _current_theme:
                _current_theme = expected
                for q in _theme_subscribers:
                    await q.put(expected)
                log.info("Auto-zmiana motywu → %s", expected)
    except asyncio.CancelledError:
        log.debug("Theme watcher zatrzymany.")


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
    boards = load_boards()
    return templates.TemplateResponse(request, "index.html", {
        "has_boards": bool(boards.get("boards")),
        "theme":      _current_theme,
    })


@app.get("/light")
async def dashboard_light():
    global _current_theme
    _current_theme = "light"
    for q in _theme_subscribers:
        await q.put("light")
    return RedirectResponse("/", status_code=302)


@app.get("/dark")
async def dashboard_dark():
    global _current_theme
    _current_theme = "dark"
    for q in _theme_subscribers:
        await q.put("dark")
    return RedirectResponse("/", status_code=302)


@app.get("/set-theme")
async def set_theme(theme: str = "dark"):
    global _current_theme
    if theme not in ("dark", "light"):
        return JSONResponse({"error": "Nieprawidłowy motyw"}, status_code=400)
    _current_theme = theme
    for q in _theme_subscribers:
        await q.put(theme)
    log.info("Zmiana motywu na: %s", theme)
    return {"ok": True, "theme": theme}


@app.get("/api/theme-stream")
async def theme_stream():
    queue: asyncio.Queue = asyncio.Queue()
    _theme_subscribers.append(queue)

    async def generator():
        try:
            yield f"data: {_current_theme}\n\n"
            while True:
                t = await queue.get()
                yield f"data: {t}\n\n"
        finally:
            _theme_subscribers.remove(queue)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


# ── Tablice (dashboard a'la HA) ────────────────────────────────────────────────

@app.get("/api/boards")
async def api_get_boards():
    """Pełna konfiguracja tablic (edytor + viewer)."""
    return load_boards()


@app.post("/api/boards")
async def api_save_boards(request: Request, _auth=Depends(verify_auth)):
    """Zapisz układ tablic i powiadom kioski przez SSE (reload)."""
    data = await request.json()
    if not isinstance(data, dict) or not isinstance(data.get("boards"), list):
        return JSONResponse({"error": "Oczekiwano obiektu z listą 'boards'"}, status_code=400)
    boards = data["boards"][:MAX_BOARDS]
    active = int(data.get("active", 0))
    active = max(0, min(active, len(boards) - 1)) if boards else 0
    cfg = {
        "active": active,
        "boards": boards,
    }
    save_boards(cfg)
    await _broadcast_boards({"type": "reload", "active": active})
    return {"ok": True, "count": len(boards), "active": active}


@app.get("/api/active-board")
async def api_get_active_board():
    return {"active": load_boards().get("active", 0)}


@app.post("/api/active-board")
async def api_set_active_board(request: Request):
    """Ustaw aktywną tablicę. Body: {"index": N} lub {"delta": +1/-1}.

    Bez auth — żeby keypad / timer / zdalne wywołania były proste w sieci lokalnej.
    """
    data = await request.json()
    cfg = load_boards()
    n = len(cfg.get("boards", []))
    if n == 0:
        return JSONResponse({"error": "Brak skonfigurowanych tablic"}, status_code=409)
    cur = int(cfg.get("active", 0))
    if "index" in data:
        new = int(data["index"])
    elif "delta" in data:
        new = cur + int(data["delta"])
    else:
        return JSONResponse({"error": "Podaj 'index' lub 'delta'"}, status_code=400)
    new = new % n  # zawijanie (next/prev działa w pętli)
    cfg["active"] = new
    save_boards(cfg)
    await _broadcast_boards({"type": "active", "active": new})
    log.info("Aktywna tablica → %d", new)
    return {"ok": True, "active": new}


@app.get("/api/board-stream")
async def board_stream():
    """SSE — wypycha zmiany aktywnej tablicy oraz zapisy układu (kalka theme_stream)."""
    queue: asyncio.Queue = asyncio.Queue()
    _board_subscribers.append(queue)

    async def generator():
        try:
            yield f"data: {json.dumps({'type': 'active', 'active': load_boards().get('active', 0)})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            _board_subscribers.remove(queue)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
        "theme":    cfg.get("theme",    {"dark_from": 21, "dark_until": 7}),
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
    for section in ("sports", "photos", "calendar", "synology", "weather", "board", "theme"):
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


@app.get("/api/debug/rt")
async def api_debug_rt(stop: str = ""):
    """Diagnostyka RT: porównaj trip_idy z RT feedu z overnight odjazdami na przystanku."""
    try:
        gtfs_rt.refresh_if_stale()
        overnight_trips = []
        if stop:
            gtfs_static.ensure_loaded()
            deps = gtfs_static.get_departures_for_stop(stop, limit=20)
            overnight_trips = [
                {
                    "trip_id": d["trip_id"],
                    "rt_id":   d["trip_id"].replace("prev_", "", 1),
                    "line":    d["line"],
                    "time":    d["scheduled_departure_str"],
                    "found_in_rt": d["trip_id"].replace("prev_", "", 1) in gtfs_rt._trip_vehicles,
                }
                for d in deps if d.get("overnight")
            ]
        sample_vehicles = [
            {"trip_id": tid, "label": lbl, "vehicle_id": vid}
            for tid, (lbl, vid, _) in list(gtfs_rt._trip_vehicles.items())[:30]
        ]
        return {"rt_count": len(gtfs_rt._trip_vehicles), "rt_vehicles": sample_vehicles,
                "overnight_departures": overnight_trips,
                "overnight_mapped": len(getattr(gtfs_static, "_overnight_trip_map", {}))}
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


def build_stop_departures(symbol: str, rows: int) -> list[dict]:
    """Pobierz i wzbogać odjazdy dla jednego słupka (encja 'transit')."""
    rows_per_bollard = max(1, min(5, int(rows)))
    deps = gtfs_static.get_departures_for_stop(symbol, limit=MAX_DEPARTURES_PER_STOP)

    try:
        gtfs_rt.enrich_departures(deps, gtfs_static)
    except Exception as e:
        log.warning("RT enrich błąd: %s", e)

    try:
        peka_vm.enrich_missing(deps, symbol)
    except Exception as e:
        log.warning("VM enrich błąd: %s", e)

    for dep in deps:
        if not dep.get("realtime"):
            dep["current_stop"] = "brak danych"
        vid = dep.get("vehicle_id", "")
        static_info = gtfs_static.get_vehicle_info(vid) if vid else {}
        dep["vehicle_info"] = static_info if static_info else dep.get("vehicle_info", {})
        dep["minutes"] = _calc_minutes(
            dep["scheduled_departure"],
            dep.get("delay_seconds"),
            dep.get("scheduled_departure_str"),
        )

    return deps[:rows_per_bollard]


@app.get("/api/departures/{symbol}")
async def api_departures_stop(symbol: str, rows: int = 3):
    """Odjazdy dla pojedynczego słupka — encja 'transit' na tablicy."""
    if not symbol:
        return JSONResponse({"error": "Brak symbolu słupka"}, status_code=400)
    try:
        gtfs_static.ensure_loaded()
    except Exception as e:
        log.error("GTFS nie załadowany: %s", e)
        return JSONResponse({"error": str(e)}, status_code=503)
    rows_per_bollard = max(1, min(5, int(rows)))
    deps = build_stop_departures(symbol, rows_per_bollard)
    return {
        "symbol":           symbol,
        "departures":       deps,
        "rows_per_bollard": rows_per_bollard,
        "error":            None,
    }


@app.get("/api/board-config")
async def api_board_config():
    if BOARD_CONFIG_PATH.exists():
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            board = cfg.get("board", {})
            photos_cfg = cfg.get("photos", {})
            return {
                "max_bollards":       int(board.get("max_bollards", 6)),
                "max_rows":           int(board.get("max_rows", 16)),
                "photo_interval_min": int(photos_cfg.get("photo_interval_min", 5)),
            }
        except Exception:
            pass
    return {"max_bollards": 6, "max_rows": 16, "photo_interval_min": 5}


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
        "vm_last_update":   peka_vm.last_update,
        "vm_error":         peka_vm.error,
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
