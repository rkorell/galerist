#!/usr/bin/env python3
# Galerist — Decoder fuer Bluetooth-Fernbedienungen
# Modified: 2026-05-06 - Erstellt zur Analyse der SmartRemote-Keycodes
# Modified: 2026-05-06 - --list-Modus ergaenzt (Capability-Check pro Device)

"""Liest Input-Events von /dev/input/eventN raw, gibt sie sofort am
Terminal aus und schreibt sie line-buffered ins Logfile.

EVIOCGRAB: solange das Skript laeuft, kommen die Events EXKLUSIV hier
an. Kein Shutdown durch KEY_POWER, kein anderes Tool sieht sie.

Aufruf:
  decode_remote.py --list                # Input-Devices listen
  decode_remote.py [device]              # Events lesen (Default: /dev/input/event3)
"""

import fcntl
import glob
import struct
import sys
from datetime import datetime

LOGFILE = '/home/pi/remote.log'

# input_event auf 64-bit Linux: timeval (16) + type (2) + code (2) + value (4)
EVENT_FORMAT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

# EVIOCGRAB ioctl-Nummer (aus linux/input.h)
EVIOCGRAB = 0x40044590

# Wichtigste Keycodes aus input-event-codes.h
KEY_NAMES = {
    1: 'KEY_ESC', 28: 'KEY_ENTER',
    102: 'KEY_HOME', 103: 'KEY_UP', 104: 'KEY_PAGEUP',
    105: 'KEY_LEFT', 106: 'KEY_RIGHT', 108: 'KEY_DOWN', 109: 'KEY_PAGEDOWN',
    114: 'KEY_VOLUMEDOWN', 115: 'KEY_VOLUMEUP', 116: 'KEY_POWER',
    119: 'KEY_PAUSE', 128: 'KEY_STOP',
    139: 'KEY_MENU', 158: 'KEY_BACK',
    163: 'KEY_NEXTSONG', 164: 'KEY_PLAYPAUSE',
    165: 'KEY_PREVIOUSSONG', 166: 'KEY_STOPCD',
    172: 'KEY_HOMEPAGE', 174: 'KEY_EXIT',
    207: 'KEY_PLAY', 208: 'KEY_FASTFORWARD',
    232: 'KEY_BRIGHTNESSDOWN', 233: 'KEY_BRIGHTNESSUP',
    353: 'KEY_SELECT', 369: 'KEY_NUMERIC_STAR', 370: 'KEY_NUMERIC_POUND',
    582: 'KEY_VOICECOMMAND',
}

EVENT_TYPES = {
    0: 'EV_SYN', 1: 'EV_KEY', 2: 'EV_REL', 3: 'EV_ABS',
    4: 'EV_MSC', 17: 'EV_LED',
}

VALUE_NAMES = {0: 'UP', 1: 'DOWN', 2: 'REPEAT'}


# Multimedia-Keycodes, die als FB-Indikator gelten (Capability-Check identisch zum InputHandler)
REMOTE_KEY_CODES = [164, 163, 165, 115, 114]  # PLAYPAUSE, NEXT, PREV, VOL+, VOL-

# Bus-Typen identisch zum InputHandler: BT (0x05) und USB (0x03)
ALLOWED_BUS_TYPES = {0x05, 0x03}
BUS_NAMES = {0x01: 'PCI', 0x03: 'USB', 0x05: 'BT', 0x10: 'ISA', 0x11: 'PS/2',
             0x18: 'I2C', 0x19: 'HOST', 0x1e: 'VIRT', 0x00: 'NULL'}


