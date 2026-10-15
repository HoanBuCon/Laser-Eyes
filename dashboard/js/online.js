/*
 * 04 Online exams: every candidate of an exam sitting it with VIGIL One on
 * their own computer, as a grid of small live pictures with their state.
 *
 * Polling (3 s) keeps it simple and robust; a candidate opened in the detail
 * view gets a bigger, more frequent live picture (POST .../boost, renewed
 * every 50 s while open).  Signals are decided in /review like Classroom's.
 */

const $ = (id) => document.getElementById(id);
const STORE_KEY = 'vigil.online.exam';
const STATE_ORDER = { LOST: 0, LIVE: 1, JOINED: 2, NOT_JOINED: 3, LEFT: 4, SUBMITTED: 5, ENDED: 5 };
const STATE_LABEL = {
    LIVE: ['Sitting', 'good'], LOST: ['Connection lost', 'danger'], JOINED: ['Joined, not started', 'warning'],
    NOT_JOINED: ['Not joined', ''], SUBMITTED: ['Submitted', ''], LEFT: ['Left early', 'danger'], ENDED: ['Ended', ''],
};
const state = { exams: [], examId: '', roster: null, filter: 'ALL', detail: null, issuedPins: [] };

function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function api(url, options = {}) {
    const res = await fetch(url, { cache: 'no-store', ...options });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
}

// --- exams -----------------------------------------------------------------------

async function loadExams() {
    state.exams = await api('/api/v1/one/exams');
    const saved = (() => { try { return localStorage.getItem(STORE_KEY) || ''; } catch (e) { return ''; } })();
    const select = $('examSelect');
    select.replaceChildren(...state.exams.map((e) => new Option(`${e.name}${e.join_open ? ' · open' : ''}`, e.id)));
    if (!state.exams.length) {
        select.append(new Option('No active exam — create one in Exams & sessions', ''));
    }
    // ?exam=…&session=… (from Operations) opens that candidate
    const params = new URLSearchParams(location.search);
    const wanted = params.get('exam') || saved;
    state.examId = state.exams.some((e) => e.id === wanted) ? wanted : (state.exams[0]?.id || '');
    select.value = state.examId;
    await refresh();
    const sessionId = params.get('session');
    const row = sessionId && state.roster?.candidates.find((c) => c.session && c.session.session_id === sessionId);
    if (row) openDetail(row.id);
}

function analysisText(session) {
    if (!session || session.analysis_mode !== 'SERVER') return 'analysed on the candidate computer';
    const a = (session.live || {}).analysis || {};
    if (!a.frames) return 'analysed on the Hub · waiting for video';
    return `analysed on the Hub · ${a.calibration === 'READY' ? 'calibrated' : 'calibrating'} · ${a.lag_s == null ? '' : (a.lag_s < 30 ? 'up to date' : `${Math.round(a.lag_s)} s behind`)}`;
}

function currentExam() {
    return (state.roster && state.roster.exam) || state.exams.find((e) => e.id === state.examId) || null;
}

// --- roster + grid ------------------------------------------------------------------

async function refresh() {
    if (!state.examId) { renderGrid(); return; }
    try {
        state.roster = await api(`/api/v1/one/exams/${encodeURIComponent(state.examId)}/roster`);
    } catch (e) {
        $('gridHint').textContent = `could not load: ${e.message}`;
        return;
    }
    $('gridHint').textContent = `updated ${new Date().toLocaleTimeString()}`;
    renderKpis();
    renderSetup();
    renderGrid();
    if (state.detail) renderDetail();
}

function stateOf(row) {
    return row.state === 'ENDED' ? 'SUBMITTED' : row.state;
}

function renderKpis() {
    const c = state.roster.counts || {};
    const total = state.roster.candidates.length;
    $('kAll').textContent = total;
    $('kLive').textContent = c.LIVE || 0;
    $('kLost').textContent = c.LOST || 0;
    $('kDone').textContent = (c.SUBMITTED || 0) + (c.ENDED || 0);
    $('kLeft').textContent = c.LEFT ? `${c.LEFT} left early` : 'pressed Submit';
    $('kWaiting').textContent = (c.NOT_JOINED || 0) + (c.JOINED || 0);
    document.querySelectorAll('.on-kpi').forEach((k) => k.setAttribute('aria-pressed', String(k.dataset.filter === state.filter)));
}

function matches(row) {
    const s = stateOf(row);
    if (state.filter === 'ALL') return true;
    if (state.filter === 'DONE') return s === 'SUBMITTED' || s === 'LEFT';
    if (state.filter === 'WAITING') return s === 'NOT_JOINED' || s === 'JOINED';
    return s === state.filter;
}

