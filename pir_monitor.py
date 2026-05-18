#!/usr/bin/env python3
import gpiod, time, subprocess, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

GPIO_PIN       = 17
NO_MOTION_SECS = 300  # 5 minut

def display(on: bool):
    cmd = ["wlopm", "--on" if on else "--off", "HDMI-A-1"]
    subprocess.run(cmd, env={
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }, capture_output=True)
    log.info("Monitor: %s", "ON" if on else "OFF")

def in_active_hours() -> bool:
    now = datetime.now()
    h, m = now.hour, now.minute
    after_start = h > 6 or (h == 6 and m >= 30)
    before_end  = h < 23
    return after_start and before_end

def main():
    monitor_on  = True
    last_motion = time.time()

    with gpiod.request_lines(
        '/dev/gpiochip4',
        consumer='pir',
        config={GPIO_PIN: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)}
    ) as request:
        log.info("PIR uruchomiony na GPIO%d", GPIO_PIN)
        while True:
            motion = request.get_value(GPIO_PIN).value

            if in_active_hours():
                if motion:
                    last_motion = time.time()
                    if not monitor_on:
                        display(True)
                        monitor_on = True
                elif time.time() - last_motion > NO_MOTION_SECS:
                    if monitor_on:
                        display(False)
                        monitor_on = False
            
            time.sleep(2)

if __name__ == "__main__":
    main()