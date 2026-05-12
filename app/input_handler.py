# (c) Dr. Ralf Korell
# Galerist — Input-Handler für Bluetooth-Fernbedienung (libevdev)
# Modified: 2026-04-13, 19:45 - Erstellt
# Modified: 2026-04-13, 21:15 - BT005 Lenkrad-FB statt Fire TV Remote
# Modified: 2026-05-06 - Capability-basierte Geräteerkennung (statt Namen-Match)
# Modified: 2026-05-06 - Bus-Filter (BT/USB), schließt vc4-hdmi und Konsorten aus
# Modified: 2026-05-06 - Selbstheilung: bluetoothctl disconnect+connect nach 3 erfolglosen Polls
# Modified: 2026-05-07 - Polling+Heilung raus, pyudev-Monitor rein (event-getrieben)

import logging
import os
import threading
from collections.abc import Callable

import libevdev
import pyudev

logger = logging.getLogger(__name__)

# Bluetooth-Fernbedienung Tasten → Galerist-Aktionen
# BT005 Lenkrad-FB: 5 Tasten (Play/Pause, Vol+/-, Forward/Back)
# Fire TV Remote Tasten bleiben als Fallback erhalten
KEY_MAP = {
    # BT005 Lenkrad-Fernbedienung
    libevdev.EV_KEY.KEY_NEXTSONG:     'next',
    libevdev.EV_KEY.KEY_PREVIOUSSONG: 'prev',
    libevdev.EV_KEY.KEY_VOLUMEUP:     'info_on',
    libevdev.EV_KEY.KEY_VOLUMEDOWN:   'info_off',
    libevdev.EV_KEY.KEY_PLAYPAUSE:    'playpause',
    # Fire TV Remote (Fallback)
    libevdev.EV_KEY.KEY_LEFT:         'prev',
    libevdev.EV_KEY.KEY_RIGHT:        'next',
    libevdev.EV_KEY.KEY_UP:           'info_on',
    libevdev.EV_KEY.KEY_DOWN:         'info_off',
    libevdev.EV_KEY.KEY_ENTER:        'select',
    libevdev.EV_KEY.KEY_SELECT:       'select',
    libevdev.EV_KEY.KEY_BACK:         'back',
    libevdev.EV_KEY.KEY_PLAY:         'playpause',
    libevdev.EV_KEY.KEY_PAUSE:        'playpause',
}


# Multimedia-Keys, an denen eine Fernbedienung erkannt wird.
# Ein Device gilt als FB, wenn (a) der Bus zugelassen ist und (b) mindestens
# MIN_MATCHING_KEYS davon unterstützt werden — unabhängig vom Geräte-Namen.
# Damit funktionieren neue FB-Modelle ohne Code-Änderung.
REQUIRED_KEYS_ANY = [
    libevdev.EV_KEY.KEY_PLAYPAUSE,
    libevdev.EV_KEY.KEY_NEXTSONG,
    libevdev.EV_KEY.KEY_PREVIOUSSONG,
    libevdev.EV_KEY.KEY_VOLUMEUP,
    libevdev.EV_KEY.KEY_VOLUMEDOWN,
]
MIN_MATCHING_KEYS = 2

# Erlaubte Bus-Typen (linux/input.h): 0x05 = BLUETOOTH, 0x03 = USB
# Schließt vc4-hdmi (Virtual/HOST), I2C-Touch etc. aus, die zufällig einige
# Multimedia-Keys mit-advertisen.
ALLOWED_BUS_TYPES = {0x05, 0x03}


def _is_remote(dev: libevdev.Device) -> bool:
    if dev.id.get('bustype') not in ALLOWED_BUS_TYPES:
        return False
    return sum(1 for k in REQUIRED_KEYS_ANY if dev.has(k)) >= MIN_MATCHING_KEYS


