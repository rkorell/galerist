// (c) Dr. Ralf Korell
// Galerist — Web-App: Steuerung, Filmstreifen, Einstellungen
// Modified: 2026-04-13, 20:00 - Erstellt
// Modified: 2026-04-13, 22:15 - Filmstreifen, Slider, Korell-Design
// Modified: 2026-04-17, 13:00 - Restart-Button
// Modified: 2026-08-01 - Anzeigegeraet-Umschalter (display_backend Monitor/Fernseher) laden + speichern
// Modified: 2026-08-02 - Grosses Vorschaubild, Helligkeits-Slider (live + speichern)
// Modified: 2026-08-20 - Counter zeigt stabile Katalognummer (meta.katalog_nr) statt Shuffle-Position
// Modified: 2026-08-20 - Originaltitel (meta.titel_original) in Klammern nach dem deutschen Titel
// Modified: 2026-08-21 - Such-Akkordeon: live Trefferzahl (WS 'search'), 'Treffer anzeigen'/'zuruecksetzen', Kuenstler-Datalist via /api/artists
// Modified: 2026-08-22 - Rahmen-Waehler (Galerist/TheFrame): WS + API auf gewaehlten Rahmen umlegen, Auswahl gemerkt (localStorage)
// Modified: 2026-08-22 - Rahmen-Liste aus config.json via /api/frames (keine IPs im Code), Buttons dynamisch erzeugt
// Modified: 2026-08-24 - Button "Service stoppen" (POST /api/stop)
// Modified: 2026-08-24 - Button "Service auf <anderem Rahmen> starten" (POST /api/start_frame, Relais ueber laufenden Rahmen)
// Modified: 2026-09-19 - Auto-Helligkeit: Toggle + min/max-Grenzen in Settings; schaltet manuellen Regler/Grenzen gegenseitig frei
// Modified: 2026-09-20 - Auto-Helligkeit: interaktiver Kurveneditor (3-Anker light->Helligkeit) mit Drag + Vorschau-Slider; loest min/max-Slider ab
// Modified: 2026-09-20 - Kurveneditor: 5 freie Werte (auch Schwellen), y 20-80, x-Wortachse, weisser Live-Punkt (WS ambient_light), Live-Schalter, Kalibriermodus treibt Schirm; Vorschau-Slider raus
// Modified: 2026-09-20 - Helligkeitsregler justiert rueckwaerts die Kennlinie (naechster Anker); Live-Sensorzahl in Erklaerung; Chips 1-zeilig (dunkel/Licht ab/Licht/Tag ab/Tag); default light_hi 60
// Modified: 2026-10-06 - Knopf "Bild aus der Galerie entfernen": setzt den Loeschmarker per POST auf remove.php (Adresse aus local_config.js), merkt sich Dateiname + Metadaten des gezeigten Bildes

class GaleristControl {
    constructor(frames) {
        this.ws = null;
        this.reconnectDelay = 2000;
        this.previewEl = document.getElementById('preview-image');
        // Aktuell gezeigtes Bild (Dateiname + Metadaten) — Grundlage fuer das Verwerfen
        this.currentFile = '';
        this.currentMeta = {};
        // Rahmen-Liste kommt aus config.json (via /api/frames). Die App steuert EINEN
        // Rahmen (nie beide zugleich). Fallback: nur der ausliefernde Rahmen.
        this.frames = (frames && frames.length)
            ? frames
            : [{ id: 'self', name: 'Rahmen', host: location.host }];
        this.frameId = this._resolveInitialFrame();
        this._initFrameSwitch();
        this.connect();
        this._initButtons();
        this._initSettings();
        this._initTheme();
        this._initSearch();
        this._initRemoveButton();
        this._loadSettings();
    }

    // ── WebSocket ────────────────────────────────────

    connect() {
        const f = this._frame();
        const ws = new WebSocket('ws://' + f.host + '/ws');
        this.ws = ws;

        ws.onopen = () => {
            this._showStatus('Verbunden: ' + f.name, true);
        };

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            this._handleMessage(msg);
        };

