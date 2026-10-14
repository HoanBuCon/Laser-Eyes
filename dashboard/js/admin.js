/*
 * 07 System: storage overview and maintenance actions for demo and testing.
 * Every action asks for the server's typed confirmation phrase; the server
 * re-checks it, refuses while an analysis runs, backs the database up and
 * writes the audit log.
 */

const API = '/api/v1/admin';
const ACTIONS = {
    'test-rooms': { title: 'Remove test rooms', url: `${API}/cleanup/test-rooms` },
    'orphan-runs': { title: 'Purge unused run folders', url: `${API}/cleanup/orphan-runs` },
    'playback-cache': { title: 'Clear playback cache', url: `${API}/cleanup/playback-cache` },
    'reset-incidents': { title: 'Reset all sessions and incidents', url: `${API}/reset/incidents` },
};
let overview = null;
let pendingAction = null;

const $ = (id) => document.getElementById(id);

function formatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let value = n / 1024;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
}

function currentActor() {
    try { return localStorage.getItem('vigil.reviewer') || ''; } catch (e) { return ''; }
}

function log(text, tone = '') {
    const box = $('activityLog');
    if (box.querySelector('.vg-empty')) box.replaceChildren();
    const row = document.createElement('div');
    row.className = 'admin-log-row';
    row.dataset.tone = tone;
    const time = document.createElement('span');
    time.textContent = new Date().toLocaleTimeString();
    const msg = document.createElement('span');
    msg.textContent = text;
    row.append(time, msg);
    box.prepend(row);
}

function describe(action) {
    if (!overview) return '';
    const c = overview.cleanup;
    const t = overview.tables;
    switch (action) {
        case 'test-rooms':
            return `${c.test_rooms.rooms} rooms, ${c.test_rooms.sessions} sessions, ${c.test_rooms.incidents} incidents will be deleted.`;
        case 'orphan-runs':
            return `${c.orphan_runs.runs} run folders (${formatBytes(c.orphan_runs.bytes)}) will be deleted from disk.`;
        case 'playback-cache':
            return `${overview.storage.playback_cache.files} cached files (${formatBytes(overview.storage.playback_cache.bytes)}) will be deleted.`;
        case 'reset-incidents':
            return `${t.sessions} sessions, ${t.incidents} incidents, ${t.reviews} review decisions and ${t.bookmarks} bookmarks will be deleted.`;
        default:
            return '';
    }
}

function render(data) {
    overview = data;
    const s = data.storage;
    $('kDb').textContent = data.database ? formatBytes(data.database.bytes) : '—';
    $('kDbHint').textContent = data.database ? data.database.path : 'not SQLite';
    $('kRuns').textContent = formatBytes(s.demo_runs.bytes);
    $('kRunsHint').textContent = `${s.demo_runs.runs} runs · ${data.cleanup.orphan_runs.runs} unused`;
    $('kReplay').textContent = formatBytes(s.replay_packages.bytes);
    $('kCache').textContent = formatBytes(s.playback_cache.bytes);
    $('kCacheHint').textContent = `${s.playback_cache.files} files`;

    const chip = $('runtimeChip');
    chip.textContent = data.runtime.busy ? `ANALYSIS ${data.runtime.state}` : 'RUNTIME IDLE';
    chip.dataset.tone = data.runtime.busy ? 'warning' : 'good';
    chip.title = data.runtime.busy ? 'Maintenance is disabled while an analysis runs' : '';

    $('tableCounts').replaceChildren(...Object.entries(data.tables).map(([name, count]) => {
        const row = document.createElement('tr');
        const label = document.createElement('td');
        label.textContent = name.replace(/_/g, ' ');
        const value = document.createElement('td');
        value.className = 'num strong';
        value.textContent = count;
        row.append(label, value);
        return row;
    }));

    const tr = data.cleanup.test_rooms;
    $('previewTestRooms').textContent = tr.rooms
        ? `${tr.rooms} rooms · e.g. ${tr.examples.slice(0, 3).join(', ')}`
        : 'Nothing to remove.';
    const orphan = data.cleanup.orphan_runs;
    $('previewOrphanRuns').textContent = orphan.runs ? `${orphan.runs} folders · ${formatBytes(orphan.bytes)}` : 'Nothing to purge.';
    $('previewCache').textContent = `${s.playback_cache.files} files · ${formatBytes(s.playback_cache.bytes)}`;
    $('previewReset').textContent = describe('reset-incidents');

    document.querySelectorAll('.admin-action button').forEach((btn) => { btn.disabled = data.runtime.busy; });
}