def list_devices():
    """Iteriert ueber /dev/input/event* und zeigt Name + Bus + UNIQ + FB-Capability-Match.

    Liest die KEY-Bitmap aus /proc/bus/input/devices und prueft, wie viele der
    REMOTE_KEY_CODES jedes Device unterstuetzt. Devices mit Match >= 2 UND
    erlaubtem Bus gelten als Fernbedienungs-Kandidat (Logik wie im InputHandler).
    """
    devices = []
    current = {}
    with open('/proc/bus/input/devices', 'r') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line:
                if current:
                    devices.append(current)
                    current = {}
                continue
            if line.startswith('I:'):
                for tok in line.split():
                    if tok.startswith('Bus='):
                        current['bus'] = int(tok.split('=', 1)[1], 16)
            elif line.startswith('N: Name='):
                current['name'] = line.split('=', 1)[1].strip('"')
            elif line.startswith('U: Uniq='):
                current['uniq'] = line.split('=', 1)[1] or '-'
            elif line.startswith('H: Handlers='):
                handlers = line.split('=', 1)[1]
                for tok in handlers.split():
                    if tok.startswith('event'):
                        current['path'] = f'/dev/input/{tok}'
                        break
            elif line.startswith('B: KEY='):
                current['key_bitmap'] = line.split('=', 1)[1].strip()
        if current:
            devices.append(current)

    print(f'{"Path":18s} {"Mark":5s} {"Bus":5s} {"M":2s} {"Name":35s} UNIQ')
    print('-' * 95)
    for d in devices:
        if 'path' not in d:
            continue
        bitmap = d.get('key_bitmap', '')
        match = _count_remote_keys(bitmap)
        bus = d.get('bus', 0)
        bus_name = BUS_NAMES.get(bus, f'{bus:#06x}')
        is_fb = match >= 2 and bus in ALLOWED_BUS_TYPES
        marker = '[FB]' if is_fb else '    '
        print(f'{d["path"]:18s} {marker} {bus_name:5s} {match}  {d.get("name", "?")[:34]:35s} {d.get("uniq", "-")}')


def _count_remote_keys(bitmap_hex: str) -> int:
    """Zaehlt, wie viele REMOTE_KEY_CODES in der KEY-Bitmap aus /proc gesetzt sind.

    Die Bitmap ist als Folge von Hex-Words notiert, hoechste Bits zuerst.
    Beispiel: 'fff 0' bedeutet: Word0=0, Word1=fff -> Bits 32..43 gesetzt.
    """
    if not bitmap_hex:
        return 0
    words = bitmap_hex.split()
    bits = 0
    for i, w in enumerate(reversed(words)):
        bits |= int(w, 16) << (i * 64)
    return sum(1 for code in REMOTE_KEY_CODES if bits & (1 << code))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--list':
        list_devices()
        return

    device = sys.argv[1] if len(sys.argv) > 1 else '/dev/input/event3'
    fd = open(device, 'rb', buffering=0)
    log = open(LOGFILE, 'a', buffering=1)

    fcntl.ioctl(fd.fileno(), EVIOCGRAB, 1)

    banner = f'=== decode_remote {device} -> {LOGFILE} (EVIOCGRAB aktiv) {datetime.now():%Y-%m-%d %H:%M:%S} ==='
    print(banner, flush=True)
    log.write(banner + '\n')

    try:
        while True:
            data = fd.read(EVENT_SIZE)
            if len(data) < EVENT_SIZE:
                continue
            sec, usec, etype, code, value = struct.unpack(EVENT_FORMAT, data)

            if etype == 0:
                continue

            ts = datetime.fromtimestamp(sec + usec / 1e6).strftime('%H:%M:%S.%f')[:-3]
            type_name = EVENT_TYPES.get(etype, f'type={etype}')
            if etype == 1:
                key_name = KEY_NAMES.get(code, f'code={code}')
                v = VALUE_NAMES.get(value, str(value))
                line = f'{ts}  {type_name:6s}  {key_name:22s}  {v}'
            else:
                line = f'{ts}  {type_name:6s}  code={code:5d}  value={value}'

            print(line, flush=True)
            log.write(line + '\n')

    except KeyboardInterrupt:
        print('\n--- Beendet ---', flush=True)
    finally:
        try:
            fcntl.ioctl(fd.fileno(), EVIOCGRAB, 0)
        except OSError:
            pass
        fd.close()
        log.close()


if __name__ == '__main__':
    main()
