# (c) Dr. Ralf Korell
# Galerist — Digitaler Bilderrahmen (Flask App Core)
# Modified: 2026-04-13, 19:50 - Erstellt
# Modified: 2026-04-13, 22:45 - Chromium-Start integriert, Ctrl+C Handler
# Modified: 2026-04-17, 13:00 - Chromium single-process (OOM-Fix), Watchdog, Restart-API

import json
import logging
import os
import random
import signal
import subprocess
import threading
import time

from flask import Flask, jsonify, request, send_from_directory
from flask_sock import Sock, ConnectionClosed

from config import Config
from metadata_cache import MetadataCache
from display_control import DisplayControl
from input_handler import InputHandler

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('galerist')

# Werkzeug (HTTP-Requests) und andere Libs nur bei Warnungen
logging.getLogger('werkzeug').setLevel(logging.WARNING)


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
        self.display_control = DisplayControl()

        # Playlist
        self.playlist: list[str] = []
        self.current_index: int = 0
        self.paused: bool = False
        self.overlay_visible: bool = False
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

        # Input-Handler (Bluetooth-Fernbedienung)
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

        self.input_handler.start()
        self._start_rotation()
        self._start_schedule_checker()
        self._start_chromium()
        self._start_chromium_watchdog()

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
            '--disk-cache-size=52428800',
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

    def _shutdown(self, signum, frame):
        """Signal-Handler für sauberes Beenden."""
        logger.info("Beende Galerist (Signal %d) ...", signum)
        self._cleanup()
        os._exit(0)

    def _cleanup(self):
        """Chromium und Input-Handler sauber beenden."""
        self.input_handler.stop()
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
        self.playlist = images
        self.current_index = 0
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

    # ── Rotation-Timer ────────────────────────────────────────

    def _start_rotation(self):
        """Automatischen Bildwechsel starten."""
        self._schedule_next()

    def _schedule_next(self):
        """Timer für nächsten Bildwechsel setzen (ersetzt laufenden Timer)."""
        if self._timer:
            self._timer.cancel()
        interval = self.config.display_interval_seconds
        self._timer = threading.Timer(interval, self._rotation_tick)
        self._timer.daemon = True
        self._timer.start()

    def _rotation_tick(self):
        """Timer-Callback: Bild wechseln wenn nicht pausiert."""
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

    def _start_schedule_checker(self):
        """Betriebsstunden-Prüfung als Daemon-Thread."""
        def check_loop():
            while True:
                try:
                    hours = self.config.operating_hours
                    self.display_control.check_operating_hours(
                        hours['on_time'], hours['off_time']
                    )
                except Exception as e:
                    logger.error("Betriebsstunden-Check Fehler: %s", e)
                time.sleep(60)

        t = threading.Thread(target=check_loop, daemon=True, name='ScheduleChecker')
        t.start()

    # ── Action-Handler ────────────────────────────────────────

    def _handle_action(self, action: str):
        """Zentrale Aktion verarbeiten (von FB oder Web-App)."""
        logger.debug("Aktion: %s", action)

        if action == 'next':
            self.advance(1)
            self._reset_timer()
        elif action == 'prev':
            self.advance(-1)
            self._reset_timer()
        elif action == 'info_on':
            self.overlay_visible = True
            self._broadcast({'type': 'show_overlay'})
            # Overlay nach konfigurierter Dauer automatisch ausblenden
            threading.Timer(
                self.config.overlay_duration_seconds,
                self._auto_hide_overlay
            ).start()
        elif action == 'info_off':
            self.overlay_visible = False
            self._broadcast({'type': 'hide_overlay'})
        elif action == 'playpause':
            self.paused = not self.paused
            logger.info("Diashow %s", "pausiert" if self.paused else "fortgesetzt")
        elif action == 'refresh_metadata':
            self.metadata_cache.refresh_from_files()
            self._init_playlist()
            self._broadcast({'type': 'show_image', **self.current_image_data()})
            self._reset_timer()

    def _auto_hide_overlay(self):
        """Overlay automatisch ausblenden nach Timer-Ablauf."""
        if self.overlay_visible:
            self.overlay_visible = False
            self._broadcast({'type': 'hide_overlay'})

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
                if 3 <= val <= 60:
                    updates['overlay_duration_seconds'] = val

            if 'operating_hours' in data:
                oh = data['operating_hours']
                if 'on_time' in oh and 'off_time' in oh:
                    updates['operating_hours'] = {
                        'on_time': oh['on_time'],
                        'off_time': oh['off_time']
                    }

            if updates:
                self.config.update_many(updates)
                self._reset_timer()
                logger.info("Einstellungen aktualisiert: %s", updates)

            return jsonify({'status': 'ok', 'settings': {
                'display_interval_seconds': self.config.display_interval_seconds,
                'overlay_duration_seconds': self.config.overlay_duration_seconds,
                'operating_hours': self.config.operating_hours,
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
                if self.overlay_visible:
                    ws.send(json.dumps({'type': 'show_overlay'}))
            except Exception:
                pass

            try:
                while True:
                    data = ws.receive(timeout=None)
                    if data is None:
                        break
                    msg = json.loads(data)
                    action = msg.get('action', '')
                    if action:
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
