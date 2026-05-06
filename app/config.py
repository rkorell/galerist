# (c) Dr. Ralf Korell
# Galerist — Konfigurationsverwaltung
# Modified: 2026-04-13, 19:30 - Erstellt

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')


class Config:
    """Konfiguration aus config.json laden, ändern und speichern."""

    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self._path = path
        self._data = {}
        self.load()

    def load(self):
        """config.json laden."""
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                self._data = json.load(f)
            logger.info("Konfiguration geladen: %s", self._path)
        except FileNotFoundError:
            logger.warning("config.json nicht gefunden: %s — verwende Defaults", self._path)
            self._data = {}

    def save(self):
        """Aktuelle Konfiguration in config.json speichern."""
        with open(self._path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, indent=4, ensure_ascii=False)
        logger.info("Konfiguration gespeichert: %s", self._path)

    def update(self, key: str, value):
        """Einzelnen Wert setzen und sofort speichern."""
        self._data[key] = value
        self.save()

    def update_many(self, updates: dict):
        """Mehrere Werte setzen und speichern."""
        self._data.update(updates)
        self.save()

    def __getattr__(self, name: str):
        if name.startswith('_'):
            raise AttributeError(name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Konfiguration hat kein Feld '{name}'")

    def to_dict(self) -> dict:
        """Gesamte Konfiguration als Dictionary."""
        return dict(self._data)
