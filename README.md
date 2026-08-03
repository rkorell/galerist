# Galerist — Digitaler Bilderrahmen

*Stand: 2026-08-03*

Ersatz für proprietäre digitale Bilderrahmen wie den Netgear Meural Canvas II auf einem Linux-Gerät mit Wayland. Zeigt eine kuratierte Bildersammlung im Vollbild, blendet Metadaten als Museums-Schild ein, lässt sich optional per Bluetooth-HID-Eingabegerät steuern. Liest die Anzeige-Metadaten direkt aus IPTC/XMP der JPEG-Dateien — **autark zur Laufzeit**, keine Datenbank, keine Netzwerk-Abhängigkeit.

Optimiert für RAM-arme Single-Board-Computer (Single-Process-Chromium für Systeme ab ~1 GB RAM).

## Architektur

```
galerist/
└── app/
    ├── galerist.py            Haupt-Entry, WebSocket, Bildwechsel-Scheduler
    ├── config.py              Loader für config.json
    ├── metadata_cache.py      IPTC/XMP-Cache, beim Start aus JPEGs gelesen
    ├── input_handler.py       Optionale BT-Fernbedienung via libevdev
    ├── bt_watcher.py          D-Bus-Watcher, stellt HID-Profil nach Wake/Reboot her
    ├── display_control.py     Display an/aus — Backend wählbar (wlr-randr / HDMI-CEC / xrandr)
    ├── static/                Frontend (Kiosk-Anzeige + Web-App)
    ├── systemd/               System-Service-Vorlage
    └── tools/decode_remote.py Diagnose-Werkzeug für Input-Devices
```

## Voraussetzungen

- Linux mit Display-Server (Wayland **oder** X11) — das Anzeige-Backend wird per Config gewählt (`display_backend`, siehe unten)
- Python 3.11+, Flask, flask-sock, libevdev (System + Python-Binding), pyudev, Pillow
- Chromium
- `wlr-randr` (Wayland) bzw. `xrandr` (X11) — für das Monitor-Backend (Bildsignal an/aus)
- `cec-utils`/`v4l-utils` (`cec-ctl`) — nur für das Fernseher-Backend (HDMI-CEC), User in Gruppe `video`
- `bluez` — nur falls eine BT-Fernbedienung genutzt werden soll
- Optional für die Diagnose: `evtest`

## Konfiguration

`app/config.json.example` als Vorlage nach `app/config.json` kopieren und anpassen. `config.json` selbst ist per `.gitignore` ausgeschlossen.

| Feld | Bedeutung |
|---|---|
| `image_directory` | absoluter Pfad zur JPEG-Sammlung |
| `metadata_cache_file` | Pfad für den persistierten XMP/IPTC-Cache |
| `display_interval_seconds` | Wartezeit zwischen Bildwechseln |
| `overlay_duration_seconds` | wie lange das Metadaten-Overlay sichtbar ist; `0` = kein automatisches Ausblenden (bleibt offen) |
| `display_backend` | `wlr-randr` (Monitor), `cec` (Fernseher via HDMI-CEC) oder `xrandr` (X11-Legacy) |
| `display_output` | Ausgang, z. B. `HDMI-A-1` |
| `display_brightness` | Software-Dimmer 20–100 (Overlay-Deckkraft, kein Hardware-Backlight) |
| `operating_hours` | Display-Zeiten `on_time`/`off_time` (HH:MM, leer = immer an) |
| `flask_host`, `flask_port` | Bind-Adresse + Port der Web-App |
| `input_device` | `null` = Auto-Erkennung; expliziter `/dev/input/eventN` als Override |
| `log_level` | `INFO`, `DEBUG`, `WARNING`, ... |

Nur für das Fernseher-Backend (`display_backend: cec`) relevant: `tv_keepshallow_minutes` / `tv_keepshallow_seconds` (kurzer CEC-Puls im Intervall, damit der TV nicht in nicht-weckbares Deep-Standby fällt). Für eine BT-Fernbedienung mit Akku-Meldung optional: `fb_battery_mac` und `fb_battery_warn_percent`.

