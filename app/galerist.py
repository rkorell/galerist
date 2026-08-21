# (c) Dr. Ralf Korell
# Galerist — Digitaler Bilderrahmen (Flask App Core)
# Modified: 2026-04-13, 19:50 - Erstellt
# Modified: 2026-04-13, 22:45 - Chromium-Start integriert, Ctrl+C Handler
# Modified: 2026-04-17, 13:00 - Chromium single-process (OOM-Fix), Watchdog, Restart-API
# Modified: 2026-07-23 - DisplayControl mit backend/output aus Config instanziiert, Compositor-Wait vor erstem Betriebsstunden-Check
# Modified: 2026-07-23 - Scheduler event-getrieben (kein 60-s-Polling); Chromium-Cache auf tmpfs + 1 MB (kein SD-Wear)
# Modified: 2026-08-01 - display_backend (Monitor/Fernseher) ueber /api/settings GET+POST verfuegbar
# Modified: 2026-08-02 - info_toggle-Action; Helligkeits-Overlay (set_brightness live via WS, display_brightness persistiert)
# Modified: 2026-08-02 - FB-Akku-Watchdog: taeglicher Batterie-Check der Fernbedienung, Warnung am Display bei <= Schwelle
# Modified: 2026-08-02 - Keep-shallow-Puls (Deep-Standby vermeiden) + Blackout-Overlay an Betriebsstunden-Uebergaengen
# Modified: 2026-08-02 - FB-Akku-Warnung als roter Header in der Infobox, Check beim Einschalten, gepinnt bis quittiert
# Modified: 2026-08-03 - Overlay-Dauer 0-20s (0=kein Auto-Close); FB-Akku-Warnung zusaetzlich per fleet-notify (Einweg-Push)
# Modified: 2026-08-21 - Suche: filterbare aktive Playlist (master/aktiv), WS-Actions search/search_show/search_reset, /api/artists, Overlay-Pin im Suchmodus

import json
import logging
import os
import random
import re
import signal
import subprocess
import threading
import time
import unicodedata

from flask import Flask, jsonify, request, send_from_directory
from flask_sock import Sock, ConnectionClosed

from config import Config
from metadata_cache import MetadataCache
from display_control import DisplayControl
from input_handler import InputHandler
from bt_watcher import BTHidWatcher

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('galerist')

# Werkzeug (HTTP-Requests) und andere Libs nur bei Warnungen
logging.getLogger('werkzeug').setLevel(logging.WARNING)


# DEBUG-Instrumentierung — in gemeinsame Datei mit input_handler.
# Ueber _DBG_ENABLED an-/abschaltbar (Default aus).
_DBG_ENABLED = False


def _dbg(msg):
    if not _DBG_ENABLED:
        return
    try:
        with open('/home/pi/galerist/inputdebug.log', 'a') as f:
            f.write(time.strftime('%H:%M:%S') + ' APP ' + msg + '\n')
    except Exception:
        pass


def _normalize(s: str) -> str:
    """Suchnormalisierung: Diakritika falten und kleinschreiben.

    So matcht 'monet' auf 'Monet' und 'seerose' als Substring auf 'Seerosen',
    unabhängig von Groß-/Kleinschreibung und Akzenten (é→e, ä→a)."""
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.lower()


