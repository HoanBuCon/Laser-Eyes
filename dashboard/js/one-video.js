/*
 * Session video (VIGIL One): the whole exam of one candidate as ONE video.
 *
 *   /one-video?session=<session id>&t=<ms from the start of the exam>
 *
 * The Hub joins the segments uploaded during the exam into one MP4 when the session ends;
 * until then (or if joining failed) the segments play one after the other as if they were
 * one video.  Signals sit on the timeline: a click jumps to that moment of the exam.
 * Stretches of the exam with no video (app closed, power cut) are marked, not hidden.
 */

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(window.location.search);
const sessionId = params.get('session') || '';
const startAt = Number(params.get('t'));
const state = { info: null, signals: [], playlist: false, current: -1, pendingSeek: null };

function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function api(url, options = {}) {
    const res = await fetch(url, { cache: 'no-store', ...options });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
}

function clock(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rest = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`;
}

function bytes(n) {
    if (!n) return '0 MB';
    return n > 1e9 ? `${(n / 1e9).toFixed(2)} GB` : `${(n / 1e6).toFixed(1)} MB`;
}

// --- moments of the exam <-> position in the video ------------------------------------

/** Seconds into the joined video for a moment of the exam (ms); the next recorded moment inside a gap. */
function locate(elapsedMs) {
    for (const row of state.info.segments) {
        const end = row.start_ms + row.duration_s * 1000;
        if (elapsedMs < row.start_ms) return row.offset_s;
        if (elapsedMs <= end) return row.offset_s + (elapsedMs - row.start_ms) / 1000;
    }
    return null;
}

function totalSeconds() {
    return state.info.duration_s || state.info.segments.reduce((sum, row) => sum + row.duration_s, 0);
}

/** Current position in the joined video, whichever way it is playing. */
function position() {
    const video = $('ovVideo');
    if (!state.playlist) return video.currentTime || 0;
    const row = state.info.segments[state.current];
    return row ? row.offset_s + (video.currentTime || 0) : 0;
}

function seek(seconds) {
    const video = $('ovVideo');
    if (!state.playlist) {
        video.currentTime = Math.max(0, seconds);
        return;
    }
    const rows = state.info.segments;
    let index = rows.findIndex((row) => seconds < row.offset_s + row.duration_s);
    if (index < 0) index = rows.length - 1;
    const local = Math.max(0, seconds - rows[index].offset_s);
    if (index !== state.current) {
        loadSegment(index, local);
    } else {
        video.currentTime = local;
    }
}

/** The moment of the exam (ms) shown at a position of the joined video: the inverse of locate(). */
function examMsAt(seconds) {
    const rows = state.info ? state.info.segments : [];
    const row = rows.find((r) => seconds < r.offset_s + r.duration_s) || rows[rows.length - 1];
    return row ? row.start_ms + Math.max(0, seconds - row.offset_s) * 1000 : seconds * 1000;
}

// --- debug overlay: face, gaze and objects the analysis saw (the global debug switch) -------------

function renderDebug() {
    const on = Boolean(window.vigilDebug && window.vigilDebug.isOn()) && Boolean(state.info && state.info.segment_count);
    $('ovDebug').checked = on;
    if (!on) {
        ReviewDebug.stop();
        return;
    }
    ReviewDebug.attach({
        video: $('ovVideo'),
        svg: $('ovDebugSvg'),
        info: $('ovDebugInfo'),
        timeMs: () => examMsAt(position()),
        loadWindow: (from, to) => api(`/api/v1/one/sessions/${encodeURIComponent(sessionId)}/trace?from_ms=${Math.round(from)}&to_ms=${Math.round(to)}`),
    });
}

function seekToExamMs(ms) {
    const at = locate(ms);
    if (at === null) return;
    seek(at);
    $('ovVideo').play().catch(() => {});
}

// --- playing ------------------------------------------------------------------------------

function loadSegment(index, local = 0) {
    const video = $('ovVideo');
    const row = state.info.segments[index];
    if (!row) return;
    state.current = index;
    state.pendingSeek = local;
    const playing = !video.paused;
    video.src = row.url;
    video.load();
    if (playing || index > 0) video.play().catch(() => {});
}

function bindVideo() {
    const video = $('ovVideo');
    video.addEventListener('loadedmetadata', () => {
        if (state.pendingSeek !== null) {
            video.currentTime = state.pendingSeek;
            state.pendingSeek = null;
        }
    });
    video.addEventListener('ended', () => {
        if (state.playlist && state.current < state.info.segments.length - 1) loadSegment(state.current + 1, 0);
    });
    video.addEventListener('timeupdate', drawProgress);
}

function drawProgress() {
    const total = totalSeconds();
    if (!total) return;
    $('ovProgress').style.width = `${Math.min(100, (position() / total) * 100)}%`;
}

// --- page ----------------------------------------------------------------------------------

function renderTimeline() {
    const total = totalSeconds();
    const marks = [];
    for (const [from, to] of state.info.gaps || []) {
        const at = locate(from);
        if (at === null || !total) continue;
        marks.push(`<span class="ov-gap" style="left:${(at / total) * 100}%" title="No video ${clock(from / 1000)} – ${clock(to / 1000)} of the exam"></span>`);
    }
    for (const item of state.signals) {
        const ms = Number(item.first_seen_ms ?? item.source_timestamp_ms);
        if (!Number.isFinite(ms) || !total) continue;
        const at = locate(ms);
        if (at === null) continue;
        marks.push(`<button type="button" class="ov-mark" data-ms="${ms}" data-severity="${esc(item.severity || '')}"
            style="left:${(at / total) * 100}%" title="${esc(item.label || item.primary_signal)} · ${clock(ms / 1000)}"></button>`);
    }
    $('ovMarks').innerHTML = marks.join('');
}

function renderSignals() {
    if (!state.signals.length) {
        $('ovSignals').innerHTML = '<div class="vg-empty">No signals in this session.</div>';
        return;
    }
    $('ovSignals').innerHTML = state.signals.map((item) => {
        const ms = Number(item.first_seen_ms ?? item.source_timestamp_ms);
        const when = Number.isFinite(ms) ? clock(ms / 1000) : '—';
        return `<button type="button" class="ov-signal" data-ms="${Number.isFinite(ms) ? ms : ''}">
            <span class="ov-signal-time">${when}</span>
            <span class="ov-signal-label">${esc(item.label || item.primary_signal)}</span>
            <span class="vg-label">${esc(item.severity || '')}${item.review_status && item.review_status !== 'PENDING' ? ' · ' + esc(item.review_status) : ''}</span>
        </button>`;
    }).join('');
}

function renderFacts() {
    const info = state.info;
    const gaps = info.gaps || [];
    const facts = [
        ['Length', clock(totalSeconds())],
        ['Segments received', String(info.segment_count)],
        ['Size', bytes(info.bytes)],
        ['Missing', gaps.length ? gaps.map(([a, b]) => `${clock(a / 1000)}–${clock(b / 1000)}`).join(', ') : 'none'],
        ['SHA-256 (joined file)', info.sha256 || '—'],
    ];
    if (info.error) facts.push(['Note', info.error]);
    $('ovFacts').innerHTML = facts.map(([k, v]) => `<div><span class="vg-label">${esc(k)}</span><span class="ov-fact">${esc(v)}</span></div>`).join('');
}

const STATUS_TEXT = {
    NONE: 'No video recorded',
    RECORDING: 'Recording — segments arrive during the exam',
    MERGING: 'Joining the segments into one video…',
    READY: 'One video for the whole exam',
    PARTIAL: 'One video — some minutes are missing',
    FAILED: 'Not joined — playing the segments',
};

async function load() {
    if (!sessionId) {
        $('ovEmpty').hidden = false;
        $('ovEmpty').textContent = 'Open this page from a VIGIL One session (Online exams or the review queue).';
        return;
    }
    try {
        state.info = await api(`/api/v1/one/sessions/${encodeURIComponent(sessionId)}/video`);
    } catch (e) {
        $('ovEmpty').hidden = false;
        $('ovEmpty').textContent = e.message;
        return;
    }
    const info = state.info;
    $('ovStatus').textContent = STATUS_TEXT[info.status] || info.status;
    $('ovStatus').dataset.tone = info.status === 'READY' ? 'good' : (info.status === 'PARTIAL' || info.status === 'FAILED' ? 'warning' : '');
    $('ovReview').href = `/review?product=ONE&session_id=${encodeURIComponent(sessionId)}`;
    $('ovMerge').hidden = !['RECORDING', 'FAILED', 'PARTIAL'].includes(info.status) || !info.segment_count;
    if (info.url) {
        $('ovDownload').hidden = false;
        $('ovDownload').href = info.url;
    }
    try {
        const q = await api(`/api/v1/proctor/review-queue?status=ALL&product=ONE&limit=500&session_id=${encodeURIComponent(sessionId)}`);
        state.signals = (q.items || []).filter((i) => i.session_id === sessionId)
            .sort((a, b) => Number(a.first_seen_ms ?? 0) - Number(b.first_seen_ms ?? 0));
        const first = q.items && q.items[0];
        if (first) $('ovTitle').textContent = `${first.subject_ref || first.student_id || 'Candidate'} · ${first.session_name || ''}`;
    } catch (e) {
        state.signals = [];
    }
    renderFacts();
    renderSignals();
    if (!info.segment_count) {
        $('ovEmpty').hidden = false;
        $('ovEmpty').textContent = 'No video has arrived for this session yet.';
        $('ovVideo').hidden = true;
        return;
    }
    state.playlist = !info.url;
    if (info.url) {
        $('ovVideo').src = info.url;
    } else {
        loadSegment(0, 0);
    }
    renderTimeline();
    if (Number.isFinite(startAt) && params.has('t')) {
        const at = locate(startAt);
        if (at !== null) {
            if (state.playlist) seek(at);
            else state.pendingSeek = at;
        }
    }
}

document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-ms]');
    if (target && target.dataset.ms !== '') seekToExamMs(Number(target.dataset.ms));
});

$('ovTimeline').addEventListener('click', (event) => {
    if (event.target.closest('[data-ms]') || !state.info) return;
    const box = event.currentTarget.getBoundingClientRect();
    seek(((event.clientX - box.left) / box.width) * totalSeconds());
});

$('ovMerge').addEventListener('click', async () => {
    $('ovMerge').disabled = true;
    try {
        await api(`/api/v1/one/sessions/${encodeURIComponent(sessionId)}/video/merge`, { method: 'POST' });
        $('ovStatus').textContent = STATUS_TEXT.MERGING;
        setTimeout(() => window.location.reload(), 4000);
    } catch (e) {
        $('ovStatus').textContent = `${e.message} (sign in as chief or admin)`;
        $('ovMerge').disabled = false;
    }
});

$('ovDebug').addEventListener('change', (e) => { if (window.vigilDebug) window.vigilDebug.set(e.target.checked); });
document.addEventListener('vigil-debug', renderDebug);
window.addEventListener('resize', () => ReviewDebug.redraw());

bindVideo();
load().then(renderDebug);
