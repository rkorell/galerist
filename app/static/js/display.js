// (c) Dr. Ralf Korell
// Galerist — Kiosk-Display: WebSocket-Client, Bildwechsel, Overlay
// Modified: 2026-04-13, 19:55 - Erstellt

class GaleristDisplay {
    constructor() {
        this.imageEl = document.getElementById('current-image');
        this.overlayEl = document.getElementById('overlay');
        this.ws = null;
        this.reconnectDelay = 2000;
        this.connect();
        this._initKeyboard();
    }

    connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${protocol}//${location.host}/ws`);

        this.ws.onopen = () => {
            console.log('WebSocket verbunden');
        };

        this.ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            this._handleMessage(msg);
        };

        this.ws.onclose = () => {
            console.log('WebSocket getrennt, Reconnect in', this.reconnectDelay, 'ms');
            setTimeout(() => this.connect(), this.reconnectDelay);
        };

        this.ws.onerror = () => {
            // onclose wird danach automatisch aufgerufen
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
                this._showImage(msg.src, msg.metadata, msg.index, msg.total);
                break;
            case 'show_overlay':
                this.overlayEl.classList.remove('hidden');
                break;
            case 'hide_overlay':
                this.overlayEl.classList.add('hidden');
                break;
            case 'preload':
                // Bild im Browser-Cache vorhalten
                new Image().src = msg.src;
                break;
        }
    }

    _showImage(src, metadata, index, total) {
        // Bild vorladen, dann sofort anzeigen (kein Übergang)
        const preload = new Image();
        preload.onload = () => {
            this.imageEl.src = src;
        };
        preload.onerror = () => {
            console.warn('Bild nicht ladbar:', src);
            this.sendAction('next');
        };
        preload.src = src;

        // Overlay-Inhalt aktualisieren (auch wenn versteckt)
        this._updateOverlay(metadata, index, total);
    }

    _updateOverlay(meta, index, total) {
        // Hilfsfunktion: Wikidata-URIs und Q-IDs filtern
        const clean = (s) => {
            if (!s) return '';
            if (s.startsWith('http')) return '';
            if (/^Q\d+$/.test(s)) return '';
            return s;
        };

        document.getElementById('overlay-artist').textContent =
            clean(meta.kuenstler);

        // Titel mit Jahr
        const titel = clean(meta.titel);
        const jahr = clean(meta.jahr);
        const titelParts = [];
        if (titel) titelParts.push(titel);
        if (jahr) titelParts.push(jahr);
        document.getElementById('overlay-title').textContent =
            titelParts.join(', ');

        // Mehrzeilige Details
        const lines = [];
        const material = clean(meta.material);
        const masse = meta.masse || '';
        const sammlung = clean(meta.sammlung);
        const standort = clean(meta.standort);
        const genre = clean(meta.genre);
        if (material) lines.push(material);
        if (masse) lines.push(masse);
        if (sammlung) lines.push(sammlung);
        if (standort && standort !== sammlung) lines.push(standort);
        // Genre bewusst weggelassen (immer "Landschaftsmalerei")
        document.getElementById('overlay-details').innerHTML =
            lines.join('<br>');

        document.getElementById('overlay-counter').textContent =
            (index && total) ? index + ' / ' + total : '';
    }

    // Tastatur-Steuerung (Entwicklung + ggf. USB-Keyboard)
    _initKeyboard() {
        document.addEventListener('keydown', (e) => {
            const keyMap = {
                'ArrowLeft':  'prev',
                'ArrowRight': 'next',
                'ArrowUp':    'info_on',
                'ArrowDown':  'info_off',
                ' ':          'playpause',
            };
            const action = keyMap[e.key];
            if (action) {
                e.preventDefault();
                this.sendAction(action);
            }
        });
    }
}

// Start
document.addEventListener('DOMContentLoaded', () => {
    new GaleristDisplay();
});
