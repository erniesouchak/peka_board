"""
synology_photos.py – integracja z Synology Photos przez publiczny album

Używa passphrase udostępnionego albumu — bez logowania, bez hasła.
Album musi być udostępniony publicznie w Synology Photos.

Konfiguracja w board_config.json:
{
  "synology": {
    "url":        "http://192.168.1.100:5000",
    "passphrase": "IxIVFeayG"
  }
}
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


class SynologyPhotos:
    def __init__(self):
        self._url:        str = ""
        self._passphrase: str = ""
        self._sharing_sid: str = ""
        self._photo_list: list[dict] = []
        self._last_fetch: float = 0.0
        self._configured = False

    def load_config(self):
        """Wczytaj konfigurację z board_config.json."""
        if not BOARD_CONFIG_PATH.exists():
            log.info("Brak board_config.json — moduł zdjęć wyłączony")
            return
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            syn = cfg.get("synology", {})
            self._url        = syn.get("url", "").rstrip("/")
            self._passphrase = syn.get("passphrase", "")
            self._configured = bool(self._url and self._passphrase)
            if self._configured:
                log.info("Synology Photos skonfigurowany: %s (passphrase: %s)",
                         self._url, self._passphrase)
        except Exception as e:
            log.warning("Błąd odczytu konfiguracji Synology: %s", e)

    # ── Sesja sharing ─────────────────────────────────────────────────────────

    def _get_sharing_sid(self) -> bool:
        """Pobierz sharing_sid dla publicznego albumu."""
        try:
            r = requests.post(
                f"{self._url}/webapi/entry.cgi",
                data={
                    "api":        "SYNO.Core.Sharing.Login",
                    "method":     "login",
                    "version":    "1",
                    "sharing_id": self._passphrase,
                },
                timeout=10,
                verify=False,
            )
            # sharing_sid jest w cookie
            self._sharing_sid = r.cookies.get("sharing_sid", "")
            if self._sharing_sid:
                log.info("Synology Photos: uzyskano sharing_sid")
                return True
            log.warning("Synology Photos: brak sharing_sid w odpowiedzi")
            return False
        except Exception as e:
            log.warning("Synology Photos: błąd pobierania sharing_sid: %s", e)
            return False

    # ── Lista zdjęć ───────────────────────────────────────────────────────────

    def _fetch_photo_list(self):
        """Pobierz listę zdjęć z publicznego albumu."""
        if not self._sharing_sid:
            if not self._get_sharing_sid():
                return
        # NIE odświeżaj sid jeśli już mamy — używaj tego samego

        try:
            photo_list = []
            offset = 0
            limit  = 500

            while True:
                r = requests.post(
                    f"{self._url}/mo/sharing/webapi/entry.cgi",
                    headers={
                        "x-syno-sharing": self._passphrase,
                        "Cookie": f"sharing_sid={self._sharing_sid}",
                    },
                    data={
                        "api":        "SYNO.Foto.Browse.Item",
                        "method":     "list",
                        "version":    "1",
                        "passphrase": self._passphrase,
                        "offset":     offset,
                        "limit":      limit,
                        "additional": '["thumbnail"]',
                    },
                    timeout=15,
                    verify=False,
                )
                data = r.json()
                if not data.get("success"):
                    log.warning("Synology Photos: błąd listowania: %s", data)
                    break

                items = data["data"].get("list", [])
                for item in items:
                    if item.get("type") in ("photo", "live"):
                        cache_key = (item.get("additional", {})
                                     .get("thumbnail", {})
                                     .get("cache_key", ""))
                        photo_list.append({
                            "id":        item["id"],
                            "cache_key": cache_key,
                        })

                if len(items) < limit:
                    break
                offset += limit

            self._photo_list = photo_list
            self._last_fetch = time.time()
            log.info("Synology Photos: załadowano %d zdjęć", len(photo_list))

        except Exception as e:
            log.warning("Synology Photos: błąd pobierania listy: %s", e)

    # ── Publiczne API ─────────────────────────────────────────────────────────

    def get_random_photo(self) -> Optional[dict]:
        """Zwróć dane losowego zdjęcia."""
        if not self._configured:
            return None

        # Odśwież listę co 24h
        if not self._photo_list or time.time() - self._last_fetch > 86400:
            self._fetch_photo_list()

        if not self._photo_list:
            return None

        photo = random.choice(self._photo_list)
        return {
            "photo_id":  photo["id"],
            "cache_key": photo["cache_key"],
            "proxy_url": f"/api/photo/{photo['id']}?cache_key={photo['cache_key']}",
        }

    def fetch_photo_bytes(self, photo_id: int, cache_key: str = "") -> Optional[bytes]:
        """Pobierz bajty zdjęcia przez proxy."""
        if not self._sharing_sid:
            if not self._get_sharing_sid():
                return None
        log.debug("Pobieranie zdjęcia %d, sid=%s...", photo_id, self._sharing_sid[:10])
        try:
            r = requests.get(
                f"{self._url}/mo/sharing/webapi/entry.cgi",
                headers={
                    "x-syno-sharing": self._passphrase,
                    "Cookie": f"sharing_sid={self._sharing_sid}",
                },
                params={
                    "api":        "SYNO.Foto.Thumbnail",
                    "method":     "get",
                    "version":    "1",
                    "id":         photo_id,
                    "cache_key":  cache_key,
                    "type":       "unit",
                    "size":       "xl",
                    "passphrase": self._passphrase,
                },
                timeout=15,
                verify=False,
            )
            log.debug("Odpowiedź Synology: %d %s", r.status_code, r.headers.get("content-type",""))
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return r.content
            # Loguj co zwrócił Synology zamiast ślepo resetować sid
            log.warning("Synology nieoczekiwana odpowiedź: %d %s %s",
                        r.status_code, r.headers.get("content-type",""), r.content[:200])
            # Spróbuj odświeżyć sid tylko gdy 401/403
            if r.status_code in (401, 403):
                self._sharing_sid = ""
                if self._get_sharing_sid():
                    r2 = requests.get(
                        f"{self._url}/mo/sharing/webapi/entry.cgi",
                        headers={
                            "x-syno-sharing": self._passphrase,
                            "Cookie": f"sharing_sid={self._sharing_sid}",
                        },
                        params={
                            "api":        "SYNO.Foto.Thumbnail",
                            "method":     "get",
                            "version":    "1",
                            "id":         photo_id,
                            "cache_key":  cache_key,
                            "type":       "unit",
                            "size":       "xl",
                            "passphrase": self._passphrase,
                        },
                        timeout=15,
                        verify=False,
                    )
                    if r2.status_code == 200 and r2.headers.get("content-type", "").startswith("image"):
                        return r2.content
        except Exception as e:
            log.warning("Synology Photos: błąd pobierania zdjęcia %d: %s", photo_id, e)
        return None

    @property
    def is_configured(self) -> bool:
        return self._configured

    @property
    def photo_count(self) -> int:
        return len(self._photo_list)