def find_remote_device(config_device: str | None = None) -> str | None:
    """Bluetooth-Fernbedienung als Input-Device finden.

    Sucht das erste Input-Device, das mindestens MIN_MATCHING_KEYS der
    erwarteten Multimedia-Keys (REQUIRED_KEYS_ANY) unterstützt.

    Args:
        config_device: Explizit konfigurierter Device-Pfad (z.B. /dev/input/event4).

    Returns:
        Device-Pfad oder None wenn nicht gefunden.
    """
    if config_device and os.path.exists(config_device):
        logger.info("Verwende konfiguriertes Input-Device: %s", config_device)
        return config_device

    for i in range(20):
        path = f'/dev/input/event{i}'
        if not os.path.exists(path):
            continue
        try:
            fd = open(path, 'rb')
            dev = libevdev.Device(fd)
            try:
                if _is_remote(dev):
                    logger.info(
                        "Fernbedienung gefunden: %s (Name=%r, UNIQ=%s)",
                        path, dev.name, dev.uniq,
                    )
                    fd.close()
                    return path
            finally:
                fd.close()
        except (PermissionError, OSError) as e:
            logger.debug("Kann %s nicht öffnen: %s", path, e)
            continue

    return None


class InputHandler:
    """Liest Tasten-Events einer Bluetooth-Fernbedienung in einem Background-Thread.

    Nutzt pyudev.Monitor, um auf udev-Events des input-Subsystems zu reagieren.
    Kein Polling — der Thread blockiert auf Monitor.poll() und reagiert event-
    getrieben, sobald ein neues /dev/input/eventN erscheint oder verschwindet.

    Ruft bei jedem erkannten Tastendruck den callback mit dem Aktionsnamen auf
    (z.B. 'next', 'prev', 'info_on').
    """

    def __init__(self, callback: Callable[[str], None], config):
        self.callback = callback
        self.config = config
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name='InputHandler')
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        """Event-Loop: udev-Monitor auf /dev/input, beim Erscheinen eines
        passenden Devices öffnen und Tasten lesen. Bei Verlust zurück in den Wait."""
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem='input')
        monitor.start()

        logged_waiting = False
        while self._running:
            # Initial / nach Verlust: einmal direkt versuchen
            device_path = find_remote_device(self.config.input_device)

            if not device_path:
                if not logged_waiting:
                    logger.info("Warte auf Fernbedienung (udev-Monitor) ...")
                    logged_waiting = True
                # Auf udev-Event warten (Timeout 1 s, damit stop() schnell greift)
                udev_event = monitor.poll(timeout=1.0)
                if udev_event is None:
                    continue
                if udev_event.action != 'add':
                    continue
                # Device-Node muss /dev/input/event* sein
                node = udev_event.device_node
                if not node or not node.startswith('/dev/input/event'):
                    continue
                # Im nächsten Loop-Durchlauf prüft find_remote_device, ob das
                # neue Device unsere FB ist (Capability+Bus-Filter).
                continue

            logged_waiting = False

            try:
                fd = open(device_path, 'rb')
                dev = libevdev.Device(fd)
            except (PermissionError, OSError) as e:
                logger.error("Kann Device nicht öffnen (%s): %s", device_path, e)
                # Kurz auf udev-Event warten, statt sofort zu hämmern
                monitor.poll(timeout=1.0)
                continue

            logger.info("Input-Handler aktiv: %s (%s)", dev.name, device_path)

            try:
                for event in dev.events():
                    if not self._running:
                        break
                    if event.matches(libevdev.EV_KEY) and event.value == 1:
                        action = KEY_MAP.get(event.code)
                        if action:
                            logger.debug("Taste: %s → %s", event.code, action)
                            self.callback(action)
            except libevdev.EventsDroppedException:
                for _ in dev.sync():
                    pass
            except OSError as e:
                logger.warning("Fernbedienung getrennt: %s — Monitor reaktiviert", e)
            finally:
                fd.close()

        logger.info("Input-Handler beendet")