        ws.onclose = () => {
            if (this.ws !== ws) return;   // durch Rahmenwechsel ersetzt → nicht reconnecten
            this._showStatus('Verbindung getrennt...');
            setTimeout(() => { if (this.ws === ws) this.connect(); }, this.reconnectDelay);
        };
    }

    // ── Rahmen-Wähler ────────────────────────────────

    _frame() {
        return this.frames.find(f => f.id === this.frameId) || this.frames[0];
    }

    _apiBase() {
        return 'http://' + this._frame().host;
    }

    _resolveInitialFrame() {
        const saved = localStorage.getItem('galerist-frame');
        if (saved && this.frames.some(f => f.id === saved)) return saved;
        const here = this.frames.find(f => location.host === f.host);
        if (here) return here.id;
        return this.frames.length ? this.frames[0].id : 'self';
    }

    _initFrameSwitch() {
        const box = document.getElementById('frame-switch');
        if (!box) return;
        box.innerHTML = '';
        // Wähler nur zeigen, wenn mehr als ein Rahmen konfiguriert ist
        if (this.frames.length < 2) { box.style.display = 'none'; return; }
        box.style.display = '';
        this.frames.forEach(f => {
            const btn = document.createElement('button');
            btn.className = 'frame-btn';
            btn.dataset.frame = f.id;
            btn.textContent = f.name;
            btn.addEventListener('click', () => this._selectFrame(f.id));
            box.appendChild(btn);
        });
        this._updateFrameButtons();
    }

    _updateFrameButtons() {
        document.querySelectorAll('.frame-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.frame === this.frameId);
        });
        this._updateStartButton();
    }

    _otherFrame() {
        // Bei genau zwei Rahmen: der jeweils NICHT gewählte
        return this.frames.find(f => f.id !== this.frameId) || null;
    }

    _updateStartButton() {
        const btn = document.getElementById('btn-start-other');
        if (!btn) return;
        const other = this._otherFrame();
        if (this.frames.length < 2 || !other) { btn.style.display = 'none'; return; }
        btn.style.display = '';
        btn.textContent = 'Service auf ' + other.name + ' starten';
    }

    _selectFrame(id) {
        if (id === this.frameId || !this.frames.some(f => f.id === id)) return;
        this.frameId = id;
        localStorage.setItem('galerist-frame', id);
        this._updateFrameButtons();
        this._resetSearchUI();
        // WebSocket auf den neuen Rahmen umlegen (alten schließen, kein Reconnect des alten)
        const old = this.ws;
        this.ws = null;
        if (old) { try { old.close(); } catch (e) { /* egal */ } }
        this.connect();
        this._loadSettings();
    }

    sendAction(action) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: action }));
        }
    }

    _sendBrightness(value) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: 'set_brightness', value: value }));
        }
    }

    _handleMessage(msg) {
        switch (msg.type) {
            case 'show_image':
                if (msg.src && this.previewEl) this.previewEl.src = msg.src;
                // Dateiname aus '/images/<datei>' — Schluessel fuer das Verwerfen
                this.currentFile = msg.src
                    ? decodeURIComponent(msg.src.replace(/^\/images\//, '')) : '';
                this.currentMeta = msg.metadata || {};
                this._updateFilmstrip(msg.strip);
                this._updatePreviewInfo(msg.metadata, msg.index, msg.total);
                break;
            case 'search_result':
                this._showSearchCount(msg.count);
                break;
            case 'search_state':
                this._applySearchState(msg.active, msg.count);
                break;
            case 'ambient_light':
                this._setAmbientLight(msg.light);
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
        if (titel) {
            let t = '\u201E' + titel + '\u201C';
            const orig = clean(meta.titel_original);
            if (orig && orig.toLowerCase() !== titel.toLowerCase()) t += ' (' + orig + ')';
            parts.push(t);
        }
        if (jahr) parts.push(jahr);
        // Katalognummer (stabil, ins XMP gebrannt) statt Shuffle-Position; total = Bestand
        const katalogNr = meta.katalog_nr || '';
        const counter = (katalogNr && total) ? ' (' + katalogNr + '/' + total + ')' : '';
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
                oSlider.value === '0' ? 'aus (bleibt offen)' : oSlider.value + ' Sek';
        });

        // Helligkeit-Slider → Anzeige + live ans Display; im Kalibriermodus (Auto aus)
        // justiert er zusätzlich rückwärts die Kennlinie (Punkt bei aktueller Raumhelligkeit).
        const bSlider = document.getElementById('setting-brightness');
        bSlider.addEventListener('input', () => {
            const v = parseInt(bSlider.value, 10);
            document.getElementById('brightness-display').textContent = v + ' %';
            this._sendBrightness(v);
            if (!document.getElementById('setting-ambient-enabled').checked) {
                this._ambientReverse(v);
            }
        });

        // Auto-Helligkeit: Toggle wirkt LIVE (schaltet die Regelung am Dienst an/aus)
        // und schaltet manuellen Regler ⇄ Kalibrierung frei.
        document.getElementById('setting-ambient-enabled').addEventListener('change', (e) => {
            this._sendAmbientEnable(e.target.checked);
            this._applyAmbientUiState();
        });

        // Kurveneditor initialisieren (Kennlinie-Anker + Vorschau-Slider)
        this._initAmbientCurve();

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
            fetch(this._apiBase() + '/api/restart', { method: 'POST' })
                .then(() => { this._showStatus('Neustart läuft...', true); })
                .catch(() => { this._showStatus('Restart fehlgeschlagen'); });
        });

        document.getElementById('btn-stop').addEventListener('click', () => {
            if (!confirm('Service wirklich stoppen?')) return;
            fetch(this._apiBase() + '/api/stop', { method: 'POST' })
                .then(() => { this._showStatus('Service wird gestoppt...', true); })
                .catch(() => { this._showStatus('Stopp fehlgeschlagen'); });
        });

        document.getElementById('btn-start-other').addEventListener('click', () => {
            const other = this._otherFrame();
            if (!other) return;
            if (!confirm('Service auf ' + other.name + ' starten?')) return;
            // Relais: der aktuell gewaehlte (laufende) Rahmen startet den anderen per SSH
            fetch(this._apiBase() + '/api/start_frame', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ frame: other.id })
            })
            .then(() => { this._showStatus(other.name + ' wird gestartet...', true); })
            .catch(() => { this._showStatus('Start fehlgeschlagen'); });
        });
    }

    _loadSettings() {
        fetch(this._apiBase() + '/api/settings')
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
                    data.overlay_duration_seconds === 0 ? 'aus (bleibt offen)' : data.overlay_duration_seconds + ' Sek';

                // Zeiten
                document.getElementById('setting-on-time').value =
                    data.operating_hours.on_time;
                document.getElementById('setting-off-time').value =
                    data.operating_hours.off_time;

                // Anzeigegerät (display_backend)
                const backend = data.display_backend || 'wlr-randr';
                const backendRadio = document.querySelector(
                    'input[name="display-backend"][value="' + backend + '"]');
                if (backendRadio) backendRadio.checked = true;

                // Helligkeit
                const brightness = data.display_brightness || 100;
                document.getElementById('setting-brightness').value = brightness;
                document.getElementById('brightness-display').textContent =
                    brightness + ' %';

                // Auto-Helligkeit (Toggle + Kennlinie-Anker)
                document.getElementById('setting-ambient-enabled').checked =
                    data.ambient_brightness_enabled !== false;
                const c = this._curve;
                c.dark = data.ambient_dim_dark != null ? data.ambient_dim_dark : 35;
                c.T    = data.ambient_light_t   != null ? data.ambient_light_t   : 10;
                c.lit  = data.ambient_dim_lit   != null ? data.ambient_dim_lit   : 45;
                c.hi   = data.ambient_light_hi  != null ? data.ambient_light_hi  : 60;
                c.max  = data.ambient_dim_max   != null ? data.ambient_dim_max   : 68;
                this._renderAmbientCurve();
                this._applyAmbientUiState();
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
        const backendEl = document.querySelector('input[name="display-backend"]:checked');
        if (backendEl) payload.display_backend = backendEl.value;

        payload.display_brightness = parseInt(
            document.getElementById('setting-brightness').value, 10);

        payload.ambient_brightness_enabled =
            document.getElementById('setting-ambient-enabled').checked;
        const c = this._curve;
        payload.ambient_dim_dark = Math.round(c.dark);
        payload.ambient_light_t  = Math.round(c.T);
        payload.ambient_dim_lit  = Math.round(c.lit);
        payload.ambient_light_hi = Math.round(c.hi);
        payload.ambient_dim_max  = Math.round(c.max);

        fetch(this._apiBase() + '/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(() => { this._showStatus('Gespeichert', true); })
        .catch(() => { this._showStatus('Fehler beim Speichern'); });
    }

    _applyAmbientUiState() {
        // Auto AN: Automatik regelt → manueller Regler + Kalibrierung ruhen.
        // Auto AUS (= Kalibriermodus): beide bedienbar, nichts überschreibt.
        const on = document.getElementById('setting-ambient-enabled').checked;
        document.getElementById('brightness-field').classList.toggle('field-disabled', on);
        document.getElementById('setting-brightness').disabled = on;
        document.getElementById('ambient-cal').classList.toggle('field-disabled', on);
    }

    _sendAmbientEnable(on) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: 'ambient_enable', value: on }));
        }
    }

    // ── Auto-Helligkeit: Kurveneditor (Raumhelligkeit → Bildhelligkeit) ───

    _initAmbientCurve() {
        // 5 gespeicherte Werte: dark (Höhe bei Licht 0), T (Schwelle Zimmerlicht),
        // lit (Höhe bei T), hi (Schwelle voller Tag), max (Höhe bei hi).
        this._curve = { dark: 35, T: 10, lit: 45, hi: 93, max: 68 };
        this._liveLight = null;       // aktueller Sensorwert (weißer Punkt), vom Dienst per WS
        this._acDrag = null;
        const svg = document.getElementById('ambient-plot');
        this._acSvg = svg;

        const pt = (evt) => {
            const r = svg.getBoundingClientRect();
            return { x: (evt.clientX - r.left) / r.width * 380, y: (evt.clientY - r.top) / r.height * 264 };
        };
        svg.addEventListener('pointerdown', (e) => {
            const g = e.target.closest('.handle'); if (!g) return;
            this._acDrag = g.getAttribute('data-id'); svg.setPointerCapture(e.pointerId); e.preventDefault();
        });
        svg.addEventListener('pointermove', (e) => {
            if (!this._acDrag) return;
            const p = pt(e), c = this._curve;
            const b = Math.round(this._acInvB(p.y)), l = Math.round(this._acInvL(p.x));
            if (this._acDrag === 'dark') {
                c.dark = Math.max(20, Math.min(c.lit, b));              // fest links, nur hoch/runter
            } else if (this._acDrag === 'lit') {
                c.lit = Math.max(c.dark, Math.min(c.max, b));
                c.T = Math.max(1, Math.min(c.hi - 1, l));
            } else if (this._acDrag === 'max') {
                c.max = Math.max(c.lit, Math.min(80, b));
                c.hi = Math.max(c.T + 1, Math.min(100, l));
            }
            this._renderAmbientCurve();
            this._ambientPreviewToScreen();
        });
        const end = () => { this._acDrag = null; };
        svg.addEventListener('pointerup', end);
        svg.addEventListener('pointercancel', end);

        this._renderAmbientCurve();
    }

    _acLX(l) { return 44 + (l / 100) * (360 - 44); }
    _acLY(b) { return 18 + (80 - b) / (80 - 20) * (214 - 18); }
    _acInvL(px) { return Math.max(0, Math.min(100, (px - 44) / (360 - 44) * 100)); }
    _acInvB(py) { return Math.max(20, Math.min(80, 80 - (py - 18) / (214 - 18) * (80 - 20))); }

    _ambientBriFor(l) {
        const c = this._curve;
        if (l <= 0) return c.dark;
        if (l >= c.hi) return c.max;
        if (l <= c.T) return c.dark + (l / c.T) * (c.lit - c.dark);
        return c.lit + ((l - c.T) / (c.hi - c.T)) * (c.max - c.lit);
    }

    // Kalibriermodus (Automatik aus): Schirm live auf Kurve(aktuelles Licht) setzen
    // und den Helligkeitsregler nachziehen (nach direktem Punkt-Ziehen).
    _ambientPreviewToScreen() {
        if (this._liveLight == null) return;
        if (document.getElementById('setting-ambient-enabled').checked) return;
        const b = Math.round(this._ambientBriFor(this._liveLight));
        this._sendBrightness(b);
        document.getElementById('setting-brightness').value = b;
        document.getElementById('brightness-display').textContent = b + ' %';
    }

    // Rückwärts: gewünschte Helligkeit B bei der aktuellen Raumhelligkeit → passende
    // Konstante (nächster Anker, vertikal) so berechnen, dass Kurve(aktuelles Licht) = B.
    _ambientReverse(B) {
        if (this._liveLight == null) return;
        const c = this._curve, l = this._liveLight;
        const cl = v => Math.max(20, Math.min(80, v));
        if (l <= 0) { c.dark = cl(B); }
        else if (l >= c.hi) { c.max = cl(B); }
        else if (l <= c.T) {
            const f = l / c.T;
            if (l <= c.T / 2) c.dark = cl((B - c.lit * f) / ((1 - f) || 1));
            else c.lit = cl((B - c.dark * (1 - f)) / (f || 1));
        } else {
            const g = (l - c.T) / ((c.hi - c.T) || 1), mid = (c.T + c.hi) / 2;
            if (l <= mid) c.lit = cl((B - c.max * g) / ((1 - g) || 1));
            else c.max = cl((B - c.lit * (1 - g)) / (g || 1));
        }
        this._renderAmbientCurve();
    }

    // Aktueller Sensorwert vom Dienst → weißer Punkt + Anzeige nachführen.
    _setAmbientLight(light) {
        this._liveLight = light;
        if (this._acSvg) this._renderAmbientCurve();
    }

    _renderAmbientCurve() {
        const NS = 'http://www.w3.org/2000/svg';
        const svg = this._acSvg, c = this._curve;
        const mk = (tag, attrs, text) => {
            const e = document.createElementNS(NS, tag);
            for (const k in attrs) e.setAttribute(k, attrs[k]);
            if (text != null) e.textContent = text;
            return e;
        };
        svg.innerHTML = '';
        // Gitter + y-Beschriftung (Bildhelligkeit %, 20–80)
        for (let b = 20; b <= 80; b += 10) {
            svg.appendChild(mk('line', { class: 'grid', x1: this._acLX(0), y1: this._acLY(b), x2: this._acLX(100), y2: this._acLY(b) }));
            svg.appendChild(mk('text', { class: 'axis-lab', x: this._acLX(0) - 6, y: this._acLY(b) + 3, 'text-anchor': 'end' }, b));
        }
        for (let l = 0; l <= 100; l += 25) {
            svg.appendChild(mk('line', { class: 'grid', x1: this._acLX(l), y1: this._acLY(80), x2: this._acLX(l), y2: this._acLY(20) }));
        }
        // Achsentitel: y = Bild %, x = Raumhelligkeit (Worte, keine Zahlen)
        svg.appendChild(mk('text', { class: 'axis-lab', x: this._acLX(0) - 6, y: this._acLY(80) - 6, 'text-anchor': 'end' }, 'Bild %'));
        svg.appendChild(mk('text', { class: 'axis-title', x: this._acLX(0), y: 258, 'text-anchor': 'start' }, 'dunkel'));
        svg.appendChild(mk('text', { class: 'axis-title', x: this._acLX(50), y: 258, 'text-anchor': 'middle' }, 'Raumhelligkeit'));
        svg.appendChild(mk('text', { class: 'axis-title', x: this._acLX(100), y: 258, 'text-anchor': 'end' }, 'hell'));

        // Kennlinie
        const pts = [[0, c.dark], [c.T, c.lit], [c.hi, c.max], [100, c.max]]
            .map(p => `${this._acLX(p[0])},${this._acLY(p[1])}`).join(' ');
        svg.appendChild(mk('polyline', { class: 'curve', points: pts }));

        // Weißer Live-Punkt „du bist hier"
        if (this._liveLight != null) {
            const l = Math.max(0, Math.min(100, this._liveLight));
            const b = this._ambientBriFor(l);
            svg.appendChild(mk('line', { class: 'nowline', x1: this._acLX(l), y1: this._acLY(80), x2: this._acLX(l), y2: this._acLY(20) }));
            svg.appendChild(mk('circle', { class: 'nowdot', cx: this._acLX(l), cy: this._acLY(b), r: 5 }));
        }

        // Anker-Handles
        const anchors = [
            { id: 'dark', l: 0, b: c.dark, col: 'var(--dark)' },
            { id: 'lit', l: c.T, b: c.lit, col: 'var(--lit)' },
            { id: 'max', l: c.hi, b: c.max, col: 'var(--max)' },
        ];
        for (const a of anchors) {
            const g = mk('g', { class: 'handle', 'data-id': a.id });
            g.appendChild(mk('circle', { class: 'hdot', cx: this._acLX(a.l), cy: this._acLY(a.b), r: 8, fill: a.col }));
            g.appendChild(mk('text', { class: 'hlab', x: this._acLX(a.l), y: this._acLY(a.b) - 13, 'text-anchor': 'middle', fill: a.col }, Math.round(a.b)));
            svg.appendChild(g);
        }

        // Chips (Höhen + Schwellen) + Live-Sensorwert
        document.getElementById('ac-dark').textContent = Math.round(c.dark);
        document.getElementById('ac-lit').textContent = Math.round(c.lit);
        document.getElementById('ac-max').textContent = Math.round(c.max);
        document.getElementById('ac-t').textContent = Math.round(c.T);
        document.getElementById('ac-hi').textContent = Math.round(c.hi);
        document.getElementById('ac-light').textContent =
            this._liveLight != null ? this._liveLight : '–';
    }

    // ── Suche ─────────────────────────────────────────

    _initSearch() {
        this._searchTimer = null;
        this._artistsLoaded = false;
        const card = document.getElementById('search-card');
        const artistInput = document.getElementById('search-artist');
        const wordInput = document.getElementById('search-word');
        const showBtn = document.getElementById('btn-search-show');
        const resetBtn = document.getElementById('btn-search-reset');

        // Künstlerliste beim Aufklappen laden (gehört zum aktiven Rahmen)
        card.addEventListener('toggle', () => {
            if (card.open) this._loadArtists();
        });

        // Tippen → entprellte Trefferzahl-Abfrage (ändert den Rahmen nicht)
        const onType = () => {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => this._sendSearch(), 250);
        };
        artistInput.addEventListener('input', onType);
        wordInput.addEventListener('input', onType);

        showBtn.addEventListener('click', () => {
            this.sendAction('search_show');
        });
        resetBtn.addEventListener('click', () => {
            artistInput.value = '';
            wordInput.value = '';
            document.getElementById('search-count').innerHTML = '&nbsp;';
            showBtn.disabled = true;
            this.sendAction('search_reset');
        });
    }

    _sendSearch() {
        const kuenstler = document.getElementById('search-artist').value.trim();
        const wort = document.getElementById('search-word').value.trim();
        const countEl = document.getElementById('search-count');
        const showBtn = document.getElementById('btn-search-show');
        // Beide Felder leer → neutral: keine Abfrage, Rahmen unberührt
        if (!kuenstler && !wort) {
            countEl.innerHTML = '&nbsp;';
            showBtn.disabled = true;
            return;
        }
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: 'search', kuenstler: kuenstler, wort: wort }));
        }
    }

    _showSearchCount(count) {
        const countEl = document.getElementById('search-count');
        const showBtn = document.getElementById('btn-search-show');
        if (count > 0) {
            countEl.textContent = count + ' Treffer';
            showBtn.disabled = false;
        } else {
            countEl.textContent = 'keine Treffer';
            showBtn.disabled = true;
        }
    }

    _applySearchState(active, count) {
        // Suchmodus im Akkordeon-Titel spiegeln (auch nach Reconnect konsistent)
        const summary = document.querySelector('#search-card .settings-header');
        if (summary) summary.textContent = active ? ('Suche · aktiv (' + count + ')') : 'Suche';
        if (active) document.getElementById('search-card').open = true;
    }

    _loadArtists() {
        if (this._artistsLoaded) return;
        this._artistsLoaded = true;
        fetch(this._apiBase() + '/api/artists')
            .then(r => r.json())
            .then(list => {
                const dl = document.getElementById('artist-list');
                dl.innerHTML = '';
                list.forEach(name => {
                    const opt = document.createElement('option');
                    opt.value = name;
                    dl.appendChild(opt);
                });
            })
            .catch(() => {});
    }

    _resetSearchUI() {
        const a = document.getElementById('search-artist');
        const w = document.getElementById('search-word');
        if (a) a.value = '';
        if (w) w.value = '';
        const c = document.getElementById('search-count');
        if (c) c.innerHTML = '&nbsp;';
        const showBtn = document.getElementById('btn-search-show');
        if (showBtn) showBtn.disabled = true;
        const summary = document.querySelector('#search-card .settings-header');
        if (summary) summary.textContent = 'Suche';
        // Künstlerliste gehört zum Rahmen → zum Neuladen markieren
        this._artistsLoaded = false;
        const dl = document.getElementById('artist-list');
        if (dl) dl.innerHTML = '';
        const card = document.getElementById('search-card');
        if (card && card.open) this._loadArtists();
    }

    // ── Bild verwerfen ───────────────────────────────

    _initRemoveButton() {
        const btn = document.getElementById('btn-remove-image');
        if (!btn) return;
        // Ohne konfigurierte Endpunkt-Adresse (static/js/local_config.js) bleibt der
        // Knopf verborgen — besser kein Knopf als einer, der ins Leere greift.
        const url = window.GALERIST_ARCHIVE_URL;
        if (!url) return;
        btn.style.display = '';

        btn.addEventListener('click', () => {
            if (!this.currentFile) { this._showStatus('Kein Bild geladen'); return; }

            const m = this.currentMeta || {};
            const titel = m.titel ? '„' + m.titel + '“' : this.currentFile;
            const wer = m.kuenstler ? ' (' + m.kuenstler + ')' : '';
            if (!confirm(titel + wer + ' endgueltig aus beiden Rahmen und dem Archiv '
                         + 'entfernen?\n\nDas Bild wird heute Nacht geloescht.')) return;

            btn.disabled = true;
            fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dateiname: this.currentFile })
            })
                .then(r => r.json().then(d => ({ ok: r.ok, d: d })))
                .then(res => {
                    if (res.ok && res.d.status === 'markiert') {
                        this._showStatus('Markiert — wird heute Nacht entfernt', true);
                    } else if (res.ok && res.d.status === 'schon_markiert') {
                        this._showStatus('War schon markiert', true);
                    } else {
                        this._showStatus(res.d.meldung || 'Markieren fehlgeschlagen');
                    }
                })
                .catch(() => { this._showStatus('Archiv nicht erreichbar'); })
                .finally(() => { btn.disabled = false; });
        });
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
    fetch('/api/frames')
        .then(r => r.json())
        .then(frames => new GaleristControl(frames))
        .catch(() => new GaleristControl([]));
});