## Anzeige (Backend, Betriebszeiten, Helligkeit)

Das Anzeige-Backend ist **bewusst config-gesteuert** (`display_backend`), keine Auto-Erkennung — ein zickiger CEC-Poll könnte den Modus sonst für den Tag verstellen:

- **`wlr-randr` (Monitor):** schaltet das Bildsignal an/aus (`--on`/`--off`).
- **`cec` (Fernseher):** schaltet den TV per HDMI-CEC in Standby bzw. weckt ihn (`cec-ctl`). Der Pi-HDMI-Ausgang bleibt dabei **immer an** — CEC wirkt nur bei aktivem Ausgang; würde er abgeschaltet, risse die CEC-Leitung ab. Manche Fernseher fallen aus stundenlangem Standby in ein nicht mehr per CEC weckbares Deep-Standby; dagegen hält ein kurzer Keep-shallow-Puls (`tv_keepshallow_*`) den TV flach.
- **`xrandr`:** X11-Fallback (Legacy).

Ein Wechsel des Backends in der Web-App wirkt **erst nach Service-Neustart** (das Backend wird beim App-Start gelesen).

Die **Betriebszeiten** (`operating_hours`) steuert ein event-getriebener Scheduler, der bis zum nächsten Umschaltpunkt schläft (kein Polling) — genau zwei Schaltvorgänge pro Tag.

Die **Helligkeit** ist ein Software-Dimmer: ein schwarzes Overlay legt sich gamma-korrigiert über das Bild (`opacity = 1 − (v/100)^1.4`, `v` = `display_brightness`, Bereich 20–100). Der Slider in der Web-App wirkt live per WebSocket. Hinweis: bei `display_brightness = 20` ist das Bild ~90 % abgedunkelt und **wirkt wie ausgeschaltet**, obwohl die Anzeige läuft.

## Service

`app/systemd/galerist.service` als Vorlage — vor dem Aktivieren `WorkingDirectory`, `ExecStart`, `User`, `Environment` an die eigene Umgebung anpassen.

Wenn der Service als System-Service (nicht als User-Service) läuft, muss die Anbindung an die laufende Display-Session über `Environment=` gesetzt werden — sonst startet Chromium ohne sichtbares Fenster. Welche Variablen nötig sind, hängt vom Display-Server ab:

- **Wayland:** `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<UID>/bus` (und i.d.R. `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`)
- **X11:** `DISPLAY=:0` und `XAUTHORITY=/home/<user>/.Xauthority`

## Bluetooth-Fernbedienung (optional)

Galerist erkennt Bluetooth-HID-Eingabegeräte, die Multimedia-Keys senden — Mini-Remotes, kleine Tastaturen, FBs in beliebiger Form-Faktor.

### Tasten-Mapping

| Keycode | App-Aktion |
|---|---|
| `KEY_VOLUMEUP` | Metadaten-Overlay einblenden |
| `KEY_NEXTSONG` | nächstes Bild |
| `KEY_VOLUMEDOWN` | Overlay ausblenden |
| `KEY_PREVIOUSSONG` | vorheriges Bild |
| `KEY_PLAYPAUSE` | Bildwechsel pausieren |

### Pairing

Standard-`bluetoothctl`-Sequenz: `power on`, `pairable on`, `scan on`, FB in Pairing-Modus bringen, dann `pair`/`trust`/`connect` mit der MAC.

### Multi-Profile-Konflikt unter BlueZ ≥ 5.82

Viele günstige BT-HID-Geräte advertisen neben HID auch A2DP/AVRCP/HFP. Wireplumber/PipeWire versucht beim Connect zuerst A2DP-Audio → Gerät lehnt ab (`Connection refused (111)`) → das HID-Profil kommt nicht hoch → kein `/dev/input/eventN`.

