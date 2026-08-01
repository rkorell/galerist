# (c) Dr. Ralf Korell
# Galerist — Display-Steuerung (HDMI on/off via wlr-randr / xrandr / HDMI-CEC)
# Modified: 2026-04-13, 19:40 - Erstellt
# Modified: 2026-07-20 - Fix: display_on-Startzustand None (unbekannt), erzwingt HDMI-Sync beim ersten Check nach Service-Restart
# Modified: 2026-07-20 - Fix: _detect_output ueberspringt Headless-Dummy (NOOP-*), waehlt realen Output statt erster Zeile
# Modified: 2026-07-23 - Umbau: Tool + Output aus Config (kein Raten), Detektion entfernt, Compositor-Wait vor erstem Schalten, Return-Code-Pruefung
# Modified: 2026-07-23 - Neu: seconds_until_next_boundary() fuer event-getriebenen Scheduler (kein 60-s-Polling mehr)
# Modified: 2026-08-01 - Neu: CEC-Backend (display_backend=cec) fuer Fernseher — Standby/Wake per cec-ctl, Pi-Ausgang bleibt an

import logging
import os
import subprocess
from datetime import datetime, time, timedelta
from time import monotonic, sleep

logger = logging.getLogger(__name__)

# HDMI-CEC (Fernseher): Adapter, TV-Logical-Address (0 laut CEC-Spec), Physical-Address-Fallback
CEC_DEVICE = '/dev/cec0'
CEC_TV_ADDR = '0'
CEC_PHYS_ADDR_FALLBACK = '4.0.0.0'


class DisplayControl:
    """Steuert das Anzeigegerät an/aus für Betriebsstunden.

    Backend kommt aus der Konfiguration (kein Raten zur Laufzeit):
      - 'wlr-randr' / 'xrandr': Monitor, Bildsignal an/aus.
      - 'cec': Fernseher, Standby/Wake per HDMI-CEC (cec-ctl). Der HDMI-Ausgang
        des Pi bleibt dabei IMMER an — CEC funktioniert nur bei aktivem Signal.
    """

    def __init__(self, backend: str, output: str):
        self.display_on: bool | None = None
        self._tool = backend
        self._output_name = output
        self._cec = (backend == 'cec')
        self._cec_phys_addr = CEC_PHYS_ADDR_FALLBACK
        self._env = self._build_env()
        if self._cec:
            self._cec_register()
        logger.info("Display-Steuerung: backend=%s, output=%s%s",
                     self._tool, self._output_name,
                     f", cec-phys-addr={self._cec_phys_addr}" if self._cec else "")

    def wait_ready(self, timeout: float = 180.0, interval: float = 0.5) -> bool:
        """Wartet, bis das Display-Backend bereit ist (Compositor bzw. CEC-Adapter).

        Verhindert, dass der erste Schaltvorgang beim Kaltstart ins Leere läuft.

        Returns:
            True wenn bereit, False bei Timeout.
        """
        deadline = monotonic() + timeout
        logger.info("Warte auf %s-Bereitschaft...", 'CEC-Adapter' if self._cec else 'Compositor')
        while True:
            if self._probe():
                logger.info("%s bereit", 'CEC-Adapter' if self._cec else 'Compositor')
                return True
            if monotonic() >= deadline:
                logger.warning("Backend nach %ss nicht bereit — fahre fort (Retry via Schedule-Check)", timeout)
                return False
            sleep(interval)

    def turn_on(self):
        """Anzeige einschalten."""
        if self.display_on is True:
            return
        if self._cec:
            ok = self._cec_send([
                ['cec-ctl', '-d', CEC_DEVICE, '--to', CEC_TV_ADDR, '--image-view-on'],
                ['cec-ctl', '-d', CEC_DEVICE, '--active-source', f'phys-addr={self._cec_phys_addr}'],
            ])
        elif self._tool == 'wlr-randr':
            ok = self._run(['wlr-randr', '--output', self._output_name, '--on']).returncode == 0
        else:
            ok = self._run(['xrandr', '--output', self._output_name, '--auto']).returncode == 0
        if ok:
            self.display_on = True
            logger.info("Display eingeschaltet")
        else:
            logger.warning("Display-Einschalten fehlgeschlagen, Zustand bleibt unbekannt")

    def turn_off(self):
        """Anzeige ausschalten."""
        if self.display_on is False:
            return
        if self._cec:
            # KEIN wlr-randr --off: der Pi-Ausgang bleibt an, sonst reißt die CEC-Leitung ab.
            ok = self._cec_send([
                ['cec-ctl', '-d', CEC_DEVICE, '--to', CEC_TV_ADDR, '--standby'],
            ])
        elif self._tool == 'wlr-randr':
            ok = self._run(['wlr-randr', '--output', self._output_name, '--off']).returncode == 0
        else:
            ok = self._run(['xrandr', '--output', self._output_name, '--off']).returncode == 0
        if ok:
            self.display_on = False
            logger.info("Display ausgeschaltet")
        else:
            logger.warning("Display-Ausschalten fehlgeschlagen, Zustand bleibt unbekannt")

    def check_operating_hours(self, on_time_str: str, off_time_str: str) -> bool:
        """Prüft Betriebsstunden und schaltet die Anzeige entsprechend.

        Unterstützt Mitternachts-Crossing (z.B. on=22:00, off=06:00).

        Returns:
            True wenn Anzeige an sein soll, False wenn aus.
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

    # ── CEC (Fernseher) ───────────────────────────────────────

    def _cec_register(self):
        """CEC-Adapter als Playback-Gerät registrieren und Physical Address auslesen."""
        self._run(['cec-ctl', '-d', CEC_DEVICE, '--playback'])
        self._cec_phys_addr = self._cec_read_phys_addr()

    def _cec_read_phys_addr(self) -> str:
        """Physical Address aus cec-ctl auslesen (Fallback bei ungültig)."""
        result = self._run(['cec-ctl', '-d', CEC_DEVICE])
        for line in result.stdout.split('\n'):
            if 'Physical Address' in line and ':' in line:
                addr = line.split(':', 1)[1].strip()
                if addr and addr.lower() != 'f.f.f.f':
                    return addr
        return CEC_PHYS_ADDR_FALLBACK

    def _cec_la_ready(self) -> bool:
        """True, wenn dem Adapter eine Logical Address zugeteilt ist (Mask != 0x0000)."""
        result = self._run(['cec-ctl', '-d', CEC_DEVICE])
        for line in result.stdout.split('\n'):
            if 'Logical Address Mask' in line and ':' in line:
                mask = line.split(':', 1)[1].strip()
                return mask not in ('0x0000', '')
        return False

    def _cec_send(self, cmds: list[list[str]], _retried: bool = False) -> bool:
        """Sendet ein oder mehrere cec-ctl-Kommandos. Bei Fehler einmal
        re-registrieren (CEC ist zickig) und die Sequenz wiederholen."""
        for cmd in cmds:
            if self._run(cmd).returncode != 0:
                if not _retried:
                    logger.warning("CEC-Kommando fehlgeschlagen — re-registriere und wiederhole")
                    self._run(['cec-ctl', '-d', CEC_DEVICE, '--playback'])
                    return self._cec_send(cmds, _retried=True)
                return False
        return True

    def _probe(self) -> bool:
        """Leichtgewichtiger Bereitschaftstest je Backend."""
        if self._cec:
            return os.path.exists(CEC_DEVICE) and self._cec_la_ready()
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
