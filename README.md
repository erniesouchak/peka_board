# PEKA Board – Tablica odjazdów ZTM Poznań

Dashboard webowy na tablicę w salonie. Dane z oficjalnych źródeł ZTM Poznań (GTFS + GTFS-RT).

## Instalacja

```bash
pip install -r requirements.txt
```

## Uruchomienie

```bash
uvicorn main:app --host 0.0.0.0 --port 8080
```

Otwórz przeglądarkę: `http://localhost:8080`

## Pierwsze uruchomienie

1. Przy starcie aplikacja automatycznie pobiera paczkę GTFS (~10MB) — może chwilę potrwać.
2. Wejdź na `http://localhost:8080` → pojawi się ekran konfiguracji.
3. Wyszukaj przystanek, dodaj bollardy (maks. 6), zapisz.
4. Tablica gotowa — odświeża dane RT co 60 sekund.

## Tryb kiosk na Raspberry Pi (Chromium)

```bash
# Autostart Chromium w trybie kiosk
chromium-browser --kiosk --noerrdialogs --disable-infobars \
  --incognito http://localhost:8080
```

Dodaj do `/etc/xdg/lxsession/LXDE-pi/autostart`:
```
@uvicorn main:app --host 0.0.0.0 --port 8080
@chromium-browser --kiosk http://localhost:8080
```

## Struktura plików

```
peka_board/
├── main.py              # FastAPI – endpointy
├── gtfs_static.py       # Parsowanie GTFS statycznego
├── gtfs_rt.py           # Parsowanie GTFS-RT (protobuf)
├── requirements.txt
├── config.json          # Twoja konfiguracja bollardów (auto-generowany)
├── gtfs_cache.zip       # Cache GTFS (auto-pobierany)
├── static/
│   ├── style.css
│   └── app.js
└── templates/
    ├── index.html       # Dashboard
    └── config.html      # Strona konfiguracji
```

## API

| Endpoint | Opis |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /config-page` | Strona konfiguracji |
| `GET /api/departures` | Odjazdy JSON |
| `GET /api/status` | Status GTFS/RT |
| `GET /api/config` | Odczyt konfiguracji |
| `POST /api/config` | Zapis konfiguracji |
| `GET /api/stops/search?q=` | Wyszukiwanie przystanków |
| `GET /api/stops/bollards?stop_name=` | Bollardy dla przystanku |

## Planowane rozszerzenia

- [ ] Widget pogody (Open-Meteo)
- [ ] Widget kalendarza (Google Calendar / iCal)
- [ ] Integracja z Home Assistant
- [ ] Powiadomienia push (pojazd za X minut)
- [ ] Sterowanie ekranem przez PIR
