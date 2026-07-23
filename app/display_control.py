# (c) Dr. Ralf Korell
# Galerist — Display-Steuerung (HDMI on/off via wlr-randr / xrandr)
# Modified: 2026-04-13, 19:40 - Erstellt
# Modified: 2026-07-20 - Fix: display_on-Startzustand None (unbekannt), erzwingt HDMI-Sync beim ersten Check nach Service-Restart
# Modified: 2026-07-20 - Fix: _detect_output ueberspringt Headless-Dummy (NOOP-*), waehlt realen Output statt erster Zeile
# Modified: 2026-07-23 - Umbau: Tool + Output aus Config (kein Raten), Detektion entfernt, Compositor-Wait vor erstem Schalten, Return-Code-Pruefung
# Modified: 2026-07-23 - Neu: seconds_until_next_boundary() fuer event-getriebenen Scheduler (kein 60-s-Polling mehr)

import logging
import os
import subprocess
from datetime import datetime, time, timedelta
from time import monotonic, sleep

logger = logging.getLogger(__name__)


class DisplayControl:
    """Steuert das HDMI-Display an/aus für Betriebsstunden.

    Tool (wlr-randr/xrandr) und Output-Name sind statische Fakten des Geräts
    und kommen aus der Konfiguration — es wird nichts zur Laufzeit geraten.
    """

    def __init__(self, backend: str, output: str):
        self.display_on: bool | None = None
        self._tool = backend
        self._output_name = output
        self._env = self._build_env()
        logger.info("Display-Steuerung: tool=%s, output=%s", self._tool, self._output_name)

    def wait_ready(self, timeout: float = 180.0, interval: float = 0.5) -> bool:
        """Wartet, bis der Compositor auf das Display-Tool antwortet.

        Verhindert, dass der erste Schaltvorgang beim Kaltstart ins Leere läuft,
        weil der Compositor-Socket noch nicht bereit ist.

        Returns:
            True wenn der Compositor bereit ist, False bei Timeout.
        """
        deadline = monotonic() + timeout
        logger.info("Warte auf Compositor-Bereitschaft (%s)...", self._tool)
        while True:
            if self._probe():
                logger.info("Compositor bereit")
                return True
            if monotonic() >= deadline:
                logger.warning("Compositor nach %ss nicht bereit — fahre fort (Retry via Schedule-Check)", timeout)
                return False
            sleep(interval)

    def turn_on(self):
        """Display einschalten."""
        if self.display_on is True:
            return
        if self._tool == 'wlr-randr':
            result = self._run(['wlr-randr', '--output', self._output_name, '--on'])
        else:
            result = self._run(['xrandr', '--output', self._output_name, '--auto'])
        if result.returncode == 0:
            self.display_on = True
            logger.info("Display eingeschaltet")
        else:
            logger.warning("Display-Einschalten fehlgeschlagen, Zustand bleibt unbekannt")

    def turn_off(self):
        """Display ausschalten."""
        if self.display_on is False:
            return
        if self._tool == 'wlr-randr':
            result = self._run(['wlr-randr', '--output', self._output_name, '--off'])
        else:
            result = self._run(['xrandr', '--output', self._output_name, '--off'])
        if result.returncode == 0:
            self.display_on = False
            logger.info("Display ausgeschaltet")
        else:
            logger.warning("Display-Ausschalten fehlgeschlagen, Zustand bleibt unbekannt")

    def check_operating_hours(self, on_time_str: str, off_time_str: str) -> bool:
        """Prüft Betriebsstunden und schaltet Display entsprechend.

        Unterstützt Mitternachts-Crossing (z.B. on=22:00, off=06:00).

        Returns:
            True wenn Display an sein soll, False wenn aus.
        """
        now = datetime.now().time()
        on_time = self._parse_time(on_time_str)
        off_time = self._parse_time(off_time_str)

        if on_time <= off_time:
            # Normaler Fall: z.B. 07:00 – 23:00
            should_be_on = on_time <= now < off_time
        else:
            # Mitternachts-Crossing: z.B. 22:00 – 06:00
            should_be_on = now >= on_time or now < off_time

        if should_be_on and self.display_on is not True:
            self.turn_on()
        elif not should_be_on and self.display_on is not False:
            self.turn_off()

        return should_be_on

    def seconds_until_next_boundary(self, on_time_str: str, off_time_str: str) -> float:
        """Sekunden bis zum nächsten Umschaltpunkt (nächstes on_time oder off_time)."""
        now_dt = datetime.now()
        seconds = []
        for t in (self._parse_time(on_time_str), self._parse_time(off_time_str)):
            cand = now_dt.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if cand <= now_dt:
                cand += timedelta(days=1)
            seconds.append((cand - now_dt).total_seconds())
        return min(seconds)

    def _probe(self) -> bool:
        """Leichtgewichtiger Bereitschaftstest: antwortet das Display-Tool?"""
        if self._tool == 'wlr-randr':
            result = self._run(['wlr-randr'])
        else:
            result = self._run(['xrandr', '--query'])
        return result.returncode == 0 and bool(result.stdout)

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        """Kommando ausführen mit Wayland-Environment."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                env=self._env, timeout=10
            )
            if result.returncode != 0:
                logger.warning("Kommando fehlgeschlagen: %s → %s", ' '.join(cmd), result.stderr.strip())
            return result
        except subprocess.TimeoutExpired:
            logger.error("Kommando-Timeout: %s", ' '.join(cmd))
            return subprocess.CompletedProcess(cmd, 1)

    def _build_env(self) -> dict:
        """Environment für wlr-randr (braucht Wayland-Variablen)."""
        env = os.environ.copy()
        env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        env.setdefault('WAYLAND_DISPLAY', 'wayland-0')
        return env

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """'HH:MM' String in time-Objekt wandeln."""
        parts = time_str.split(':')
        return time(int(parts[0]), int(parts[1]))
