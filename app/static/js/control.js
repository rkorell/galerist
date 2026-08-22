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

class GaleristControl {
    constructor(frames) {
        this.ws = null;
        this.reconnectDelay = 2000;
        this.previewEl = document.getElementById('preview-image');
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
                this._updateFilmstrip(msg.strip);
                this._updatePreviewInfo(msg.metadata, msg.index, msg.total);
                break;
            case 'search_result':
                this._showSearchCount(msg.count);
                break;
            case 'search_state':
                this._applySearchState(msg.active, msg.count);
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

        // Helligkeit-Slider → Anzeige + live ans Display senden
        const bSlider = document.getElementById('setting-brightness');
        bSlider.addEventListener('input', () => {
            document.getElementById('brightness-display').textContent =
                bSlider.value + ' %';
            this._sendBrightness(parseInt(bSlider.value, 10));
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
            fetch(this._apiBase() + '/api/restart', { method: 'POST' })
                .then(() => { this._showStatus('Neustart läuft...', true); })
                .catch(() => { this._showStatus('Restart fehlgeschlagen'); });
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

        fetch(this._apiBase() + '/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(() => { this._showStatus('Gespeichert', true); })
        .catch(() => { this._showStatus('Fehler beim Speichern'); });
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
