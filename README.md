# PEKA Board

Tablica odjazdów komunikacji miejskiej Poznania (ZTM) na Raspberry Pi 5, wyświetlana w przeglądarce w trybie kiosk.

## Funkcje

- **Odjazdy ZTM Poznań** — dane GTFS statyczne + GTFS-RT (live, opóźnienia)
- **Pogoda** — aktualna + prognoza 3-dniowa (Open-Meteo, bez klucza API)
- **Kalendarz** — iCal (Google Calendar, Apple Calendar, inne)
- **Zdjęcia** — losowe zdjęcia z Synology Photos (publiczny album)
- **Wywóz odpadów** — harmonogram dla rejonu V (kom-lub.com.pl)
- **Jasny i ciemny motyw** — `/light` i `/dark`

## Sprzęt

- Raspberry Pi 5 4GB w obudowie Argon ONE V3
- Monitor 24" (iiyama X2483HSU) podłączony przez HDMI
- Karta microSD 64GB
- Mysz USB (wymagana dla ukrycia kursora)

## Wymagania systemowe

```bash
sudo apt install curl wtype unclutter -y
pip install -r requirements.txt --break-system-packages
```

## Instalacja

```bash
git clone https://github.com/erniesouchak/peka_board.git
cd peka_board
pip install -r requirements.txt --break-system-packages
cp board_config.json.example board_config.json
nano board_config.json  # uzupełnij dane
```

## Konfiguracja

### board_config.json

```json
{
  "synology": {
    "url": "http://192.168.1.100:5000",
    "passphrase": "TwojPassphrase"
  },
  "weather": {
    "lat": 52.345,
    "lon": 16.875
  },
  "calendar": {
    "ical_urls": [
      "https://calendar.google.com/calendar/ical/...",
      "https://p71-caldav.icloud.com/published/2/..."
    ]
  },
  "board": {
    "max_bollards": 6,
    "max_rows": 16
  }
}
```

### Bollardy (przystanki)

Wejdź na `http://localhost:8080/config-page` i skonfiguruj przystanki przez interfejs webowy.

## Uruchomienie

```bash
cd ~/peka_board
uvicorn main:app --host 0.0.0.0 --port 8080
```

Tablica dostępna pod `http://localhost:8080/light`

## Autostart na Raspberry Pi

### 1. Serwis systemd

```bash
sudo nano /etc/systemd/system/pekaboard.service
```

```ini
[Unit]
Description=PEKA Board
After=network.target

[Service]
User=ernie
WorkingDirectory=/home/ernie/peka_board
ExecStartPre=/bin/sleep 15
ExecStart=/home/ernie/.local/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable pekaboard
sudo systemctl start pekaboard
```

### 2. Autostart Chromium

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/pekaboard.desktop
```

```ini
[Desktop Entry]
Type=Application
Name=PEKA Board
Exec=bash -c "until curl -sf http://localhost:8080 > /dev/null 2>&1; do sleep 3; done && chromium --kiosk --password-store=basic --disable-gpu-vsync --disable-features=Translate http://localhost:8080/light"
```

### 3. Ukrycie kursora (labwc + wtype)

```bash
nano ~/.config/labwc/rc.xml
```

```xml
<?xml version="1.0"?>
<labwc_config>
  <keyboard>
    <keybind key="A-W-h">
      <action name="HideCursor" />
      <action name="WarpCursor" x="-1" y="-1" />
    </keybind>
  </keyboard>
</labwc_config>
```

```bash
nano ~/.config/labwc/autostart
```

Dodaj:
```bash
wtype -M alt -M logo h -m alt -m logo &
```

### 4. Uprawnienia sudo dla ydotoold (opcjonalne)

```bash
sudo nano /etc/sudoers.d/ydotool
```

```
ernie ALL=(ALL) NOPASSWD: /usr/local/bin/ydotoold, /usr/local/bin/ydotool
```

### 5. Restart GTFS o północy (cron)

```bash
crontab -e
```

Dodaj:
```
5 0 * * * systemctl restart pekaboard
```

## Zrzut ekranu przez SSH

```bash
# Na RPi:
WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 grim ~/screenshot.png

# Na Windows (PowerShell):
scp ernie@malinowa.local:~/screenshot.png $HOME\screenshot.png

# Na macOS:
scp ernie@malinowa.local:~/screenshot.png ~/Desktop/screenshot.png
```

## Logi

```bash
journalctl -u pekaboard -f
journalctl -u pekaboard --since today
journalctl -u pekaboard -n 100
```

## Licencje

- Ikony pogody: [amCharts Animated Weather Icons](https://www.amcharts.com/free-animated-svg-weather-icons/) (CC BY 4.0)
- Dane GTFS: ZTM Poznań
- Pogoda: Open-Meteo (CC BY 4.0)
