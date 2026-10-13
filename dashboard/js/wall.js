/*
 * 06 Video wall: several cameras side by side, in the page or fullscreen.
 *
 * Replay tiles play the preset's original video and draw a box around each
 * student with an incident in the replay package: amber while the behaviour
 * builds up (behaviour start → incident), red once the incident is raised,
 * kept for a few seconds after its last occurrence.  The live tile shows the
 * analysis stream, which already carries the same clean boxes.  Seat ROIs are
 * never drawn here.  Every tile keeps its source's aspect ratio.
 */

const LAYOUTS = { 1: [1, 1], 4: [2, 2], 6: [3, 2], 9: [3, 3] };
const HOLD_AFTER_LAST_MS = 3000;
const STORE_KEY = 'vigil.wall';

const state = { layout: 4, sources: [], boxes: true, paused: false };
const tiles = [];
let sourceOptions = [];
const incidentCache = new Map();

const $ = (id) => document.getElementById(id);

function loadPrefs() {
    try {
        const saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
        if (LAYOUTS[saved.layout]) state.layout = saved.layout;
        if (Array.isArray(saved.sources)) state.sources = saved.sources;
        if (typeof saved.boxes === 'boolean') state.boxes = saved.boxes;
    } catch (e) { /* storage unavailable */ }
}

function savePrefs() {
    try {
        localStorage.setItem(STORE_KEY, JSON.stringify({ layout: state.layout, sources: state.sources, boxes: state.boxes }));
    } catch (e) { /* storage unavailable */ }
}