function renderGrid() {
    const grid = $('grid');
    if (!state.roster) {
        grid.innerHTML = '<div class="vg-empty">Choose an exam.</div>';
        return;
    }
    const rows = state.roster.candidates.filter(matches).sort((a, b) =>
        (STATE_ORDER[stateOf(a)] ?? 9) - (STATE_ORDER[stateOf(b)] ?? 9)
        || ((b.session?.total_events || 0) - (a.session?.total_events || 0))
        || a.candidate_code.localeCompare(b.candidate_code));
    $('gridTitle').textContent = `Candidates · ${rows.length}`;
    if (!rows.length) {
        grid.innerHTML = state.roster.candidates.length
            ? '<div class="vg-empty">Nobody in this group.</div>'
            : '<div class="vg-empty">No candidates yet. Open <strong>Exam setup</strong> to add the list.</div>';
        return;
    }
    const stamp = Math.floor(Date.now() / 3000);
    grid.innerHTML = rows.map((row) => {
        const s = stateOf(row);
        const [label, tone] = STATE_LABEL[s] || [s, ''];
        const session = row.session;
        const live = session?.live || {};
        const signals = session?.total_events || 0;
        const thumb = s === 'LIVE' && session ? `<img alt="" src="${esc(session.thumbnail_url)}?t=${stamp}" onerror="this.remove()">` : '';
        const note = s === 'LIVE' ? '' : `<span>${esc(label.toUpperCase())}</span>`;
        return `<button type="button" class="on-card" data-state="${esc(s)}" data-id="${esc(row.id)}">
            <div class="on-thumb">${thumb}${note}</div>
            <div class="on-card-body">
                <div class="on-card-top"><strong>${esc(row.candidate_code)}</strong><span class="vg-chip" data-tone="${tone}">${esc(label)}</span></div>
                <div class="on-name">${esc(row.full_name || '—')}</div>
                <div class="on-meta">${signals ? `<span class="on-signals-badge">${signals} signal${signals > 1 ? 's' : ''}</span> · ` : ''}${
                    s === 'LIVE' ? `eyes ${esc(live.eye || '—')} · head ${esc(live.head || '—')}${session.simulation ? ' · SIMULATION' : ''}` :
                    s === 'LOST' ? `silent for ${Math.round(session?.seconds_since_heartbeat || 0)} s` : ''}</div>
            </div>
        </button>`;
    }).join('');
}

// --- setup (chief) ---------------------------------------------------------------------

function renderSetup() {
    const exam = currentExam();
    if (!exam) return;
    $('joinCode').textContent = exam.join_code || '—';
    $('joinState').textContent = exam.join_open ? 'open' : 'closed';
    $('joinState').dataset.tone = exam.join_open ? 'good' : '';
    $('joinToggle').textContent = exam.join_open ? 'Close joining' : 'Open joining';
    const policy = exam.policy || {};
    document.querySelectorAll('[data-policy]').forEach((el) => {
        if (document.activeElement === el) return;
        if (el.type === 'checkbox') el.checked = Boolean(policy[el.dataset.policy]);
        else el.value = String(policy[el.dataset.policy] ?? '');
    });
}

async function setJoining(open, rotate = false) {
    try {
        await api(`/api/v1/one/exams/${encodeURIComponent(state.examId)}/joining`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ open, rotate }),
        });
        await refresh();
    } catch (e) {
        $('rosterMsg').textContent = `${e.message} (sign in as chief or admin)`;
    }
}

async function savePolicy(el) {
    const value = el.type === 'checkbox' ? el.checked : Number(el.value);
    try {
        await api(`/api/v1/one/exams/${encodeURIComponent(state.examId)}/policy`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [el.dataset.policy]: value }),
        });
        await refresh();
    } catch (e) {
        $('rosterMsg').textContent = `${e.message} (sign in as chief or admin)`;
    }
}

function parseRoster(text) {
    return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
        const [code, name, pin] = line.split(/[,;\t]/).map((p) => (p || '').trim());
        return { candidate_code: code, full_name: name || null, pin: pin || null };
    }).filter((row) => row.candidate_code && !/^(code|candidate|mã)/i.test(row.candidate_code));
}

async function importRoster() {
    const rows = parseRoster($('rosterInput').value);
    if (!rows.length) { $('rosterMsg').textContent = 'Nothing to add.'; return; }
    try {
        const data = await api(`/api/v1/one/exams/${encodeURIComponent(state.examId)}/candidates`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidates: rows }),
        });
        state.issuedPins = data.imported.filter((r) => r.pin);
        $('rosterMsg').textContent = `${data.imported.length} candidate(s) saved.`;
        $('rosterInput').value = '';
        $('pinsBox').hidden = !state.issuedPins.length;
        $('pinsBody').innerHTML = state.issuedPins.map((r) => `<tr><td class="strong">${esc(r.candidate_code)}</td><td>${esc(r.full_name || '')}</td><td style="font-family:var(--vigil-font-mono)">${esc(r.pin)}</td></tr>`).join('');
        await refresh();
    } catch (e) {
        $('rosterMsg').textContent = `${e.message} (sign in as chief or admin)`;
    }
}

function downloadPins() {
    const exam = currentExam();
    const lines = ['candidate_code,full_name,pin,join_code', ...state.issuedPins.map((r) =>
        [r.candidate_code, r.full_name || '', r.pin, exam?.join_code || ''].map((v) => `"${String(v).replace(/"/g, '""')}"`).join(','))];
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `vigil-one-pins-${exam?.join_code || 'exam'}.csv` });
    a.click();
    URL.revokeObjectURL(a.href);
}

