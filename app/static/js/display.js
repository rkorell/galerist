// (c) Dr. Ralf Korell
// Galerist — Kiosk-Display: WebSocket-Client, Bildwechsel, Overlay
// Modified: 2026-04-13, 19:55 - Erstellt
// Modified: 2026-08-02 - Dimm-Overlay: set_brightness setzt #dimmer-Opacity live
// Modified: 2026-08-02 - Slider perzeptuell (CIE-L*/Weber-Fechner): Leuchtdichte = (v/100)^3
// Modified: 2026-08-02 - Exponent gamma-korrigiert auf 1.4 (Overlay mischt in sRGB, ^2.2 steckt schon drin)
// Modified: 2026-08-02 - battery_warning: Warnbanner bei schwachem FB-Akku
// Modified: 2026-08-02 - blackout: Voll-Schwarz-Overlay fuer den TV-Keep-shallow-Puls
// Modified: 2026-08-02 - Akku-Warnung als roter Header in der Infobox (gepinnt bis Quittung/Zuklappen)

class GaleristDisplay {
    constructor() {
        this.imageEl = document.getElementById('current-image');
        this.overlayEl = document.getElementById('overlay');
        this.dimmerEl = document.getElementById('dimmer');
        this.batteryHeaderEl = document.getElementById('overlay-battery');
        this._lastBrightness = 100;
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
                this.batteryHeaderEl.classList.add('hidden');  // Zuklappen = Warnung quittiert
                break;
            case 'preload':
                // Bild im Browser-Cache vorhalten
                new Image().src = msg.src;
                break;
            case 'set_brightness':
                this._setBrightness(msg.value);
                break;
            case 'battery_warning':
                this._showBatteryWarning(msg.percent);
                break;
            case 'blackout':
                this._setBlackout(msg.on);
                break;
        }
    }

    _setBlackout(on) {
        if (on) {
            this.dimmerEl.style.opacity = '1';           // voll schwarz
        } else {
            this._setBrightness(this._lastBrightness);   // zurueck auf zuletzt gesetzte Helligkeit
        }
    }

    _showBatteryWarning(percent) {
        document.getElementById('battery-pct').textContent = percent;
        this.batteryHeaderEl.classList.remove('hidden');   // roter Header oben in der Infobox
        this.overlayEl.classList.remove('hidden');         // Infobox aufklappen (gepinnt bis Quittung)
    }

    _setBrightness(value) {
        // Slider = empfundene Helligkeit. Wahrnehmung ~ Kubikwurzel der Leuchtdichte
        // (CIE-L*/Weber-Fechner); das Overlay mischt aber in sRGB, wodurch die Opacity
        // bereits mit ~2.2 wirkt. Netto ergibt Exponent 3/2.2 ≈ 1.4 eine perzeptuell
        // lineare Skala — jeder Prozent fühlt sich gleich stark an.
        const v = Math.max(20, Math.min(100, parseInt(value, 10) || 100));
        this._lastBrightness = v;
        this.dimmerEl.style.opacity = (1 - Math.pow(v / 100, 1.4)).toFixed(3);
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
