#!/bin/bash
# (c) Dr. Ralf Korell
# Galerist — Chromium Kiosk Starter (fuer labwc autostart)
# Modified: 2026-04-13, 20:05 - Erstellt

# Warten bis Flask-Server bereit ist
for i in $(seq 1 30); do
    if curl -s -o /dev/null http://localhost:5000/; then
        break
    fi
    sleep 1
done

exec chromium \
    --kiosk \
    --ozone-platform=wayland \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --no-first-run \
    --disable-extensions \
    --disable-background-networking \
    --disable-sync \
    --js-flags="--max-old-space-size=128" \
    --disk-cache-size=52428800 \
    http://localhost:5000/
