# (c) Dr. Ralf Korell
# Galerist — Metadaten-Cache (IPTC/XMP aus JPEG-Dateien)
# Modified: 2026-04-13, 19:35 - Erstellt
# Modified: 2026-04-13, 22:00 - Erweiterte Metadaten
# Modified: 2026-04-14, 22:30 - Umstellung auf IPTC/XMP statt DB

import json
import logging
import os
import xml.etree.ElementTree as ET

from PIL import Image

logger = logging.getLogger(__name__)

# XMP Namespaces
_NS = {
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'photoshop': 'http://ns.adobe.com/photoshop/1.0/',
}


class MetadataCache:
    """Lädt Bild-Metadaten aus IPTC/XMP der JPEG-Dateien und cached sie als JSON."""

    def __init__(self, config):
        self.config = config
        self.metadata: dict[str, dict] = {}

    def refresh_from_files(self) -> int:
        """Metadaten aus den JPEG-Dateien lesen (XMP).

        Returns:
            Anzahl der gelesenen Bilder.
        """
        image_dir = self.config.image_directory
        logger.info("Lese Metadaten aus JPEG-Dateien (%s) ...", image_dir)

        if not os.path.isdir(image_dir):
            logger.warning("Bildverzeichnis nicht gefunden: %s", image_dir)
            return 0

        self.metadata.clear()
        count = 0

        for filename in sorted(os.listdir(image_dir)):
            if not filename.lower().endswith('.jpg'):
                continue
            if filename.endswith('.thumb'):
                continue

            path = os.path.join(image_dir, filename)
            meta = self._read_xmp(path)
            self.metadata[filename] = meta
            count += 1

        logger.info("Metadaten gelesen: %d Bilder", count)
        self._save_cache()
        return count

    def load_from_cache(self) -> bool:
        """Lokalen JSON-Cache laden.

        Returns:
            True wenn erfolgreich, False wenn Datei fehlt oder defekt.
        """
        cache_file = self.config.metadata_cache_file
        if not os.path.exists(cache_file):
            return False

        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)
            logger.info("Metadaten-Cache geladen: %d Einträge", len(self.metadata))
            return True
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Cache-Datei defekt: %s", e)
            return False

    def get_metadata(self, filename: str) -> dict | None:
        """Metadaten für einen Dateinamen abrufen."""
        return self.metadata.get(filename)

    def get_image_list(self) -> list[str]:
        """Liste aller Dateinamen mit Metadaten."""
        return list(self.metadata.keys())

    def _read_xmp(self, path: str) -> dict:
        """XMP-Metadaten aus einer JPEG-Datei lesen."""
        try:
            img = Image.open(path)
            xmp_xml = None
            for segment, content in img.applist:
                if segment == 'APP1' and b'adobe' in content.lower():
                    idx = content.find(b'<x:xmpmeta')
                    if idx >= 0:
                        end = content.find(b'</x:xmpmeta>')
                        if end >= 0:
                            xmp_xml = content[idx:end + 13].decode('utf-8', errors='replace')
                    break
            img.close()

            if not xmp_xml:
                return self._empty_meta(path)

            root = ET.fromstring(xmp_xml)

            # dc:creator, dc:title, dc:description aus erstem oder zweitem Description-Block
            kuenstler = ''
            titel = ''
            description = ''
            jahr = ''
            sammlung = ''
            standort = ''

            for desc in root.findall('.//rdf:Description', _NS):
                # dc:creator
                creator = desc.find('.//dc:creator//rdf:li', _NS)
                if creator is not None and creator.text:
                    kuenstler = creator.text

                # dc:title
                title = desc.find('.//dc:title//rdf:li', _NS)
                if title is not None and title.text:
                    titel = title.text

                # dc:description
                desc_el = desc.find('.//dc:description//rdf:li', _NS)
                if desc_el is not None and desc_el.text:
                    description = desc_el.text

                # photoshop:DateCreated, Source, City (als Attribute)
                date = desc.get('{http://ns.adobe.com/photoshop/1.0/}DateCreated')
                if date:
                    jahr = date

                source = desc.get('{http://ns.adobe.com/photoshop/1.0/}Source')
                if source:
                    sammlung = source

                city = desc.get('{http://ns.adobe.com/photoshop/1.0/}City')
                if city:
                    standort = city

                # Auch als Unterelemente prüfen
                for child in desc:
                    tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                    if tag == 'DateCreated' and child.text:
                        jahr = child.text
                    elif tag == 'Source' and child.text:
                        sammlung = child.text
                    elif tag == 'City' and child.text:
                        standort = child.text

            # Material und Maße aus Description extrahieren
            material = ''
            masse = ''
            if description:
                parts = [p.strip() for p in description.split(',')]
                for p in parts:
                    if '×' in p or ' x ' in p.lower():
                        masse = p
                    elif p and not masse:
                        material = p

            return {
                'kuenstler': kuenstler,
                'titel': titel,
                'jahr': jahr,
                'material': material,
                'sammlung': sammlung,
                'standort': standort,
                'masse': masse,
                'genre': '',
                'stil': '',
                'datum': '',
                'wikidata_id': '',
            }

        except Exception as e:
            logger.debug("XMP-Lesefehler %s: %s", os.path.basename(path), e)
            return self._empty_meta(path)

    def _empty_meta(self, path: str) -> dict:
        """Leere Metadaten mit Notfall-Titel aus Dateiname."""
        return {
            'kuenstler': '',
            'titel': self._titel_aus_dateiname(os.path.basename(path)),
            'jahr': '',
            'material': '',
            'sammlung': '',
            'standort': '',
            'masse': '',
            'genre': '',
            'stil': '',
            'datum': '',
            'wikidata_id': '',
        }

    def _save_cache(self):
        """Metadaten als JSON speichern."""
        cache_file = self.config.metadata_cache_file
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)
        logger.info("Metadaten-Cache gespeichert: %s (%d Einträge)",
                     cache_file, len(self.metadata))

    @staticmethod
    def _titel_aus_dateiname(filename: str) -> str:
        """Notfall-Titel aus Dateiname generieren."""
        name = filename.replace('.jpg', '')
        result = []
        for i, ch in enumerate(name):
            if ch.isupper() and i > 0 and name[i - 1].islower():
                result.append(' ')
            result.append(ch)
        return ''.join(result)
