#!/usr/bin/env bash
# (c) Dr. Ralf Korell
# Galerist — Deploy-Script: Code (und optional Bilder) auf die Rahmen-Pis
# Modified: 2026-08-24 - Erstellt (Code+optional Bilder auf galerist/theframe, Cache-Rebuild, Restart)
#
# Laeuft auf dem WebServer (Code-Master). Nutzt die passwortlosen SSH-Aliase
# der Rahmen. Faellt NIE config.json an (device-local, pro Rahmen verschieden).
#
# Aufruf:
#   ./deploy.sh                  Code auf beide Rahmen, Restart
#   ./deploy.sh --images         Code + Bilder auf beide (Cache-Rebuild), Restart
#   ./deploy.sh theframe         nur Code, nur TheFrame
#   ./deploy.sh --images galerist
set -uo pipefail

cd "$(dirname "$(readlink -f "$0")")"

APP=app
DEST=/home/pi/galerist
ALL_FRAMES=(galerist theframe)   # SSH-Aliase; weitere Rahmen hier ergaenzen

usage() {
    echo "Usage: $0 [--images|-i] [${ALL_FRAMES[*]}]"
    echo "  ohne Ziel: beide Rahmen.  --images: zusaetzlich Bilder (mit Cache-Rebuild)."
    exit 1
}

WITH_IMAGES=0
FRAMES=()
for arg in "$@"; do
    case "$arg" in
        --images|-i) WITH_IMAGES=1 ;;
        -h|--help)   usage ;;
        *)
            found=0
            for f in "${ALL_FRAMES[@]}"; do [ "$arg" = "$f" ] && found=1; done
            [ "$found" = 1 ] && FRAMES+=("$arg") || { echo "Unbekanntes Argument: $arg"; usage; }
            ;;
    esac
done
[ ${#FRAMES[@]} -eq 0 ] && FRAMES=("${ALL_FRAMES[@]}")

fail=0
for F in "${FRAMES[@]}"; do
    echo "=== $F ==="
    # Code (config.json wird NICHT angefasst — es liegt nicht unter *.py/static)
    if rsync -a --exclude='__pycache__' "$APP"/*.py "$F:$DEST/" \
       && rsync -a --exclude='__pycache__' "$APP"/static "$F:$DEST/"; then
        echo "  Code deployt"
    else
        echo "  FEHLER beim Code-Deploy auf $F — uebersprungen"; fail=1; continue
    fi

    if [ "$WITH_IMAGES" = 1 ]; then
        if rsync -a --exclude='*.jpg_original' --exclude='*.md' galerie/ "$F:$DEST/images/" \
           && ssh "$F" "rm -f $DEST/metadata_cache.json"; then
            echo "  Bilder deployt + Cache geloescht (Rebuild beim Start)"
        else
            echo "  FEHLER beim Bild-Deploy auf $F"; fail=1
        fi
    fi

    if ssh "$F" 'sudo systemctl restart galerist.service'; then
        echo "  Service neu gestartet"
    else
        echo "  FEHLER beim Restart auf $F"; fail=1
    fi
done

[ "$fail" = 0 ] && echo "Fertig — alle Ziele ok." || echo "Mit Fehlern beendet."
exit "$fail"