class GaleristApp:
    """Zentrale Anwendung: Flask-Server, WebSocket, Bildrotation, Input-Handling."""

    def __init__(self):
        self.config = Config()

        # Log-Level aus Config
        log_level = getattr(logging, self.config.log_level, logging.INFO)
        logging.getLogger().setLevel(log_level)

        # Metadaten laden
        self.metadata_cache = MetadataCache(self.config)
        if not self.metadata_cache.load_from_cache():
            self.metadata_cache.refresh_from_files()

        # Display-Steuerung
        self.display_control = DisplayControl(
            backend=getattr(self.config, 'display_backend', 'wlr-randr'),
            output=getattr(self.config, 'display_output', 'HDMI-A-1'),
        )

        # Playlist: master = vollständige gemischte Liste (stabil), playlist = aktive
        # Liste (== master im Normalbetrieb, == Treffermenge im Suchmodus).
        self.master: list[str] = []
        self.playlist: list[str] = []
        self.current_index: int = 0
        # Suchmodus: Flag + zuletzt berechnete (noch nicht angewandte) Treffer + Suchindex
        self.search_active: bool = False
        self._pending: list[str] = []
        self._search_index: dict[str, dict] = {}
        self.paused: bool = False
        self.overlay_visible: bool = False
        # Bildhelligkeit (Dimm-Overlay): 100 = klar, 20 = stark gedimmt. Live via WS,
        # persistiert in config.display_brightness (nur beim Speichern auf Platte).
        self._brightness: int = getattr(self.config, 'display_brightness', 100)
        # Voll-Schwarz-Overlay während der TV-Off-Zeit (Keep-shallow-Pulse bleiben unsichtbar)
        self._blackout: bool = False
        # Aktive FB-Akku-Warnung (Prozent, oder None). Roter Header in der Infobox,
        # gepinnt bis manuell zugeklappt (= quittiert).
        self._battery_warning: int | None = None
        self._lock = threading.Lock()

        # WebSocket-Clients
        self._ws_clients: list = []
        self._ws_lock = threading.Lock()

        # Rotation-Timer
        self._timer: threading.Timer | None = None

        # Flask Setup
        self.app = Flask(__name__,
                         static_folder='static',
                         static_url_path='/static')
        self.sock = Sock(self.app)
        self._register_routes()

        # Playlist initialisieren
        self._init_playlist()

        # BT-Watcher: erzwingt HID-Profil-Connect bei FB-Wake (D-Bus-getrieben)
        self.bt_watcher = BTHidWatcher()

        # Input-Handler (Bluetooth-Fernbedienung, udev-getrieben)
        self.input_handler = InputHandler(
            callback=self._handle_action,
            config=self.config
        )

        # Chromium-Prozess
        self._chromium: subprocess.Popen | None = None

    def start(self):
        """Alle Hintergrund-Dienste starten und Flask-Server laufen lassen."""
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        self.bt_watcher.start()
        self.input_handler.start()
        self._start_rotation()
        self._start_schedule_checker()
        self._start_chromium()
        self._start_chromium_watchdog()
        self._start_tv_keepshallow()

        logger.info("Galerist gestartet: %d Bilder, Intervall %ds, Port %d",
                     len(self.playlist),
                     self.config.display_interval_seconds,
                     self.config.flask_port)

        try:
            self.app.run(
                host=self.config.flask_host,
                port=self.config.flask_port,
                debug=False,
                threaded=True
            )
        except KeyboardInterrupt:
            pass
        finally:
            self._cleanup()

    def _start_chromium(self):
        """Chromium im Kiosk-Modus starten (wartet kurz bis Flask bereit ist)."""
        def launch():
            time.sleep(2)  # Flask braucht einen Moment
            self._launch_chromium()

        threading.Thread(target=launch, daemon=True, name='ChromiumLauncher').start()

    def _launch_chromium(self):
        """Chromium-Prozess starten."""
        # Zombie-Prozess aufräumen falls vorhanden
        if self._chromium is not None:
            try:
                self._chromium.wait(timeout=0)
            except subprocess.TimeoutExpired:
                self._chromium.terminate()
                try:
                    self._chromium.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._chromium.kill()
            self._chromium = None

        cmd = [
            'chromium',
            '--kiosk',
            '--ozone-platform=wayland',
            '--noerrdialogs',
            '--disable-infobars',
            '--disable-session-crashed-bubble',
            '--hide-crash-restore-bubble',
            '--disable-features=TranslateUI,Translate,GCMRegistration',
            '--disable-component-update',
            '--no-first-run',
            '--disable-extensions',
            '--disable-background-networking',
            '--disable-sync',
            '--password-store=basic',
            '--js-flags=--max-old-space-size=128',
            # Cache auf tmpfs (RAM, nie SD → kein Flash-Wear) und auf 1 MB gekappt.
            # Lokaler Server, 1.204 rotierende Bilder → Browser-Cache bringt praktisch nichts.
            '--disk-cache-dir=/tmp/galerist-cache',
            '--disk-cache-size=1048576',
            # Single-Process: statt 9 Prozesse nur 1 → drastisch weniger RAM
            '--single-process',
            '--in-process-gpu',
            '--disable-gpu-compositing',
            'http://localhost:{}/'.format(self.config.flask_port),
        ]
        try:
            self._chromium = subprocess.Popen(cmd)
            logger.info("Chromium gestartet (PID %d)", self._chromium.pid)
        except FileNotFoundError:
            logger.warning("Chromium nicht gefunden — kein Kiosk-Modus")

    def _start_chromium_watchdog(self):
        """Überwacht Chromium und startet bei Crash automatisch neu."""
        def watchdog():
            while True:
                time.sleep(10)
                if self._chromium is not None and self._chromium.poll() is not None:
                    exit_code = self._chromium.returncode
                    logger.warning("Chromium abgestürzt (Exit-Code %s) — Neustart ...",
                                   exit_code)
                    time.sleep(2)
                    self._launch_chromium()

        t = threading.Thread(target=watchdog, daemon=True, name='ChromiumWatchdog')
        t.start()

    # ── Fernbedienungs-Akku ───────────────────────────────────

    def _read_fb_battery(self) -> int | None:
        """Akkustand der Bluetooth-Fernbedienung in Prozent (oder None).

        Liest den BlueZ Battery Service via bluetoothctl. None, wenn keine MAC
        konfiguriert, die FB nicht verbunden ist oder kein Wert vorliegt.
        """
        mac = getattr(self.config, 'fb_battery_mac', None)
        if not mac:
            return None
        try:
            out = subprocess.run(
                ['bluetoothctl', 'info', mac],
                capture_output=True, text=True, timeout=8
            ).stdout
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("FB-Akku: bluetoothctl fehlgeschlagen: %s", e)
            return None
        connected = False
        percent = None
        for line in out.splitlines():
            s = line.strip()
            if s.startswith('Connected:'):
                connected = s.endswith('yes')
            elif 'Battery Percentage' in s:
                m = re.search(r'\((\d+)\)', s)  # "Battery Percentage: 0x14 (20)"
                if m:
                    percent = int(m.group(1))
        if not connected:
            return None
        return percent

    def _check_fb_battery(self):
        """FB-Akku prüfen (beim morgendlichen Einschalten). Warnt nur, wenn ein Wert
        vorliegt und er <= Schwelle ist. Ist die FB nicht verbunden, gibt es keinen
        Wert — das ist bewusst KEINE Meldung wert."""
        try:
            pct = self._read_fb_battery()
        except Exception as e:
            logger.error("FB-Akku-Check Fehler: %s", e)
            return
        if pct is None:
            return  # FB nicht verbunden / kein Wert — still bleiben
        logger.info("FB-Akku: %d%%", pct)
        if pct <= getattr(self.config, 'fb_battery_warn_percent', 20):
            logger.warning("FB-Akku schwach (%d%%) — Warnung in der Infobox", pct)
            self._battery_warning = pct
            self.overlay_visible = True
            self._broadcast({'type': 'battery_warning', 'percent': pct})
            self._notify_fb_battery(pct)

    def _notify_fb_battery(self, pct: int):
        """Einweg-Push per fleet-notify (Handy), zusätzlich zur Overlay-Warnung.
        Fehlertolerant — scheitert der Push, bleibt die Overlay-Warnung unberührt."""
        try:
            subprocess.run(
                ['fleet-notify', 'INFO', f'FB-Akku schwach ({pct}%) — bitte laden'],
                capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("fleet-notify fehlgeschlagen: %s", e)

    def _ack_battery_warning(self):
        """FB-Akku-Warnung quittieren (beim manuellen Zuklappen der Infobox)."""
        if self._battery_warning is not None:
            logger.info("FB-Akku-Warnung quittiert (war %d%%)", self._battery_warning)
            self._battery_warning = None

    # ── TV Keep-shallow (Deep-Standby vermeiden) ──────────────

    def _start_tv_keepshallow(self):
        """Weckt den Fernseher nachts periodisch kurz per CEC und legt ihn wieder
        schlafen, damit der Standby-Timer die Deep-Standby-Schwelle nie erreicht
        (sonst ist er morgens nicht mehr weckbar). Nur bei display_backend='cec'.

        Kein Overlay-Handling hier — das Schwarz-Overlay liegt in der Off-Zeit ohnehin
        an (an den Betriebsstunden-Übergängen gesetzt), sodass die Pulse unsichtbar bleiben.
        """
        if getattr(self.config, 'display_backend', '') != 'cec':
            return
        interval = getattr(self.config, 'tv_keepshallow_minutes', 120) * 60
        hold = getattr(self.config, 'tv_keepshallow_seconds', 30)
        if interval <= 0:
            return

        def loop():
            time.sleep(interval)
            while True:
                try:
                    # Nur wenn der TV gerade AUS sein soll (Betriebsstunden 'off').
                    # Overlay liegt in der Off-Zeit ohnehin schwarz an (Betriebsstunden-Übergang).
                    if self.display_control.display_on is False:
                        logger.info("Keep-shallow: TV kurz wecken (Deep-Standby vermeiden)")
                        self.display_control.cec_wake()
                        time.sleep(hold)
                        self.display_control.cec_standby()
                except Exception as e:
                    logger.error("Keep-shallow Fehler: %s", e)
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True, name='TVKeepShallow').start()

    def _shutdown(self, signum, frame):
        """Signal-Handler für sauberes Beenden."""
        logger.info("Beende Galerist (Signal %d) ...", signum)
        self._cleanup()
        os._exit(0)

    def _cleanup(self):
        """Chromium und Input-Handler sauber beenden."""
        self.input_handler.stop()
        self.bt_watcher.stop()
        if self._chromium and self._chromium.poll() is None:
            logger.info("Beende Chromium (PID %d) ...", self._chromium.pid)
            self._chromium.terminate()
            try:
                self._chromium.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._chromium.kill()
            logger.info("Chromium beendet")
        if self._timer:
            self._timer.cancel()

    # ── Playlist ──────────────────────────────────────────────

    def _init_playlist(self):
        """Bildliste laden und zufällig mischen."""
        images = self.metadata_cache.get_image_list()
        random.shuffle(images)
        self.master = images
        self.playlist = self.master
        self.current_index = 0
        self.search_active = False
        self._pending = []
        self._build_search_index()
        logger.info("Playlist: %d Bilder", len(self.playlist))

    def current_image_data(self) -> dict:
        """Aktuelles Bild mit Metadaten und Filmstreifen-Nachbarn."""
        if not self.playlist:
            return {'src': '', 'metadata': {}, 'index': 0, 'total': 0, 'strip': []}
        with self._lock:
            idx = self.current_index
            filename = self.playlist[idx]
        meta = self.metadata_cache.get_metadata(filename) or {}
        # Filmstreifen: 2 vor + aktuell + 2 nach
        strip = []
        n = len(self.playlist)
        for offset in range(-2, 3):
            si = (idx + offset) % n
            sf = self.playlist[si]
            strip.append({
                'src': f'/images/{sf}',
                'thumb': f'/thumbs/{sf}',
                'active': offset == 0,
            })
        return {
            'src': f'/images/{filename}',
            'metadata': meta,
            'index': idx + 1,
            'total': n,
            'strip': strip,
        }

    def advance(self, direction: int = 1):
        """Zum nächsten/vorherigen Bild wechseln."""
        if not self.playlist:
            return
        with self._lock:
            self.current_index = (self.current_index + direction) % len(self.playlist)
        self._broadcast({'type': 'show_image', **self.current_image_data()})

    # ── Suche ─────────────────────────────────────────────────

    def _build_search_index(self):
        """Normalisierten Suchindex über alle Bilder aufbauen (einmalig je Playlist).

        Pro Datei: ein Künstler-Feld und ein Textfeld (Titel + Originaltitel + Tags),
        beide diakritika-gefaltet und kleingeschrieben — so ist jede Suche nur noch
        ein Substring-Check ohne Pro-Tastendruck-Normalisierung des Bestands."""
        idx = {}
        for fn in self.master:
            meta = self.metadata_cache.get_metadata(fn) or {}
            tags = ' '.join(meta.get('such_tags') or [])
            text = ' '.join([
                meta.get('titel', '') or '',
                meta.get('titel_original', '') or '',
                tags,
            ])
            idx[fn] = {
                'artist': _normalize(meta.get('kuenstler', '') or ''),
                'text': _normalize(text),
            }
        self._search_index = idx

    def _search_matches(self, kuenstler: str, wort: str) -> list:
        """Treffer-Dateinamen für die Suchbegriffe (UND-Verknüpfung), in master-Reihenfolge.

        Leere Begriffe werden ignoriert; sind beide leer, gibt es keine Treffer."""
        kn = _normalize(kuenstler)
        wn = _normalize(wort)
        if not kn and not wn:
            return []
        result = []
        for fn in self.master:
            e = self._search_index.get(fn)
            if not e:
                continue
            if kn and kn not in e['artist']:
                continue
            if wn and wn not in e['text']:
                continue
            result.append(fn)
        return result

    def _do_search_count(self, ws, kuenstler: str, wort: str):
        """Treffer berechnen, als _pending merken und die Anzahl an den Anfrager melden.
        Ändert den angezeigten Bestand NICHT — erst 'search_show' schaltet um."""
        matches = self._search_matches(kuenstler, wort)
        with self._lock:
            self._pending = matches
        try:
            ws.send(json.dumps({'type': 'search_result', 'count': len(matches)},
                                ensure_ascii=False))
        except Exception:
            pass

    def _do_search_show(self):
        """Zuletzt berechnete Treffermenge als aktive Playlist anwenden.
        Öffnet die Infobox gepinnt (kein Auto-Close, solange search_active)."""
        with self._lock:
            pending = list(self._pending)
        if not pending:
            return
        with self._lock:
            self.playlist = pending
            self.current_index = 0
            self.search_active = True
        self.overlay_visible = True
        self._broadcast({'type': 'show_image', **self.current_image_data()})
        self._broadcast({'type': 'show_overlay'})
        self._broadcast({'type': 'search_state', 'active': True, 'count': len(pending)})
        self._reset_timer()

    def _do_search_reset(self):
        """Suchmodus verlassen: vollen Bestand ab dem aktuell gezeigten Bild fortsetzen."""
        with self._lock:
            current_file = self.playlist[self.current_index] if self.playlist else None
            self.playlist = self.master
            if current_file and current_file in self.master:
                self.current_index = self.master.index(current_file)
            else:
                self.current_index = 0
            self.search_active = False
            self._pending = []
        self._broadcast({'type': 'show_image', **self.current_image_data()})
        self._broadcast({'type': 'search_state', 'active': False, 'count': 0})
        self._reset_timer()
        # Normale Auto-Close-Semantik zurückgeben: sichtbare Infobox regulär ausblenden
        if self.overlay_visible and self.config.overlay_duration_seconds > 0:
            threading.Timer(self.config.overlay_duration_seconds,
                            self._auto_hide_overlay).start()

    # ── Rotation-Timer ────────────────────────────────────────

    def _start_rotation(self):
        """Automatischen Bildwechsel starten."""
        self._schedule_next()

    def _schedule_next(self):
        """Timer für nächsten Bildwechsel setzen (ersetzt laufenden Timer)."""
        if self._timer:
            self._timer.cancel()
        interval = self.config.display_interval_seconds
        _dbg('schedule_next interval=' + str(interval))  # DEBUG
        self._timer = threading.Timer(interval, self._rotation_tick)
        self._timer.daemon = True
        self._timer.start()

    def _rotation_tick(self):
        """Timer-Callback: Bild wechseln wenn nicht pausiert."""
        _dbg('tick paused=' + str(self.paused) + ' display_on=' + str(self.display_control.display_on))  # DEBUG
        if not self.paused and self.display_control.display_on:
            self.advance(1)
            # Nächstes Bild vorladen (bei ausreichend langem Intervall)
            self._preload_next()
        self._schedule_next()

    def _preload_next(self):
        """Dem Browser das nächste Bild zum Vorladen ankündigen."""
        if not self.playlist:
            return
        interval = self.config.display_interval_seconds
        if interval < 30:
            return  # Zu kurzes Intervall, Preload lohnt nicht
        with self._lock:
            next_index = (self.current_index + 1) % len(self.playlist)
            next_file = self.playlist[next_index]
        # Preload 10s vor dem nächsten Wechsel senden
        delay = max(interval - 10, interval * 0.8)
        threading.Timer(delay, lambda: self._broadcast({
            'type': 'preload',
            'src': f'/images/{next_file}'
        })).start()

    def _reset_timer(self):
        """Timer nach manuellem Blättern zurücksetzen."""
        self._schedule_next()

    # ── Betriebsstunden ───────────────────────────────────────

    def _set_blackout(self, on: bool):
        """Voll-Schwarz-Overlay am Kiosk setzen (nur cec-Backend), nur bei Zustandswechsel.
        An den Betriebsstunden-Übergängen aufgerufen: an beim Off-Übergang, aus beim
        On-Übergang — so bleibt die Off-Zeit inkl. Keep-shallow-Pulse schwarz."""
        if getattr(self.config, 'display_backend', '') != 'cec':
            return
        if on == self._blackout:
            return
        self._blackout = on
        self._broadcast({'type': 'blackout', 'on': on})

    def _start_schedule_checker(self):
        """Betriebsstunden-Steuerung als event-getriebener Daemon-Thread.

        Schläft jeweils exakt bis zum nächsten Umschaltpunkt statt zu pollen.
        Die Zeiten sind ein statischer Faktor und werden einmal beim Start
        gelesen — eine Änderung wird per Service-Neustart übernommen.
        """
        def scheduler():
            self.display_control.wait_ready()
            hours = self.config.operating_hours
            on_time, off_time = hours['on_time'], hours['off_time']
            prev_on = None
            while True:
                try:
                    should_be_on = self.display_control.check_operating_hours(on_time, off_time)
                    self._set_blackout(not should_be_on)
                    if should_be_on and prev_on is not True:
                        self._check_fb_battery()  # beim (morgendlichen) Einschalten
                    prev_on = should_be_on
                    wait_s = self.display_control.seconds_until_next_boundary(on_time, off_time)
                except Exception as e:
                    logger.error("Betriebsstunden-Scheduler Fehler: %s", e)
                    wait_s = 3600
                logger.info("Nächster Display-Umschaltpunkt in %d s", int(wait_s))
                time.sleep(wait_s + 1)

        t = threading.Thread(target=scheduler, daemon=True, name='ScheduleChecker')
        t.start()

    # ── Action-Handler ────────────────────────────────────────

    def _handle_action(self, action: str):
        """Zentrale Aktion verarbeiten (von FB oder Web-App)."""
        logger.debug("Aktion: %s", action)
        _dbg('handle_action ' + str(action))  # DEBUG

        if action == 'next':
            self.advance(1)
            self._reset_timer()
        elif action == 'prev':
            self.advance(-1)
            self._reset_timer()
        elif action == 'info_on':
            self.overlay_visible = True
            self._broadcast({'type': 'show_overlay'})
            # Overlay nach konfigurierter Dauer automatisch ausblenden (0 = aus, bleibt offen)
            if self.config.overlay_duration_seconds > 0:
                threading.Timer(
                    self.config.overlay_duration_seconds,
                    self._auto_hide_overlay
                ).start()
        elif action == 'info_off':
            self.overlay_visible = False
            self._ack_battery_warning()
            self._broadcast({'type': 'hide_overlay'})
        elif action == 'info_toggle':
            if self.overlay_visible:
                self.overlay_visible = False
                self._ack_battery_warning()
                self._broadcast({'type': 'hide_overlay'})
            else:
                self.overlay_visible = True
                self._broadcast({'type': 'show_overlay'})
                if self.config.overlay_duration_seconds > 0:
                    threading.Timer(
                        self.config.overlay_duration_seconds,
                        self._auto_hide_overlay
                    ).start()
        elif action == 'playpause':
            self.paused = not self.paused
            logger.info("Diashow %s", "pausiert" if self.paused else "fortgesetzt")
        elif action == 'refresh_metadata':
            self.metadata_cache.refresh_from_files()
            self._init_playlist()
            self._broadcast({'type': 'show_image', **self.current_image_data()})
            self._reset_timer()

    def _auto_hide_overlay(self):
        """Overlay automatisch ausblenden nach Timer-Ablauf.

        Bei aktiver Akku-Warnung NICHT ausblenden — die bleibt gepinnt, bis manuell
        zugeklappt (= quittiert)."""
        if self.overlay_visible and self._battery_warning is None and not self.search_active:
            self.overlay_visible = False
            self._broadcast({'type': 'hide_overlay'})

    def set_brightness(self, value):
        """Bildhelligkeit live setzen (20–100) und an Kiosk-Clients broadcasten.

        Kein Disk-Write — Persistenz erfolgt erst über /api/settings (Speichern).
        """
        try:
            v = int(value)
        except (TypeError, ValueError):
            return
        v = max(20, min(100, v))
        self._brightness = v
        self._broadcast({'type': 'set_brightness', 'value': v})

    # ── WebSocket ─────────────────────────────────────────────

    def _broadcast(self, message: dict):
        """Nachricht an alle verbundenen WebSocket-Clients senden."""
        data = json.dumps(message, ensure_ascii=False)
        dead = []
        with self._ws_lock:
            for ws in self._ws_clients:
                try:
                    ws.send(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._ws_clients.remove(ws)

    def _register_routes(self):
        """Flask-Routes und WebSocket-Endpoints registrieren."""

        # ── Seiten ────────────────────────────────────────

        @self.app.route('/')
        def kiosk():
            """Kiosk-Anzeige: Vollbild-Bilddarstellung."""
            return send_from_directory('static', 'index.html')

        @self.app.route('/galerist')
        def control():
            """Web-App: Steuerung und Einstellungen."""
            return send_from_directory('static', 'control.html')

        # ── Bild-Auslieferung ─────────────────────────────

        @self.app.route('/images/<path:filename>')
        def serve_image(filename):
            """Bild aus lokalem Verzeichnis ausliefern."""
            return send_from_directory(self.config.image_directory, filename)

        @self.app.route('/thumbs/<path:filename>')
        def serve_thumb(filename):
            """Thumbnail ausliefern (dateiname.jpg → dateiname.jpg.thumb)."""
            thumb_name = filename + '.thumb'
            return send_from_directory(self.config.image_directory, thumb_name)

        # ── API ───────────────────────────────────────────

        @self.app.route('/api/settings', methods=['GET'])
        def get_settings():
            """Aktuelle Einstellungen abrufen."""
            return jsonify({
                'display_interval_seconds': self.config.display_interval_seconds,
                'overlay_duration_seconds': self.config.overlay_duration_seconds,
                'operating_hours': self.config.operating_hours,
                'display_backend': getattr(self.config, 'display_backend', 'wlr-randr'),
                'display_brightness': self._brightness,
            })

        @self.app.route('/api/settings', methods=['POST'])
        def update_settings():
            """Einstellungen ändern und speichern."""
            data = request.json
            updates = {}

            if 'display_interval_seconds' in data:
                val = int(data['display_interval_seconds'])
                if 10 <= val <= 36000:
                    updates['display_interval_seconds'] = val

            if 'overlay_duration_seconds' in data:
                val = int(data['overlay_duration_seconds'])
                if 0 <= val <= 20:
                    updates['overlay_duration_seconds'] = val

            if 'operating_hours' in data:
                oh = data['operating_hours']
                if 'on_time' in oh and 'off_time' in oh:
                    updates['operating_hours'] = {
                        'on_time': oh['on_time'],
                        'off_time': oh['off_time']
                    }

            if 'display_backend' in data:
                val = data['display_backend']
                if val in ('wlr-randr', 'cec', 'xrandr'):
                    updates['display_backend'] = val

            if 'display_brightness' in data:
                val = int(data['display_brightness'])
                if 20 <= val <= 100:
                    updates['display_brightness'] = val
                    self._brightness = val

            if updates:
                self.config.update_many(updates)
                self._reset_timer()
                logger.info("Einstellungen aktualisiert: %s", updates)

            return jsonify({'status': 'ok', 'settings': {
                'display_interval_seconds': self.config.display_interval_seconds,
                'overlay_duration_seconds': self.config.overlay_duration_seconds,
                'operating_hours': self.config.operating_hours,
                'display_backend': getattr(self.config, 'display_backend', 'wlr-randr'),
                'display_brightness': self._brightness,
            }})

        @self.app.route('/api/status')
        def status():
            """Debug-Status der Anwendung."""
            return jsonify({
                'current_index': self.current_index,
                'playlist_length': len(self.playlist),
                'paused': self.paused,
                'overlay_visible': self.overlay_visible,
                'display_on': self.display_control.display_on,
                'ws_clients': len(self._ws_clients),
                'current_image': self.current_image_data(),
            })

        @self.app.route('/api/artists')
        def artists():
            """Distinct-Künstlerliste für die Autocomplete-Datalist der Suche."""
            seen = set()
            for fn in self.master:
                meta = self.metadata_cache.get_metadata(fn) or {}
                a = (meta.get('kuenstler') or '').strip()
                if a and not a.startswith('http') and not re.match(r'^Q\d+$', a):
                    seen.add(a)
            return jsonify(sorted(seen))

        @self.app.route('/api/restart', methods=['POST'])
        def restart_service():
            """Galerist-Service per systemd neu starten."""
            logger.info("Service-Neustart angefordert via Web-App")
            # Antwort zuerst senden, dann neustarten
            def do_restart():
                time.sleep(1)
                subprocess.Popen(['sudo', 'systemctl', 'restart', 'galerist.service'])
            threading.Thread(target=do_restart, daemon=True).start()
            return jsonify({'status': 'restarting'})

        # ── WebSocket ─────────────────────────────────────

        @self.sock.route('/ws')
        def ws_handler(ws):
            """WebSocket-Verbindung: sendet Bild-Updates, empfängt Aktionen."""
            with self._ws_lock:
                self._ws_clients.append(ws)
            logger.debug("WebSocket-Client verbunden (gesamt: %d)", len(self._ws_clients))

            # Aktuellen Zustand sofort senden
            try:
                ws.send(json.dumps(
                    {'type': 'show_image', **self.current_image_data()},
                    ensure_ascii=False
                ))
                ws.send(json.dumps({'type': 'set_brightness', 'value': self._brightness}))
                if self._blackout:
                    ws.send(json.dumps({'type': 'blackout', 'on': True}))
                if self.overlay_visible:
                    ws.send(json.dumps({'type': 'show_overlay'}))
                if self._battery_warning is not None:
                    ws.send(json.dumps({'type': 'battery_warning', 'percent': self._battery_warning}))
                if self.search_active:
                    ws.send(json.dumps({'type': 'search_state', 'active': True, 'count': len(self.playlist)}))
            except Exception:
                pass

            try:
                while True:
                    data = ws.receive(timeout=None)
                    if data is None:
                        break
                    msg = json.loads(data)
                    action = msg.get('action', '')
                    if action == 'set_brightness':
                        self.set_brightness(msg.get('value'))
                    elif action == 'search':
                        self._do_search_count(ws, msg.get('kuenstler', ''), msg.get('wort', ''))
                    elif action == 'search_show':
                        self._do_search_show()
                    elif action == 'search_reset':
                        self._do_search_reset()
                    elif action:
                        self._handle_action(action)
            except (ConnectionClosed, ConnectionError):
                pass
            finally:
                with self._ws_lock:
                    if ws in self._ws_clients:
                        self._ws_clients.remove(ws)
                logger.debug("WebSocket-Client getrennt (gesamt: %d)", len(self._ws_clients))


# ── Hauptprogramm ─────────────────────────────────────────────

if __name__ == '__main__':
    app = GaleristApp()
    app.start()