function prettyPattern(name) {
    return String(name || '').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

function seatShort(code) {
    const parts = String(code || '').split('-');
    return parts.length ? `S${parts[parts.length - 1]}` : '';
}

function formatTime(seconds) {
    if (!Number.isFinite(seconds)) return '';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
}

async function loadSources() {
    let presets = [];
    try {
        const res = await fetch('/api/v1/demo/presets');
        if (res.ok) presets = await res.json();
    } catch (e) { /* offline */ }
    sourceOptions = [
        ...presets.map((p) => ({ id: `preset:${p.name}`, label: `${p.title} · replay`, preset: p.name })),
        { id: 'live', label: 'Live analysis stream' },
        { id: '', label: '— empty —' },
    ];
    const defaults = [...presets.map((p) => `preset:${p.name}`), 'live'];
    for (let i = 0; i < 9; i += 1) {
        if (state.sources[i] === undefined || !sourceOptions.some((o) => o.id === state.sources[i])) {
            state.sources[i] = defaults.length ? defaults[i % defaults.length] : '';
        }
    }
}

async function incidentsFor(preset) {
    if (!incidentCache.has(preset)) {
        incidentCache.set(preset, fetch(`/api/v1/demo/presets/${encodeURIComponent(preset)}/incidents`)
            .then((res) => (res.ok ? res.json() : { incidents: [] }))
            .catch(() => ({ incidents: [] })));
    }
    return incidentCache.get(preset);
}

function setAspect(tile, width, height) {
    if (width > 0 && height > 0) tile.stage.style.setProperty('--ar', String(width / height));
}

function setStatus(tile, text, tone = '') {
    tile.status.textContent = text;
    tile.status.dataset.tone = tone;
}

function clearMedia(tile) {
    tile.video.pause();
    tile.video.removeAttribute('src');
    tile.video.load();
    tile.video.hidden = true;
    tile.img.removeAttribute('src');
    tile.img.hidden = true;
    tile.svg.replaceChildren();
    tile.incidents = [];
    tile.frameSize = null;
    tile.lastDrawKey = '';
    tile.time.textContent = '';
}

async function assignSource(tile, sourceId) {
    tile.source = sourceId;
    clearMedia(tile);
    tile.empty.hidden = Boolean(sourceId);
    if (!sourceId) {
        setStatus(tile, 'EMPTY');
        return;
    }
    if (sourceId === 'live') {
        tile.img.hidden = false;
        tile.img.src = `/api/v1/demo/stream?tile=${tile.index}&t=${Date.now()}`;
        setStatus(tile, 'LIVE');
        return;
    }
    const option = sourceOptions.find((o) => o.id === sourceId);
    const preset = option ? option.preset : sourceId.replace('preset:', '');
    tile.video.hidden = false;
    tile.video.src = `/api/v1/demo/presets/${encodeURIComponent(preset)}/source`;
    if (!state.paused) tile.video.play().catch(() => {});
    setStatus(tile, 'LOADING');
    const data = await incidentsFor(preset);
    if (tile.source !== sourceId) return;
    tile.incidents = data.incidents || [];
    tile.frameSize = data.frame_size;
    if (!data.available) setStatus(tile, 'NO REPLAY DATA', 'warning');
}

function buildTile(index) {
    const node = $('tileTemplate').content.firstElementChild.cloneNode(true);
    const tile = {
        index,
        el: node,
        select: node.querySelector('.wall-source'),
        status: node.querySelector('.wall-status'),
        time: node.querySelector('.wall-time'),
        stage: node.querySelector('.wall-stage'),
        video: node.querySelector('.wall-video'),
        img: node.querySelector('.wall-live'),
        svg: node.querySelector('.wall-boxes'),
        empty: node.querySelector('.wall-empty'),
        source: null,
        incidents: [],
        frameSize: null,
        lastDrawKey: '',
    };
    sourceOptions.forEach((o) => {
        const opt = document.createElement('option');
        opt.value = o.id;
        opt.textContent = o.label;
        tile.select.append(opt);
    });
    tile.select.value = state.sources[index] || '';
    tile.select.addEventListener('change', () => {
        state.sources[index] = tile.select.value;
        savePrefs();
        assignSource(tile, tile.select.value);
    });
    tile.video.addEventListener('loadedmetadata', () => setAspect(tile, tile.video.videoWidth, tile.video.videoHeight));
    tile.video.addEventListener('error', () => setStatus(tile, 'VIDEO ERROR', 'danger'));
    // A play() issued before the data arrived can be dropped; retry once it can play
    tile.video.addEventListener('canplay', () => {
        if (!state.paused && tile.video.paused) tile.video.play().catch(() => {});
    });
    tile.img.addEventListener('load', () => setAspect(tile, tile.img.naturalWidth, tile.img.naturalHeight));
    const fullscreen = () => toggleFullscreen(tile.el);
    node.querySelector('.wall-tile-fs').addEventListener('click', fullscreen);
    node.querySelector('.wall-tile-body').addEventListener('dblclick', fullscreen);
    return tile;
}

function renderWall() {
    const wall = $('wall');
    tiles.splice(0).forEach(clearMedia);
    const [cols, rows] = LAYOUTS[state.layout];
    wall.style.setProperty('--wall-cols', cols);
    wall.style.setProperty('--wall-rows', rows);
    wall.dataset.layout = String(state.layout);
    const nodes = [];
    for (let i = 0; i < state.layout; i += 1) {
        const tile = buildTile(i);
        tiles.push(tile);
        nodes.push(tile.el);
    }
    wall.replaceChildren(...nodes);
    tiles.forEach((tile) => assignSource(tile, state.sources[tile.index] || ''));
    document.querySelectorAll('#layoutSeg [data-layout]').forEach((btn) => {
        btn.setAttribute('aria-pressed', String(Number(btn.dataset.layout) === state.layout));
    });
}

// --- Suspect boxes ------------------------------------------------------------

function activeBoxes(incidents, nowMs) {
    // One box per seat; a raised incident outranks a building one
    const bySeat = new Map();
    incidents.forEach((inc) => {
        if (nowMs < inc.behavior_start_ms || nowMs > inc.last_seen_ms + HOLD_AFTER_LAST_MS) return;
        const flagged = nowMs >= inc.flagged_ms;
        const key = inc.seat_code || inc.event_id;
        const current = bySeat.get(key);
        if (!current || (flagged && !current.flagged) || (flagged === current.flagged && (inc.risk || 0) > (current.inc.risk || 0))) {
            bySeat.set(key, { inc, flagged });
        }
    });
    return [...bySeat.values()];
}

function drawBoxes(tile) {
    if (!tile.frameSize || tile.video.hidden) return;
    const nowMs = tile.video.currentTime * 1000;
    const boxes = state.boxes ? activeBoxes(tile.incidents, nowMs) : [];
    const flaggedCount = boxes.filter((b) => b.flagged).length;
    const suspectCount = boxes.length - flaggedCount;
    const stageWidth = tile.stage.clientWidth || 1;
    const key = `${boxes.map((b) => `${b.inc.event_id}:${b.flagged}`).join('|')}@${stageWidth}`;
    tile.time.textContent = `${formatTime(tile.video.currentTime)} / ${formatTime(tile.video.duration)}`;
    if (tile.status.textContent !== 'VIDEO ERROR') {
        if (flaggedCount) setStatus(tile, `${flaggedCount} FLAGGED${suspectCount ? ` · ${suspectCount} SUSPECT` : ''}`, 'danger');
        else if (suspectCount) setStatus(tile, `${suspectCount} SUSPECT`, 'warning');
        else setStatus(tile, tile.video.paused ? 'PAUSED' : 'REPLAY');
    }
    if (key === tile.lastDrawKey) return;
    tile.lastDrawKey = key;

    const [w, h] = tile.frameSize;
    const ns = 'http://www.w3.org/2000/svg';
    const el = (tag, attrs, text) => {
        const node = document.createElementNS(ns, tag);
        Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
        if (text) node.textContent = text;
        return node;
    };
    tile.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const unit = w / stageWidth; // frame pixels per screen pixel
    const fontSize = Math.max(10, Math.min(13, stageWidth / 60)) * unit;
    const children = [];
    boxes.forEach(({ inc, flagged }) => {
        const [x1, y1, x2, y2] = inc.bbox;
        children.push(el('rect', {
            x: x1, y: y1, width: x2 - x1, height: y2 - y1,
            class: `wall-box ${flagged ? 'wall-box--flagged' : 'wall-box--suspect'}`,
        }));
        const text = flagged
            ? `${seatShort(inc.seat_code)} ${prettyPattern(inc.pattern)} ${Math.round(inc.risk || 0)}`
            : `${seatShort(inc.seat_code)} suspect`;
        const padX = 4 * unit;
        const labelH = fontSize * 1.35;
        const labelW = text.length * fontSize * 0.62 + padX * 2;
        const top = y1 - labelH >= 0 ? y1 - labelH : y2;
        children.push(el('rect', { x: x1, y: top, width: labelW, height: labelH, class: 'wall-label-bg' }));
        children.push(el('text', { x: x1 + padX, y: top + labelH * 0.76, 'font-size': fontSize, class: 'wall-label' }, text));
    });
    tile.svg.replaceChildren(...children);
}

function frameLoop() {
    tiles.forEach(drawBoxes);
    requestAnimationFrame(frameLoop);
}

// --- Live tile status -----------------------------------------------------------

async function pollLive() {
    const liveTiles = tiles.filter((t) => t.source === 'live');
    if (!liveTiles.length) return;
    try {
        const res = await fetch('/api/v1/demo/status', { cache: 'no-store' });
        if (!res.ok) return;
        const status = await res.json();
        const running = status.state === 'RUNNING' || status.state === 'PAUSED';
        liveTiles.forEach((tile) => {
            if (running) {
                const flagged = status.active_review_incidents || 0;
                setStatus(tile, `LIVE · ${String(status.preset || '').toUpperCase()}${flagged ? ` · ${flagged} FLAGGED` : ''}`, flagged ? 'danger' : 'good');
            } else {
                setStatus(tile, 'NO ANALYSIS RUNNING');
            }
            tile.empty.hidden = running;
            tile.empty.textContent = running ? '' : 'Start an analysis on Live monitor';
            if (tile.img.naturalWidth) setAspect(tile, tile.img.naturalWidth, tile.img.naturalHeight);
        });
    } catch (e) { /* server restarting */ }
}

// --- Controls -------------------------------------------------------------------

// Native fullscreen when the browser allows it; otherwise (embedded views,
// kiosk frames) the element fills the window until Esc.
function maximize(element) {
    document.querySelectorAll('.is-maximized').forEach((node) => node.classList.remove('is-maximized'));
    if (element) element.classList.add('is-maximized');
    tiles.forEach((t) => { t.lastDrawKey = ''; });
}

function toggleFullscreen(element) {
    if (element.classList.contains('is-maximized')) {
        maximize(null);
        return;
    }
    if (document.fullscreenElement === element) {
        document.exitFullscreen().catch(() => {});
        return;
    }
    const enter = () => {
        if (!element.requestFullscreen) return maximize(element);
        // Some embedded browsers neither grant nor refuse the request
        const fallback = setTimeout(() => {
            if (document.fullscreenElement !== element) maximize(element);
        }, 700);
        return element.requestFullscreen()
            .then(() => clearTimeout(fallback))
            .catch(() => { clearTimeout(fallback); maximize(element); });
    };
    if (document.fullscreenElement) document.exitFullscreen().then(enter).catch(enter);
    else {
        maximize(null);
        enter();
    }
}

function setPaused(paused) {
    state.paused = paused;
    tiles.forEach((tile) => {
        if (tile.video.hidden) return;
        if (paused) tile.video.pause();
        else tile.video.play().catch(() => {});
    });
    $('playAllBtn').textContent = paused ? 'Play all' : 'Pause all';
}

document.addEventListener('DOMContentLoaded', async () => {
    loadPrefs();
    $('boxesToggle').checked = state.boxes;
    await loadSources();
    renderWall();

    document.querySelectorAll('#layoutSeg [data-layout]').forEach((btn) => {
        btn.addEventListener('click', () => {
            state.layout = Number(btn.dataset.layout);
            savePrefs();
            renderWall();
        });
    });
    $('boxesToggle').addEventListener('change', () => {
        state.boxes = $('boxesToggle').checked;
        savePrefs();
        tiles.forEach((t) => { t.lastDrawKey = ''; });
    });
    $('playAllBtn').addEventListener('click', () => setPaused(!state.paused));
    $('restartAllBtn').addEventListener('click', () => {
        tiles.forEach((tile) => { if (!tile.video.hidden) tile.video.currentTime = 0; });
        setPaused(false);
    });
    $('fullscreenBtn').addEventListener('click', () => toggleFullscreen($('wall')));
    document.addEventListener('keydown', (event) => {
        if (event.target.closest('select, input')) return;
        if (event.key === 'f' || event.key === 'F') toggleFullscreen($('wall'));
        if (event.key === 'Escape') maximize(null);
        if (event.key === ' ') { event.preventDefault(); setPaused(!state.paused); }
    });

    document.addEventListener('fullscreenchange', () => {
        if (document.fullscreenElement) maximize(null);
        tiles.forEach((t) => { t.lastDrawKey = ''; });
    });

    requestAnimationFrame(frameLoop);
    pollLive();
    setInterval(pollLive, 2000);
});
