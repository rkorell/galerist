# (c) Dr. Ralf Korell
# Galerist — Display-Steuerung (HDMI on/off via wlr-randr / xrandr)
# Modified: 2026-04-13, 19:40 - Erstellt
# Modified: 2026-07-20 - Fix: display_on-Startzustand None (unbekannt), erzwingt HDMI-Sync beim ersten Check nach Service-Restart
# Modified: 2026-07-20 - Fix: _detect_output ueberspringt Headless-Dummy (NOOP-*), waehlt realen Output statt erster Zeile

import logging
import os
import subprocess
from datetime import datetime, time

logger = logging.getLogger(__name__)


class DisplayControl:
    """Steuert das HDMI-Display an/aus für Betriebsstunden.

    Erkennt automatisch ob Wayland (wlr-randr) oder X11 (xrandr) läuft
    und ermittelt den Output-Namen dynamisch.
    """

    def __init__(self):
        self.display_on: bool | None = None
        self._tool = self._detect_tool()
        self._env = self._build_env()
        self._output_name = self._detect_output()
        logger.info("Display-Steuerung: tool=%s, output=%s", self._tool, self._output_name)

    def turn_on(self):
        """Display einschalten."""
        if self.display_on is True:
            return
        if self._tool == 'wlr-randr':
            self._run(['wlr-randr', '--output', self._output_name, '--on'])
        else:
            self._run(['xrandr', '--output', self._output_name, '--auto'])
        self.display_on = True
        logger.info("Display eingeschaltet")

    def turn_off(self):
        """Display ausschalten."""
        if self.display_on is False:
            return
        if self._tool == 'wlr-randr':
            self._run(['wlr-randr', '--output', self._output_name, '--off'])
        else:
            self._run(['xrandr', '--output', self._output_name, '--off'])
        self.display_on = False
        logger.info("Display ausgeschaltet")

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

    def _detect_tool(self) -> str:
        """Wayland oder X11 erkennen."""
        xdg_runtime = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        wayland_socket = os.path.join(xdg_runtime, 'wayland-0')
        if os.path.exists(wayland_socket):
            return 'wlr-randr'
        return 'xrandr'

    def _build_env(self) -> dict:
        """Environment für wlr-randr (braucht Wayland-Variablen)."""
        env = os.environ.copy()
        env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        env.setdefault('WAYLAND_DISPLAY', 'wayland-0')
        return env

    def _detect_output(self) -> str:
        """Output-Name dynamisch ermitteln (z.B. HDMI-A-1, DSI-2)."""
        if self._tool == 'wlr-randr':
            result = self._run(['wlr-randr'])
            if result.stdout:
                # Nicht-eingerückte Zeilen sind Output-Namen ('HDMI-A-1 "Make" "Model"'),
                # eingerückte Zeilen sind Eigenschaften. Headless-Dummy (NOOP-*) überspringen,
                # den wlroots anlegt, wenn der reale Output deaktiviert wurde.
                outputs = [line.split()[0] for line in result.stdout.split('\n')
                           if line and not line[0].isspace()]
                real = [o for o in outputs if not o.startswith('NOOP')]
                if real:
                    return real[0]
                if outputs:
                    return outputs[0]
        else:
            result = self._run(['xrandr', '--query'])
            if result.stdout:
                for line in result.stdout.split('\n'):
                    if ' connected' in line:
                        return line.split()[0]
        logger.warning("Kein Display-Output erkannt, verwende 'HDMI-A-1'")
        return 'HDMI-A-1'

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """'HH:MM' String in time-Objekt wandeln."""
        parts = time_str.split(':')
        return time(int(parts[0]), int(parts[1]))