**Workaround:** `bluetoothd` ohne Audio-Plugins starten. Drop-in-Datei `/etc/systemd/system/bluetooth.service.d/override.conf`:

```ini
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd -P audio,a2dp,avrcp
```

Aktivieren mit `systemctl daemon-reload && systemctl restart bluetooth`. Nicht anwendbar auf Systemen, die BT-Audio brauchen.

`DisablePlugins=` in `/etc/bluetooth/main.conf` funktioniert **nicht** — BlueZ 5.82 ignoriert es als „Unknown key", die Plugin-Disable-Option ist nur als Kommandozeilenparameter gültig.

### Erkennung (`input_handler.py`, udev-getrieben)

`InputHandler` nutzt `pyudev.Monitor` auf das `input`-Subsystem. Sobald ein neues `/dev/input/eventN` erscheint, wird `find_remote_device()` aufgerufen, das **ohne hardcodierten Namen** filtert:

1. **Bus-Filter:** Bus = `0x05` (BT) oder `0x03` (USB). Schließt virtuelle Devices und I2C-Touch-Controller aus, die zufällig Multimedia-Keys mit-advertisen.
2. **Capability-Schwelle:** Device unterstützt mindestens 2 von `KEY_PLAYPAUSE`, `KEY_NEXTSONG`, `KEY_PREVIOUSSONG`, `KEY_VOLUMEUP`, `KEY_VOLUMEDOWN`.

Beliebige BT-HID-Geräte funktionieren ohne Code-Änderung. Manueller Override über `config.input_device` möglich. Kein Polling — der Handler reagiert event-getrieben.

### HID-Profil-Trigger (`bt_watcher.py`, D-Bus-getrieben)

BlueZ stellt nach Reboot/Wake zwar die BT-Schicht zu paired+trusted Devices her (`Connected: yes`), baut das **HID-Profil aber nicht zuverlässig auf** — `/dev/input/eventN` fehlt. `BTHidWatcher` lauscht parallel auf BlueZ-D-Bus-Signale (`org.bluez.ObjectManager.InterfacesAdded` und `Properties.PropertiesChanged` auf `Device1`). Sobald ein Device, das die HID-Service-UUID (`00001124-…`) advertised, ein Wake-Signal sendet (`RSSI`-Update / `ManufacturerData` / `ServicesResolved`), ruft der Watcher gezielt `Device1.ConnectProfile(HID-UUID)` auf. BlueZ baut daraufhin das HID-Profil auf, der Kernel exportiert `eventN`, der `InputHandler` greift.

`bt_watcher` und `input_handler` arbeiten unabhängig — ein D-Bus-Subscriber, ein udev-Subscriber, kein Sync-Event zwischen ihnen.

### Akku-Warnung (optional)

Ist in der Config eine `fb_battery_mac` hinterlegt, liest die App den Akkustand der Fernbedienung aus dem BlueZ Battery Service (`bluetoothctl info`) und blendet bei Unterschreiten von `fb_battery_warn_percent` (Default 20 %) eine Warnung im Overlay ein.

### Diagnose-Tool: `decode_remote.py`

```bash
python3 app/tools/decode_remote.py --list                  # alle Input-Devices mit Bus + Capability-Score
python3 app/tools/decode_remote.py /dev/input/eventN       # Live-Events EVIOCGRAB-geschützt mitlesen
```

Schreibt parallel in ein Logfile. Hilft bei FB-Tausch oder Auto-Detect-Problemen.

## Bildersammlung

JPEGs mit IPTC/XMP-Metadaten. Gelesene Felder: `dc:Creator`, `dc:Title`, `photoshop:DateCreated`, `dc:Description`, `photoshop:Source`, `photoshop:City`. Die App liest die Metadaten beim ersten Start und persistiert einen JSON-Cache; Folgestarts sind schnell.

## Lizenz

Keine Lizenz hinterlegt. Code als Referenz/Inspiration für eigene Bilderrahmen-Projekte gedacht.
