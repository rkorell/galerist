// (c) Dr. Ralf Korell
// Galerist — Web-App: Steuerung, Filmstreifen, Einstellungen
// Modified: 2026-04-13, 20:00 - Erstellt
// Modified: 2026-04-13, 22:15 - Filmstreifen, Slider, Korell-Design
// Modified: 2026-04-17, 13:00 - Restart-Button

class GaleristControl {
    constructor() {
        this.ws = null;
        this.reconnectDelay = 2000;
        this.connect();
        this._initButtons();
        this._initSettings();
        this._initTheme();
        this._loadSettings();
    }

    // ── WebSocket ────────────────────────────────────

    connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${protocol}//${location.host}/ws`);

        this.ws.onopen = () => {
            this._showStatus('Verbunden', true);
        };

        this.ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            this._handleMessage(msg);
        };

        this.ws.onclose = () => {
            this._showStatus('Verbindung getrennt...');
            setTimeout(() => this.connect(), this.reconnectDelay);
        };
    }

    sendAction(action) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: action }));
        }
    }

    _handleMessage(msg) {
        switch (msg.type) {
            case 'show_image':
                this._updateFilmstrip(msg.strip);
                this._updatePreviewInfo(msg.metadata, msg.index, msg.total);
                break;
        }
    }

    // ── Filmstreifen ─────────────────────────────────

    _updateFilmstrip(strip) {
        if (!strip || strip.length === 0) return;
        const thumbs = document.querySelectorAll('.filmstrip-thumb');
        strip.forEach((item, i) => {
            if (thumbs[i]) {
                thumbs[i].src = item.thumb;
                thumbs[i].onerror = () => { thumbs[i].src = item.src; };
                thumbs[i].className = 'filmstrip-thumb' + (item.active ? ' active' : '');
            }
        });
    }

    _updatePreviewInfo(meta, index, total) {
        const clean = (s) => {
            if (!s) return '';
            if (s.startsWith('http')) return '';
            if (/^Q\d+$/.test(s)) return '';
            return s;
        };

        const info = document.getElementById('preview-info');
        const parts = [];
        const kuenstler = clean(meta.kuenstler);
        const titel = clean(meta.titel);
        const jahr = clean(meta.jahr);
        if (kuenstler) parts.push(kuenstler);
        if (titel) parts.push('\u201E' + titel + '\u201C');
        if (jahr) parts.push(jahr);
        const counter = (index && total) ? ' (' + index + '/' + total + ')' : '';
        info.textContent = parts.join(' \u2013 ') + counter;
    }

    // ── Buttons ──────────────────────────────────────

    _initButtons() {
        document.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.sendAction(btn.dataset.action);
            });
        });
    }

    // ── Theme ────────────────────────────────────────

    _initTheme() {
        if (localStorage.getItem('galerist-theme') === 'light') {
            document.documentElement.setAttribute('data-theme', 'light');
        }
        this._updateThemeBtn();

        document.getElementById('theme-toggle').addEventListener('click', () => {
            const isLight = document.documentElement.getAttribute('data-theme') === 'light';
            document.documentElement.setAttribute('data-theme', isLight ? '' : 'light');
            localStorage.setItem('galerist-theme', isLight ? 'dark' : 'light');
            this._updateThemeBtn();
        });
    }

    _updateThemeBtn() {
        const isLight = document.documentElement.getAttribute('data-theme') === 'light';
        document.getElementById('theme-toggle').textContent = isLight ? '\u{1F319}' : '\u{2600}\u{FE0F}';
    }

    // ── Einstellungen ────────────────────────────────

    _initSettings() {
        // Interval-Slider: Stunden + Minuten → Anzeige aktualisieren
        const hSlider = document.getElementById('setting-interval-h');
        const mSlider = document.getElementById('setting-interval-m');
        const updateIntervalDisplay = () => {
            const h = parseInt(hSlider.value);
            const m = parseInt(mSlider.value);
            const parts = [];
            if (h > 0) parts.push(h + ' Std');
            if (m > 0) parts.push(m + ' Min');
            document.getElementById('interval-display').textContent =
                parts.length > 0 ? parts.join(' ') : '0 Min';
        };
        hSlider.addEventListener('input', updateIntervalDisplay);
        mSlider.addEventListener('input', updateIntervalDisplay);

        // Overlay-Slider → Anzeige
        const oSlider = document.getElementById('setting-overlay-duration');
        oSlider.addEventListener('input', () => {
            document.getElementById('overlay-display').textContent =
                oSlider.value + ' Sek';
        });

        // Buttons
        document.getElementById('btn-save-settings').addEventListener('click', () => {
            this._saveSettings();
        });
        document.getElementById('btn-refresh-metadata').addEventListener('click', () => {
            this.sendAction('refresh_metadata');
            this._showStatus('Metadaten werden aktualisiert...', true);
        });

        document.getElementById('btn-restart').addEventListener('click', () => {
            if (!confirm('Service wirklich neu starten?')) return;
            fetch('/api/restart', { method: 'POST' })
                .then(() => { this._showStatus('Neustart läuft...', true); })
                .catch(() => { this._showStatus('Restart fehlgeschlagen'); });
        });
    }

    _loadSettings() {
        fetch('/api/settings')
            .then(r => r.json())
            .then(data => {
                // Intervall in Stunden + Minuten aufteilen
                const totalSec = data.display_interval_seconds;
                const h = Math.floor(totalSec / 3600);
                const m = Math.floor((totalSec % 3600) / 60);
                document.getElementById('setting-interval-h').value = h;
                document.getElementById('setting-interval-m').value = m;
                const parts = [];
                if (h > 0) parts.push(h + ' Std');
                if (m > 0) parts.push(m + ' Min');
                document.getElementById('interval-display').textContent =
                    parts.length > 0 ? parts.join(' ') : '0 Min';

                // Overlay
                document.getElementById('setting-overlay-duration').value =
                    data.overlay_duration_seconds;
                document.getElementById('overlay-display').textContent =
                    data.overlay_duration_seconds + ' Sek';

                // Zeiten
                document.getElementById('setting-on-time').value =
                    data.operating_hours.on_time;
                document.getElementById('setting-off-time').value =
                    data.operating_hours.off_time;
            })
            .catch(() => {
                this._showStatus('Einstellungen nicht ladbar');
            });
    }

    _saveSettings() {
        const h = parseInt(document.getElementById('setting-interval-h').value);
        const m = parseInt(document.getElementById('setting-interval-m').value);
        const totalSeconds = h * 3600 + m * 60;

        const payload = {
            display_interval_seconds: Math.max(totalSeconds, 10),
            overlay_duration_seconds: parseInt(
                document.getElementById('setting-overlay-duration').value),
            operating_hours: {
                on_time: document.getElementById('setting-on-time').value,
                off_time: document.getElementById('setting-off-time').value,
            }
        };

        fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(() => { this._showStatus('Gespeichert', true); })
        .catch(() => { this._showStatus('Fehler beim Speichern'); });
    }

    // ── Status ───────────────────────────────────────

    _showStatus(text, ok) {
        const el = document.getElementById('status-message');
        el.textContent = text;
        el.className = ok ? 'status ok' : 'status';
        setTimeout(() => {
            if (el.textContent === text) el.textContent = '';
        }, 3000);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new GaleristControl();
});
