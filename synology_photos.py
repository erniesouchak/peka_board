"""
synology_photos.py – integracja z Synology Photos API

Konfiguracja w pliku board_config.json:
{
  "synology": {
    "url":      "http://192.168.1.100:5000",
    "username": "admin",
    "password": "haslo",
    "album":    "Tablica"
  }
}

API Synology Photos używa SYNO.API.Auth do logowania
i SYNO.Foto.Browse.Item do pobierania zdjęć z albumu.
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

BOARD_CONFIG_PATH = Path("board_config.json")
SESSION_CACHE     = Path("synology_session.json")


class SynologyPhotos:
    def __init__(self):
        self._url:      str = ""
        self._username: str = ""
        self._password: str = ""
        self._album:    str = ""
        self._album_id: int = 0
        self._token:    str = ""
        self._photo_ids: list[int] = []
        self._last_fetch: float = 0.0
        self._current_photo_id: Optional[int] = None
        self._current_photo_url: Optional[str] = None
        self._photo_changed_at: float = 0.0
        self._configured = False

    def load_config(self):
        """Wczytaj konfigurację z board_config.json."""
        if not BOARD_CONFIG_PATH.exists():
            log.info("Brak board_config.json — moduł zdjęć wyłączony")
            return

        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            syn = cfg.get("synology", {})
            self._url      = syn.get("url", "").rstrip("/")
            self._username = syn.get("username", "")
            self._password = syn.get("password", "")
            self._album    = syn.get("album", "")
            self._album_id = int(syn.get("album_id", 0))
            self._configured = bool(self._url and self._username and self._album)
            if self._configured:
                log.info("Synology Photos skonfigurowany: %s / album: %s",
                         self._url, self._album)
        except Exception as e:
            log.warning("Błąd odczytu konfiguracji Synology: %s", e)

    # ── Autentykacja ──────────────────────────────────────────────────────────

    def _login(self) -> bool:
        """Zaloguj się i zapisz token sesji."""
        try:
            r = requests.get(
                f"{self._url}/webapi/auth.cgi",
                params={
                    "api":     "SYNO.API.Auth",
                    "version": "3",
                    "method":  "login",
                    "account": self._username,
                    "passwd":  self._password,
                    "session": "PEKABoard",
                    "format":  "sid",
                },
                timeout=10,
                verify=False,
            )
            data = r.json()
            if data.get("success"):
                self._token = data["data"]["sid"]
                SESSION_CACHE.write_text(
                    json.dumps({"sid": self._token, "ts": time.time()}),
                    encoding="utf-8"
                )
                log.info("Synology Photos: zalogowano pomyślnie")
                return True
            else:
                log.warning("Synology Photos: błąd logowania: %s", data)
                return False
        except Exception as e:
            log.warning("Synology Photos: błąd połączenia: %s", e)
            return False

    def _ensure_token(self) -> bool:
        """Upewnij się że mamy ważny token."""
        if self._token:
            return True
        # Spróbuj z cache
        if SESSION_CACHE.exists():
            try:
                data = json.loads(SESSION_CACHE.read_text(encoding="utf-8"))
                if time.time() - data.get("ts", 0) < 86400:  # 24h
                    self._token = data["sid"]
                    return True
            except Exception:
                pass
        return self._login()

    # ── Pobieranie listy zdjęć ────────────────────────────────────────────────

    def _get_album_id(self) -> Optional[int]:
        """Znajdź ID albumu po nazwie."""
        try:
            r = requests.get(
                f"{self._url}/webapi/entry.cgi",
                params={
                    "api":     "SYNO.Foto.Browse.Album",
                    "version": "1",
                    "method":  "list",
                    "offset":  0,
                    "limit":   200,
                    "_sid":    self._token,
                },
                timeout=10,
                verify=False,
            )
            data = r.json()
            if not data.get("success"):
                return None
            for album in data["data"].get("list", []):
                if album.get("name") == self._album:
                    return album["id"]
            log.warning("Synology Photos: nie znaleziono albumu '%s'", self._album)
            return None
        except Exception as e:
            log.warning("Synology Photos: błąd pobierania albumów: %s", e)
            return None

    def _fetch_photo_list(self):
        """Pobierz listę ID zdjęć z albumu."""
        if not self._ensure_token():
            return

        # Użyj album_id z konfiguracji jeśli podany, inaczej szukaj po nazwie
        if self._album_id:
            album_id = self._album_id
        else:
            album_id = self._get_album_id()

        if not album_id:
            return

        try:
            photo_ids = []
            offset = 0
            limit  = 500

            while True:
                r = requests.get(
                    f"{self._url}/webapi/entry.cgi",
                    params={
                        "api":        "SYNO.Foto.Browse.Item",
                        "version":    "1",
                        "method":     "list",
                        "album_id":   album_id,
                        "offset":     offset,
                        "limit":      limit,
                        "sort_by":    "filename",
                        "sort_direction": "asc",
                        "additional": '["thumbnail"]',
                        "_sid":       self._token,
                    },
                    timeout=15,
                    verify=False,
                )
                data = r.json()
                if not data.get("success"):
                    break

                items = data["data"].get("list", [])
                for item in items:
                    if item.get("type") == "photo":
                        photo_ids.append({
                            "id":        item["id"],
                            "cache_key": item.get("additional", {}).get(
                                "thumbnail", {}).get("cache_key", ""),
                        })
                if len(items) < limit:
                    break
                offset += limit

            self._photo_ids = photo_ids
            self._last_fetch = time.time()
            log.info("Synology Photos: załadowano %d zdjęć z albumu '%s'",
                     len(photo_ids), self._album)
        except Exception as e:
            log.warning("Synology Photos: błąd pobierania zdjęć: %s", e)

    # ── URL zdjęcia ───────────────────────────────────────────────────────────

    def get_photo_url(self, photo_id: int, cache_key: str = "", size: str = "xl") -> str:
        """Zwróć URL do miniatury zdjęcia."""
        params = (
            f"?api=SYNO.Foto.Thumbnail"
            f"&version=1"
            f"&method=get"
            f"&id={photo_id}"
            f"&type=unit"
            f"&size={size}"
            f"&cache_key={cache_key}"
            f"&_sid={self._token}"
        )
        return f"{self._url}/webapi/entry.cgi{params}"

    # ── Publiczne API ─────────────────────────────────────────────────────────

    def get_random_photo(self) -> Optional[dict]:
        """
        Zwróć dane losowego zdjęcia.
        Odświeża listę zdjęć jeśli pierwsza sesja lub po 24h.
        """
        if not self._configured:
            return None

        # Odśwież listę zdjęć jeśli pusta lub stara (24h)
        if not self._photo_ids or time.time() - self._last_fetch > 86400:
            self._fetch_photo_list()

        if not self._photo_ids:
            return None

        photo = random.choice(self._photo_ids)
        photo_id  = photo["id"]
        cache_key = photo.get("cache_key", "")
        url = self.get_photo_url(photo_id, cache_key)

        return {
            "photo_id":  photo_id,
            "cache_key": cache_key,
            "url":       url,
            "proxy_url": f"/api/photo/{photo_id}?cache_key={cache_key}",
        }

    def fetch_photo_bytes(self, photo_id: int, cache_key: str = "") -> Optional[bytes]:
        """Pobierz bajty zdjęcia (przez proxy żeby nie ujawniać tokenu)."""
        if not self._ensure_token():
            return None
        try:
            r = requests.get(
                self.get_photo_url(photo_id, cache_key),
                timeout=15,
                verify=False,
            )
            if r.status_code == 200:
                return r.content
            if r.status_code in (401, 403):
                self._token = ""
                SESSION_CACHE.unlink(missing_ok=True)
                if self._login():
                    r2 = requests.get(
                        self.get_photo_url(photo_id, cache_key),
                        timeout=15, verify=False)
                    if r2.status_code == 200:
                        return r2.content
        except Exception as e:
            log.warning("Synology Photos: błąd pobierania zdjęcia %d: %s",
                        photo_id, e)
        return None

    @property
    def is_configured(self) -> bool:
        return self._configured

    @property
    def photo_count(self) -> int:
        return len(self._photo_ids)
