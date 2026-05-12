# (c) Dr. Ralf Korell
# Galerist — D-Bus-Watcher: erzwingt HID-Profil-Connect bei FB-Wake
# Modified: 2026-05-07 - Erstellt
# Modified: 2026-05-07 - ServicesResolved als HID-Aktiv-Indikator (Input1-Interface ist bloßer Marker)

"""Hört auf BlueZ-D-Bus-Signale (ObjectManager.InterfacesAdded und
PropertiesChanged auf Device1) und ruft bei einem HID-fähigen Device
ohne aktives Input1-Interface gezielt ConnectProfile(HID-UUID) auf.

Löst das BlueZ-5.82-Multi-Profile-Problem: Wenn die FB nach Sleep/Reboot
aufwacht und Advertising sendet, baut BlueZ allein das HID-Profil oft
nicht zuverlässig auf. Dieser Watcher erzwingt das HID-Profil aktiv.

Läuft als Daemon-Thread mit eigener GLib-MainLoop. Kein Polling.
"""

import logging
import threading

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gio  # noqa: E402

logger = logging.getLogger(__name__)

# Bluetooth SIG: HID Service
HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"

BLUEZ_NAME = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
INPUT_IFACE = "org.bluez.Input1"
OBJ_MGR_IFACE = "org.freedesktop.DBus.ObjectManager"


class BTHidWatcher:
    """Daemon-Thread, der BlueZ-D-Bus überwacht und das HID-Profil
    aktiv aufbaut, wenn ein HID-fähiges Device aufwacht."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._loop: GLib.MainLoop | None = None
        self._bus = None
        self._om_proxy: Gio.DBusProxy | None = None
        self._device_proxies: dict[str, Gio.DBusProxy] = {}

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name='BTHidWatcher')
        self._thread.start()

    def stop(self):
        if self._loop and self._loop.is_running():
            self._loop.quit()

    def _run(self):
        self._loop = GLib.MainLoop()
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._setup_subscriptions()
            logger.info("BTHidWatcher gestartet (HID-UUID %s)", HID_UUID)
            self._loop.run()
        except GLib.Error as e:
            logger.error("BTHidWatcher: D-Bus-Fehler: %s", e.message)
        except Exception as e:
            logger.error("BTHidWatcher: Fehler im MainLoop: %s", e)
        finally:
            logger.info("BTHidWatcher beendet")

    def _setup_subscriptions(self):
        """ObjectManager abonnieren und initialen Bestand durchgehen."""
        self._om_proxy = Gio.DBusProxy.new_sync(
            self._bus,
            Gio.DBusProxyFlags.NONE,
            None,
            BLUEZ_NAME,
            "/",
            OBJ_MGR_IFACE,
            None,
        )
        self._om_proxy.connect("g-signal", self._on_om_signal)

        # Initialer Scan: alle bekannten Devices durchgehen.
        # Erfasst den Fall "FB ist im Cache, aber HID nicht aktiv" beim Start.
        try:
            result = self._om_proxy.call_sync(
                "GetManagedObjects", None, Gio.DBusCallFlags.NONE, -1, None
            )
            objects = result.unpack()[0]
        except GLib.Error as e:
            logger.warning("BTHidWatcher: GetManagedObjects fehlgeschlagen: %s", e.message)
            return

        for path, ifaces in objects.items():
            if DEVICE_IFACE in ifaces:
                self._handle_device(path, ifaces[DEVICE_IFACE])

    def _on_om_signal(self, proxy, sender, signal, params):
        if signal == "InterfacesAdded":
            path, ifaces = params.unpack()
            if DEVICE_IFACE in ifaces:
                self._handle_device(path, ifaces[DEVICE_IFACE])
        elif signal == "InterfacesRemoved":
            path, ifaces = params.unpack()
            if DEVICE_IFACE in ifaces and path in self._device_proxies:
                del self._device_proxies[path]
                logger.info("BTHidWatcher: Device entfernt: %s", path)

    def _handle_device(self, path: str, device_props: dict):
        """Neues oder bekanntes Device prüfen und ggf. Subscribe + Connect triggern.

        ServicesResolved=False ist der echte Indikator dafür, dass das HID-Profil
        nicht produktiv läuft — Input1-Interface allein ist nur ein Marker und
        existiert auch ohne aktive HID-Connection.
        """
        uuids = device_props.get("UUIDs", [])
        if HID_UUID not in uuids:
            return  # Kein HID-fähiges Device, ignorieren

        # Property-Changes auf diesem Device beobachten (Wake-Trigger)
        if path not in self._device_proxies:
            try:
                proxy = Gio.DBusProxy.new_sync(
                    self._bus,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    BLUEZ_NAME,
                    path,
                    DEVICE_IFACE,
                    None,
                )
                proxy.connect("g-properties-changed", self._on_props_changed, path)
                self._device_proxies[path] = proxy
                logger.info("BTHidWatcher: HID-Device beobachtet %s", path)
            except GLib.Error as e:
                logger.warning("BTHidWatcher: Device-Proxy fehlgeschlagen %s: %s", path, e.message)
                return

        # ServicesResolved=False → HID-Profil ist nicht produktiv aufgebaut.
        # Auch wenn Connected=True (Cache/Zombie) — wir triggern aktiv.
        if not device_props.get("ServicesResolved", False):
            self._trigger_connect_profile(path)

    def _on_props_changed(self, proxy, changed_props, invalidated_props, path):
        """Wake-Indikatoren auf Device1: RSSI/ManufacturerData/ServicesResolved/Connected.
        Bei Trigger und fehlendem Input1-Interface: ConnectProfile."""
        triggers = {"RSSI", "ManufacturerData", "ServicesResolved", "Connected"}

        # changed_props kann GLib.Variant oder dict-like sein, je nach Version
        if hasattr(changed_props, "keys"):
            changed_keys = set(changed_props.keys())
        else:
            try:
                changed_keys = set(changed_props.unpack().keys())
            except Exception:
                return

        if not triggers.intersection(changed_keys):
            return

        # Wenn ServicesResolved=True dabei ist: HID-Profil ist sauber aufgebaut, fertig
        if hasattr(changed_props, "get"):
            sr = changed_props.get("ServicesResolved")
        else:
            unpacked = changed_props.unpack()
            sr = unpacked.get("ServicesResolved")
        if sr is True:
            logger.debug("BTHidWatcher: %s ServicesResolved → HID aufgebaut", path)
            return

        # Sonst: Wake-Signal, HID noch nicht produktiv → ConnectProfile triggern
        self._trigger_connect_profile(path)

    def _trigger_connect_profile(self, path: str):
        """Ruft Device1.ConnectProfile(HID-UUID) auf. Idempotent — Errors werden geloggt."""
        device_proxy = self._device_proxies.get(path)
        if device_proxy is None:
            return
        logger.info("BTHidWatcher: ConnectProfile(HID) für %s", path)
        try:
            device_proxy.call_sync(
                "ConnectProfile",
                GLib.Variant("(s)", (HID_UUID,)),
                Gio.DBusCallFlags.NONE,
                10000,  # 10 s Timeout
                None,
            )
            logger.info("BTHidWatcher: HID-Profil verbunden für %s", path)
        except GLib.Error as e:
            msg = e.message or ""
            if "AlreadyConnected" in msg or "InProgress" in msg:
                logger.debug("BTHidWatcher: ConnectProfile %s — %s (idempotent)", path, msg)
            else:
                logger.warning("BTHidWatcher: ConnectProfile %s fehlgeschlagen: %s", path, msg)
