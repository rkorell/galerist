# (c) Dr. Ralf Korell
# Galerist — Umgebungslicht-Abgriff (Senvolon via MQTT, abhaengigkeitsfrei)
# Modified: 2026-09-19 - Erstellt (Ambient-Helligkeitsregelung)
#
# Liest den retained STATE des Senvolon-Praesenzsensors vom MQTT-Broker und
# gibt den 'light'-Wert zurueck. Bewusst OHNE externe Bibliothek (kein paho-mqtt,
# kein mosquitto_sub) — nur die stdlib. Da der Sensor seinen STATE als retained
# publisht, genuegt ein Einmal-Connect pro Zyklus: connect -> subscribe ->
# retained PUBLISH lesen -> disconnect. Kein Keepalive, kein Dauerprozess.

import json
import logging
import socket
import struct

logger = logging.getLogger(__name__)


def _encode_remaining_length(n: int) -> bytes:
    """MQTT 'Remaining Length' als variable-length-Integer kodieren."""
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n > 0:
            byte |= 0x80
        out.append(byte)
        if n == 0:
            break
    return bytes(out)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Genau n Bytes lesen oder Fehler werfen."""
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('MQTT-Verbindung vom Broker geschlossen')
        buf += chunk
    return buf


def _read_remaining_length(sock: socket.socket) -> int:
    """MQTT 'Remaining Length'-Varint vom Socket lesen."""
    multiplier = 1
    value = 0
    while True:
        byte = _recv_exact(sock, 1)[0]
        value += (byte & 0x7F) * multiplier
        if (byte & 0x80) == 0:
            break
        multiplier *= 128
    return value


def _read_packet(sock: socket.socket) -> tuple[int, bytes]:
    """Ein MQTT-Paket lesen: (Fixed-Header-Byte, Body)."""
    header = _recv_exact(sock, 1)[0]
    length = _read_remaining_length(sock)
    body = _recv_exact(sock, length) if length else b''
    return header, body


def fetch_light(host: str, port: int, topic: str, field: str,
                client_id: str, timeout: float = 8.0) -> int | None:
    """Einmalige MQTT-Abfrage: retained STATE von 'topic' lesen und den Wert
    unter dem JSON-Schluessel 'field' zurueckgeben.

    Returns:
        int Helligkeitswert, oder None bei jedem Fehler (Netz, Broker, Payload).
        None heisst fuer den Aufrufer: Helligkeit unveraendert lassen.
    """
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)

        # CONNECT: Protokoll 'MQTT' Level 4 (3.1.1), clean session, keepalive 15s
        var_header = b'\x00\x04MQTT\x04\x02\x00\x0f'
        cid = client_id.encode('utf-8')
        payload = struct.pack('!H', len(cid)) + cid
        conn = var_header + payload
        sock.sendall(b'\x10' + _encode_remaining_length(len(conn)) + conn)

        # CONNACK erwarten (0x20), Return-Code pruefen
        htype, hbody = _read_packet(sock)
        if (htype & 0xF0) != 0x20 or len(hbody) < 2 or hbody[1] != 0:
            logger.warning("Ambient: CONNECT abgelehnt (Code %s)",
                           hbody[1] if len(hbody) >= 2 else '?')
            return None

        # SUBSCRIBE (Packet-ID 1, QoS 0)
        tb = topic.encode('utf-8')
        sub = struct.pack('!H', 1) + struct.pack('!H', len(tb)) + tb + b'\x00'
        sock.sendall(b'\x82' + _encode_remaining_length(len(sub)) + sub)

        # Pakete lesen, bis ein PUBLISH auf dem Topic kommt (retained kommt sofort)
        for _ in range(10):
            ptype, body = _read_packet(sock)
            if (ptype & 0xF0) != 0x30:  # nur PUBLISH interessiert (SUBACK etc. ignorieren)
                continue
            topic_len = struct.unpack('!H', body[:2])[0]
            idx = 2 + topic_len
            qos = (ptype & 0x06) >> 1
            if qos > 0:
                idx += 2  # Packet-Identifier ueberspringen
            data = json.loads(body[idx:].decode('utf-8'))
            value = data.get(field)
            if isinstance(value, (int, float)):
                return int(value)
            logger.warning("Ambient: Feld '%s' fehlt oder ungueltig im STATE", field)
            return None
        return None

    except Exception as exc:
        logger.warning("Ambient: MQTT-Abfrage fehlgeschlagen: %s", exc)
        return None
    finally:
        if sock is not None:
            try:
                sock.sendall(b'\xe0\x00')  # DISCONNECT
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
