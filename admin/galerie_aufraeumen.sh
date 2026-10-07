#!/usr/bin/env bash
# (c) Dr. Ralf Korell
# Galerist — Naechtliches Aufraeumen: markierte Bilder physisch entfernen
# Modified: 2026-10-06 - Erstellt
# Modified: 2026-10-07 - Auf einen Status zurueckgebaut: Queue ist to_be_deleted, danach verworfen=true/to_be_deleted=false; Logfile und Buchhaltung entfallen
#
# Laeuft auf dem WebServer (Code- und Archiv-Master) als User pi, taeglich eine
# Stunde nach der Anzeige-Ausschaltzeit der Rahmen. Die Rahmen laufen nachts
# durch (nur die Anzeige ist aus), sind also per SSH erreichbar.
#
#   to_be_deleted = true   Loesch-Queue, gesetzt vom Knopf in der Steuer-App
#   verworfen     = true   ungeeignet; haelt das Bild ueber v_meural_bilder
#                          dauerhaft aus kuenftigen Crawls und Downloads heraus
#
# Nachweis ist die DB selbst: steht to_be_deleted noch, war der Lauf unvollstaendig.
#
# Standortabhaengige Werte (DB-Zugang, Rahmen-Aliase, Pfade) stehen in
# aufraeumen.env daneben — nicht in Git, das Galerist-Repo ist public.

set -uo pipefail

ENV_DATEI="$(dirname "$(readlink -f "$0")")/aufraeumen.env"
[ -r "$ENV_DATEI" ] || { echo "$ENV_DATEI nicht lesbar" >&2; exit 1; }
# shellcheck source=/dev/null
. "$ENV_DATEI"

: "${PGHOST:?}" "${PGUSER:?}" "${PGDATABASE:?}" "${PGPASSWORD:?}"
: "${GAL_ARCHIV:?}" "${GAL_FRAMES:?}" "${GAL_FRAME_BILDER:?}" "${GAL_FRAME_CACHE:?}"
export PGHOST PGUSER PGDATABASE PGPASSWORD

# ── Lösch-Queue ───────────────────────────────────────────────────────────
QUEUE=$(psql -q -At -c "SELECT dateiname FROM bilder
                         WHERE to_be_deleted AND dateiname IS NOT NULL;") || exit 1
[ -n "$QUEUE" ] || exit 0

# Dateinamen pruefen, bevor sie in rm/ssh landen (Pfadtrenner, Sonderzeichen).
DATEIEN=()
while IFS= read -r f; do
    [[ "$f" =~ ^[A-Za-z0-9._-]+\.jpg$ ]] && DATEIEN+=("$f")
done <<< "$QUEUE"
[ "${#DATEIEN[@]}" -gt 0 ] || exit 0

# ── Archiv auf dem WebServer ──────────────────────────────────────────────
for f in "${DATEIEN[@]}"; do
    rm -f -- "$GAL_ARCHIV/$f" "$GAL_ARCHIV/$f.thumb"
done

# ── Rahmen ────────────────────────────────────────────────────────────────
# Ein SSH-Aufruf je Rahmen loescht die Dateien und meldet, wie viele wegfielen.
# Nur dann folgen Cache-Loeschung und Dienst-Neustart — der Neustart baut
# metadata_cache.json aus den Dateien neu auf.
for rahmen in $GAL_FRAMES; do
    weg=$(printf '%s\n' "${DATEIEN[@]}" | ssh -o BatchMode=yes -o ConnectTimeout=15 "$rahmen" \
        "cd '$GAL_FRAME_BILDER' || exit 1
         n=0
         while IFS= read -r f; do
             for p in \"\$f\" \"\$f.thumb\"; do
                 [ -e \"\$p\" ] && rm -f -- \"\$p\" && n=\$((n+1))
             done
         done
         echo \"\$n\"") || continue

    [ "${weg:-0}" -gt 0 ] || continue

    ssh -o BatchMode=yes -o ConnectTimeout=15 "$rahmen" \
        "rm -f -- '$GAL_FRAME_CACHE' && sudo systemctl restart galerist.service"
done

# ── Status fortschreiben ──────────────────────────────────────────────────
psql -q -c "UPDATE bilder SET verworfen = true, to_be_deleted = false
             WHERE to_be_deleted;"