async function loadOverview() {
    try {
        const res = await fetch(`${API}/overview`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        render(await res.json());
    } catch (err) {
        log(`Could not load the overview: ${err.message}`, 'danger');
    }
}

function openConfirm(action) {
    if (!overview) return;
    pendingAction = action;
    const phrase = overview.confirm_phrases[action];
    $('confirmTitle').textContent = ACTIONS[action].title;
    $('confirmText').textContent = describe(action);
    $('confirmPhrase').textContent = phrase;
    $('confirmInput').value = '';
    $('confirmError').hidden = true;
    $('confirmRun').disabled = true;
    $('confirmDialog').showModal();
    $('confirmInput').focus();
}

async function runPending(event) {
    event.preventDefault();
    const action = pendingAction;
    if (!action) return;
    const run = $('confirmRun');
    run.disabled = true;
    run.textContent = 'Working…';
    try {
        const res = await fetch(ACTIONS[action].url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: $('confirmInput').value, actor: $('actorInput').value.trim() || null }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
        $('confirmDialog').close();
        const parts = Object.entries(body)
            .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${k === 'bytes' ? formatBytes(v) : v}`);
        log(`${ACTIONS[action].title} — ${parts.join(' · ')}`, 'good');
        await loadOverview();
    } catch (err) {
        $('confirmError').textContent = err.message;
        $('confirmError').hidden = false;
        log(`${ACTIONS[action].title} failed: ${err.message}`, 'danger');
    } finally {
        run.textContent = 'Run';
        run.disabled = false;
    }
}

// --- VIGIL One computers ----------------------------------------------------------

function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function when(iso) {
    return iso ? new Date(iso + 'Z').toLocaleString() : '—';
}

async function loadDevices() {
    const body = document.getElementById('oneDevices');
    const res = await fetch('/api/v1/one/devices', { cache: 'no-store' });
    if (res.status === 401 || res.status === 403) {
        body.innerHTML = '<tr><td class="vg-empty" colspan="5">Sign in as chief or admin to see paired computers.</td></tr>';
        return;
    }
    const devices = res.ok ? await res.json() : [];
    body.innerHTML = devices.length ? devices.map((d) => `<tr>
        <td class="strong">${esc(d.name)}<br><small>${esc(d.hostname || '')} ${esc(d.app_version || '')}</small></td>
        <td>${esc(when(d.paired_at))}<br><small>by ${esc(d.paired_by || '—')}</small></td>
        <td>${esc(when(d.last_seen_at))}</td>
        <td>${d.revoked ? '<span class="vg-chip">REVOKED</span>' : '<span class="vg-chip" data-tone="good">ACTIVE</span>'}</td>
        <td>${d.revoked ? '' : `<button type="button" class="vigil-btn vigil-btn--sm vigil-btn--danger" data-revoke="${esc(d.id)}">Revoke</button>`}</td>
    </tr>`).join('') : '<tr><td class="vg-empty" colspan="5">No VIGIL One computer paired yet.</td></tr>';
}

async function createPairingCode() {
    const res = await fetch('/api/v1/one/pairing-codes', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        document.getElementById('pairHint').textContent = data.detail || `HTTP ${res.status}`;
        return;
    }
    document.getElementById('pairCode').textContent = data.code;
    const until = new Date(data.expires_at);
    document.getElementById('pairHint').textContent = `valid until ${until.toLocaleTimeString()} · server ${window.location.host}`;
    log(`Pairing code created (valid ${Math.round(data.valid_seconds / 60)} min)`);
}

async function revokeDevice(id) {
    if (!window.confirm('Revoke this computer? It keeps its data locally but can no longer send to this server.')) return;
    const res = await fetch(`/api/v1/one/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
    log(res.ok ? 'VIGIL One computer revoked' : `Revoke failed (HTTP ${res.status})`, res.ok ? '' : 'danger');
    loadDevices();
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('pairCodeBtn').addEventListener('click', createPairingCode);
    document.getElementById('oneDevices').addEventListener('click', (event) => {
        const btn = event.target.closest('[data-revoke]');
        if (btn) revokeDevice(btn.dataset.revoke);
    });
    loadDevices();
    setInterval(loadDevices, 15000);
});

document.addEventListener('DOMContentLoaded', () => {
    $('actorInput').value = currentActor();
    $('actorInput').addEventListener('change', () => {
        try { localStorage.setItem('vigil.reviewer', $('actorInput').value.trim()); } catch (e) { /* private mode */ }
    });
    $('refreshBtn').addEventListener('click', loadOverview);
    document.querySelectorAll('.admin-action').forEach((card) => {
        card.querySelector('button').addEventListener('click', () => openConfirm(card.dataset.action));
    });
    $('confirmInput').addEventListener('input', () => {
        const phrase = overview ? overview.confirm_phrases[pendingAction] : '';
        $('confirmRun').disabled = $('confirmInput').value.trim().toUpperCase() !== phrase;
    });
    $('confirmCancel').addEventListener('click', () => $('confirmDialog').close());
    $('confirmForm').addEventListener('submit', runPending);
    loadOverview();
});