// --- detail -------------------------------------------------------------------------

function openDetail(candidateId) {
    state.detail = { id: candidateId, boostedAt: 0 };
    $('detail').showModal();
    renderDetail();
}

async function renderDetail() {
    const row = state.roster?.candidates.find((c) => c.id === state.detail?.id);
    if (!row) return;
    const session = row.session;
    const [label] = STATE_LABEL[stateOf(row)] || [row.state];
    $('dTitle').textContent = `${row.candidate_code} · ${row.full_name || ''} · ${label}`;
    const live = session?.live || {};
    $('dReadings').textContent = session
        ? `eyes ${live.eye || '—'} · head ${live.head || '—'} · people ${live.people ?? '—'} · ${live.fps ?? '—'} fps · last heartbeat ${session.seconds_since_heartbeat ?? '—'} s ago · ${analysisText(session)}${session.simulation ? ' · SIMULATION' : ''}`
        : 'This candidate has not started yet.';
    if (session && stateOf(row) === 'LIVE') {
        if (Date.now() - state.detail.boostedAt > 50000) {
            state.detail.boostedAt = Date.now();
            fetch(`/api/v1/one/sessions/${encodeURIComponent(session.session_id)}/boost`, { method: 'POST' }).catch(() => {});
        }
        const img = new Image();
        img.onload = () => { $('dLive').src = img.src; $('dLiveNote').hidden = true; };
        img.src = `${session.thumbnail_url}?t=${Date.now()}`;
    } else {
        $('dLive').removeAttribute('src');
        $('dLiveNote').hidden = false;
        $('dLiveNote').textContent = session ? 'No live picture (not connected).' : 'Not started.';
    }
    if (!session) {
        $('dSignals').innerHTML = '<div class="vg-empty">No session yet.</div>';
        return;
    }
    $('dReview').href = `/review?status=ALL&product=ONE&session_id=${encodeURIComponent(session.session_id)}`;
    $('dReport').href = `/reports?session=${encodeURIComponent(session.session_id)}`;
    $('dVideo').href = `/one-video?session=${encodeURIComponent(session.session_id)}`;
    try {
        const q = await api(`/api/v1/proctor/review-queue?status=ALL&product=ONE&limit=100&session_id=${encodeURIComponent(session.session_id)}`);
        $('dSignals').innerHTML = q.items.length ? q.items.map((item) => {
            const manual = item.source_type === 'MANUAL_BOOKMARK';
            const img = item.snapshot_url || (manual ? `/api/v1/proctor/bookmarks/${item.bookmark_id}/snapshot` : '');
            const when = item.first_seen_ms != null ? `${(item.first_seen_ms / 1000).toFixed(0)} s` : '';
            const at = item.first_seen_ms != null ? `&t=${Math.round(item.first_seen_ms)}` : '';
            return `<div class="on-signal">${img ? `<a href="/one-video?session=${encodeURIComponent(session.session_id)}${at}" title="Watch this moment"><img src="${esc(img)}" alt=""></a>` : '<span></span>'}
                <div><strong>${esc(manual ? 'Marked frame' : (item.label || item.primary_signal))}</strong><br><small>${esc(item.reason || item.note || '')}</small></div>
                <span class="vg-chip">${esc(when || item.review_status)}</span></div>`;
        }).join('') : '<div class="vg-empty">No signal so far.</div>';
    } catch (e) {
        $('dSignals').innerHTML = `<div class="vg-empty">${esc(e.message)}</div>`;
    }
}

// --- wiring -----------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    $('examSelect').addEventListener('change', (e) => {
        state.examId = e.target.value;
        try { localStorage.setItem(STORE_KEY, state.examId); } catch (err) { /* storage blocked */ }
        state.roster = null;
        refresh();
    });
    $('setupBtn').addEventListener('click', () => { $('setupPanel').hidden = !$('setupPanel').hidden; });
    $('joinToggle').addEventListener('click', () => setJoining(!currentExam()?.join_open));
    $('joinRotate').addEventListener('click', () => {
        if (window.confirm('Make a new join code? The old one stops working; candidates already in keep going.')) setJoining(true, true);
    });
    document.querySelectorAll('[data-policy]').forEach((el) => el.addEventListener('change', () => savePolicy(el)));
    $('rosterImport').addEventListener('click', importRoster);
    $('pinsDownload').addEventListener('click', downloadPins);
    document.querySelectorAll('.on-kpi').forEach((k) => k.addEventListener('click', () => {
        state.filter = state.filter === k.dataset.filter ? 'ALL' : k.dataset.filter;
        renderKpis();
        renderGrid();
    }));
    $('grid').addEventListener('click', (e) => {
        const card = e.target.closest('.on-card');
        if (card) openDetail(card.dataset.id);
    });
    $('dClose').addEventListener('click', () => $('detail').close());
    $('detail').addEventListener('close', () => { state.detail = null; });
    loadExams().catch((e) => { $('grid').innerHTML = `<div class="vg-empty">${esc(e.message)}</div>`; });
    setInterval(refresh, 3000);
});
