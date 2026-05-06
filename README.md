# Galerist — Digitaler Bilderrahmen

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
    ├── display_control.py     Display on/off via wlr-randr
    ├── static/                Frontend (Kiosk-Anzeige + Web-App)
    ├── systemd/               System-Service-Vorlage
    └── tools/decode_remote.py Diagnose-Werkzeug für Input-Devices
```

## Voraussetzungen

- Linux mit Display-Server (Wayland **oder** X11) — `display_control.py` erkennt automatisch und nutzt `wlr-randr` bzw. `xrandr`
- Python 3.11+, Flask, libevdev (System + Python-Binding), Pillow
- Chromium
- `bluez` — nur falls eine BT-Fernbedienung genutzt werden soll
- Optional für die Diagnose: `evtest`

## Konfiguration

`app/config.json.example` als Vorlage nach `app/config.json` kopieren und anpassen. `config.json` selbst ist per `.gitignore` ausgeschlossen.

| Feld | Bedeutung |
|---|---|
| `image_directory` | absoluter Pfad zur JPEG-Sammlung |
| `metadata_cache_file` | Pfad für den persistierten XMP/IPTC-Cache |
| `display_interval_seconds` | Wartezeit zwischen Bildwechseln |
| `overlay_duration_seconds` | wie lange das Metadaten-Overlay sichtbar ist |
| `operating_hours` | Display-Zeiten `on_time`/`off_time` (HH:MM, leer = immer an) |
| `flask_host`, `flask_port` | Bind-Adresse + Port der Web-App |
| `input_device` | `null` = Auto-Erkennung; expliziter `/dev/input/eventN` als Override |
| `log_level` | `INFO`, `DEBUG`, `WARNING`, ... |

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

### Erkennung (`input_handler.py`)

`find_remote_device()` sucht das passende Input-Device **ohne hardcodierten Namen**:

1. **Bus-Filter:** Bus = `0x05` (BT) oder `0x03` (USB). Schließt virtuelle Devices und I2C-Touch-Controller aus, die zufällig Multimedia-Keys mit-advertisen.
2. **Capability-Schwelle:** Device unterstützt mindestens 2 von `KEY_PLAYPAUSE`, `KEY_NEXTSONG`, `KEY_PREVIOUSSONG`, `KEY_VOLUMEUP`, `KEY_VOLUMEDOWN`.

Beliebige BT-HID-Geräte funktionieren ohne Code-Änderung. Manueller Override über `config.input_device` möglich.

### Selbstheilung nach Reboot

BlueZ stellt nach Reboot zwar die BT-Schicht zu paired+trusted Devices her (`Connected: yes`), zieht aber das **HID-Profil nicht aktiv hoch** — `/dev/input/eventN` fehlt. Der Auto-Reconnect-Loop in `input_handler.py` zählt erfolglose Such-Polls und triggert nach 15 s einmalig `bluetoothctl disconnect` + `connect`. Der explizite Connect baut alle Profile auf, HID ist da, das Suchen findet das Gerät beim nächsten Poll. Bei späterem Verbindungsverlust gilt derselbe Mechanismus — der Counter wird bei Erfolg zurückgesetzt.

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
