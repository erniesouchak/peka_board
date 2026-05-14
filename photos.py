"""
photos.py – menedżer źródeł zdjęć dla PEKA Board

Obsługuje trzy źródła:
  "synology"     – publiczny album Synology Photos (istniejący moduł)
  "local_folder" – losowe zdjęcie z lokalnego folderu
  "url_list"     – losowy URL z podanej listy

Konfiguracja w board_config.json:
{
  "photos": {
    "source": "synology",          // lub "local_folder" / "url_list"
    "local_path": "/home/ernie/photos",
    "urls": ["https://..."]
  },
  "synology": { "url": "...", "passphrase": "..." }
}

Jeśli sekcja "photos" jest nieobecna, a "synology" jest skonfigurowany,
zachowanie jest wsteczne-kompatybilne (używa Synology).
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Optional

from synology_photos import SynologyPhotos

log = logging.getLogger(__name__)

BOARD_CONFIG_PATH = Path("board_config.json")

# Rozszerzenia traktowane jako obrazy przy skanowaniu lokalnego folderu
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


class PhotoManager:
    """Fasada łącząca różne źródła zdjęć."""

    def __init__(self):
        self._source: str = "synology"
        self._local_path: Path | None = None
        self._url_list: list[str] = []
        self._synology = SynologyPhotos()
        self._configured = False

    # ── Konfiguracja ──────────────────────────────────────────────────────────

    def load_config(self):
        """Wczytaj konfigurację z board_config.json."""
        if not BOARD_CONFIG_PATH.exists():
            log.info("Brak board_config.json — moduł zdjęć wyłączony")
            return
        try:
            cfg = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
            photos_cfg = cfg.get("photos", {})
            self._source = photos_cfg.get("source", "synology")

            if self._source == "local_folder":
                path_str = photos_cfg.get("local_path", "")
                if path_str:
                    self._local_path = Path(path_str)
                    self._configured = self._local_path.is_dir()
                    if not self._configured:
                        log.warning("Zdjęcia: folder lokalny nie istnieje: %s", path_str)
                    else:
                        log.info("Zdjęcia: lokalny folder %s", self._local_path)
                else:
                    log.warning("Zdjęcia: brak local_path w konfiguracji")

            elif self._source == "url_list":
                self._url_list = photos_cfg.get("urls", [])
                self._configured = bool(self._url_list)
                if self._configured:
                    log.info("Zdjęcia: lista URL (%d pozycji)", len(self._url_list))
                else:
                    log.warning("Zdjęcia: pusta lista URL")

            else:  # domyślnie "synology"
                self._source = "synology"
                self._synology.load_config()
                self._configured = self._synology.is_configured

        except Exception as e:
            log.warning("Błąd odczytu konfiguracji zdjęć: %s", e)

    # ── Publiczne API ─────────────────────────────────────────────────────────

    def get_random_photo(self) -> Optional[dict]:
        """
        Zwróć słownik opisujący losowe zdjęcie.
        Zawsze zawiera klucz 'proxy_url' — URL do pobrania obrazu przez klienta.
        """
        if not self._configured:
            return None

        if self._source == "local_folder":
            return self._get_local_photo()
        elif self._source == "url_list":
            return self._get_url_photo()
        else:
            return self._synology.get_random_photo()

    def fetch_photo_bytes(self, photo_id: int, cache_key: str = "") -> Optional[bytes]:
        """Proxy dla Synology — pobiera bajty zdjęcia."""
        return self._synology.fetch_photo_bytes(photo_id, cache_key)

    # ── Źródła ───────────────────────────────────────────────────────────────

    def _get_local_photo(self) -> Optional[dict]:
        """Zwróć losowe zdjęcie z lokalnego folderu."""
        if not self._local_path or not self._local_path.is_dir():
            return None
        images = [
            f for f in self._local_path.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS
        ]
        if not images:
            log.warning("Zdjęcia: brak plików obrazów w %s", self._local_path)
            return None
        chosen = random.choice(images)
        return {
            "proxy_url": f"/api/photo/local/{chosen.name}",
            "source":    "local",
        }

    def _get_url_photo(self) -> Optional[dict]:
        """Zwróć losowy URL z listy."""
        if not self._url_list:
            return None
        url = random.choice(self._url_list)
        return {
            "proxy_url": url,
            "source":    "url",
        }

    # ── Właściwości ───────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return self._configured

    @property
    def source(self) -> str:
        return self._source

    @property
    def local_path(self) -> Optional[Path]:
        return self._local_path
