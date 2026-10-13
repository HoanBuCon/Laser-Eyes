/**
 * VIGIL AI — Review queue: every AI incident and proctor bookmark, across
 * sessions and rooms, with evidence and the human decision side by side.
 *
 * Filters live in the URL (?status=&room=&pattern=&severity=&source=&sort=)
 * so a filtered queue can be shared.  Keyboard: J/K move, C/R/I choose a
 * decision, Enter saves and opens the next incident.
 */

const demoToken = new URLSearchParams(window.location.search).get('token') || '';
const PAGE_SIZE = 50;
const REVIEWER_KEY = 'vigil.reviewer';

const state = {
    filters: { status: 'PENDING', room: '', pattern: '', severity: '', source: 'ALL', sort: 'newest', session_id: '' },
    items: [],
    total: 0,
    offset: 0,
    selectedId: null,
    decision: null,
    loading: false,
};

function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (demoToken) headers.set('X-Vigil-Demo-Token', demoToken);
    return fetch(url, { ...options, headers });
}

function authenticatedUrl(url) {
    if (!demoToken || !url) return url;
    const parsed = new URL(url, window.location.origin);
    parsed.searchParams.set('token', demoToken);
    return `${parsed.pathname}${parsed.search}`;
}

const $ = (id) => document.getElementById(id);

function itemId(item) {
    return item.source_type === 'MANUAL_BOOKMARK' ? `MANUAL:${item.bookmark_id}` : `AI:${item.event_id}`;
}

function prettyName(raw) {
    if (!raw) return 'Review signal';
    return String(raw).replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (l) => l.toUpperCase());
}

// "SEAT-CLASSROOM-03-15" -> "S15"
function seatShort(ref) {
    const digits = String(ref || '').match(/(\d+)(?!.*\d)/);
    return digits ? `S${digits[1].padStart(2, '0')}` : (ref ? String(ref).slice(0, 6) : '—');
}

function roomShort(code) {
    return String(code || '').replace(/^ROOM-/, '');
}

function seconds(ms) {
    return ms === null || ms === undefined ? null : `${(Number(ms) / 1000).toFixed(1)}s`;
}

function timingText(item) {
    if (item.source_type === 'MANUAL_BOOKMARK') {
        return item.source_timestamp_ms !== null && item.source_timestamp_ms !== undefined
            ? `Marked at ${seconds(item.source_timestamp_ms)} of the video`
            : 'Marked by a proctor';
    }
    const flagged = item.first_seen_ms;
    if (flagged === null || flagged === undefined) {
        // Incidents recorded before timing was stored
        return item.created_at ? `Recorded ${new Date(item.created_at + 'Z').toLocaleString()}` : 'Time not recorded';
    }
    const start = item.behavior_start_ms ?? flagged;
    const parts = [];
    parts.push(flagged - start >= 100 ? `Behaviour ${seconds(start)} → flagged ${seconds(flagged)}` : `Flagged at ${seconds(flagged)}`);
    if ((item.occurrence_count || 1) > 1 && item.last_seen_ms > flagged) parts.push(`re-flagged until ${seconds(item.last_seen_ms)}`);
    return parts.join(' · ');
}

function compactTiming(item) {
    if (item.source_type === 'MANUAL_BOOKMARK') return seconds(item.source_timestamp_ms) || 'proctor';
    const flagged = item.first_seen_ms;
    if (flagged === null || flagged === undefined) return item.created_at ? new Date(item.created_at + 'Z').toLocaleDateString() : '';
    const start = item.behavior_start_ms ?? flagged;
    return flagged - start >= 100 ? `${(start / 1000).toFixed(1)}→${(flagged / 1000).toFixed(1)}s` : seconds(flagged);
}

// -----------------------------------------------------------------------------
// Filters (kept in the URL)
// -----------------------------------------------------------------------------

function readFiltersFromUrl() {
    const params = new URLSearchParams(window.location.search);
    for (const key of Object.keys(state.filters)) {
        if (params.has(key)) state.filters[key] = params.get(key);
    }
}

function writeFiltersToUrl() {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(state.filters)) {
        if (value && !(key === 'status' && value === 'PENDING') && !(key === 'source' && value === 'ALL') && !(key === 'sort' && value === 'newest')) {
            params.set(key, value);
        } else {
            params.delete(key);
        }
    }
    const query = params.toString();
    history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
}

function syncFilterControls() {
    document.querySelectorAll('.status-filters [data-status]').forEach((btn) => {
        btn.setAttribute('aria-pressed', String(btn.dataset.status === state.filters.status));
    });
    $('filterRoom').value = state.filters.room;
    $('filterPattern').value = state.filters.pattern;
    $('filterSeverity').value = state.filters.severity;
    $('filterSource').value = state.filters.source;
    $('sortOrder').value = state.filters.sort;
    const heading = { ALL: 'All incidents', PENDING: 'Pending', CONFIRMED: 'Confirmed', REJECTED: 'Rejected', INCONCLUSIVE: 'Unsure' };
    $('listHeading').textContent = heading[state.filters.status] || 'Incidents';
}

function setFilter(key, value) {
    state.filters[key] = value;
    writeFiltersToUrl();
    syncFilterControls();
    loadQueue(true);
}

function fillSelect(select, options, current, emptyLabel) {
    const keep = select.value || current;
    select.replaceChildren(new Option(emptyLabel, ''));
    options.forEach(({ value, label }) => select.append(new Option(label, value)));
    if ([...select.options].some((o) => o.value === keep)) select.value = keep;
}

// -----------------------------------------------------------------------------
// Queue
// -----------------------------------------------------------------------------

async function loadQueue(reset) {
    if (state.loading) return;
    state.loading = true;
    if (reset) state.offset = 0;
    const params = new URLSearchParams({
        status: state.filters.status,
        source: state.filters.source,
        sort: state.filters.sort,
        limit: String(PAGE_SIZE),
        offset: String(state.offset),
    });
    for (const key of ['room', 'pattern', 'severity', 'session_id']) {
        if (state.filters[key]) params.set(key, state.filters[key]);
    }
    try {
        const res = await apiFetch(`/api/v1/proctor/review-queue?${params}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        state.items = reset ? data.items : state.items.concat(data.items);
        state.total = data.total;
        Object.entries(data.counts || {}).forEach(([status, n]) => {
            const el = document.querySelector(`[data-count-for="${status}"]`);
            if (el) el.textContent = n;
        });
        fillSelect($('filterRoom'), (data.facets?.rooms || []).map((r) => ({ value: r.code, label: `${roomShort(r.code)} · ${r.count}` })), state.filters.room, 'All rooms');
        fillSelect($('filterPattern'), (data.facets?.patterns || []).map((p) => ({ value: p.name, label: `${prettyName(p.name)} · ${p.count}` })), state.filters.pattern, 'All behaviours');
        showError(null);
        renderList();
        if (state.openId && await openLinkedItem()) return;
        // Keep the open incident if it is still listed, otherwise open the first
        if (!state.items.some((item) => itemId(item) === state.selectedId)) {
            selectItem(state.items.length ? itemId(state.items[0]) : null);
        }
    } catch (err) {
        showError(`Could not load the review queue (${err.message}).`);
    } finally {
        state.loading = false;
    }
}

// Other pages (Operations, Playback) link here with ?open=AI:<event_id>.  The
// incident is opened even when it is not on the first page or outside the
// current filter; the link parameter is then dropped from the URL.
async function openLinkedItem() {
    const wanted = state.openId;
    state.openId = null;
    const params = new URLSearchParams(window.location.search);
    params.delete('open');
    const query = params.toString();
    history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
    if (!state.items.some((item) => itemId(item) === wanted) && wanted.startsWith('AI:')) {
        try {
            const res = await apiFetch(`/api/v1/proctor/review-queue?status=ALL&event_id=${encodeURIComponent(wanted.slice(3))}&limit=1`);
            const data = res.ok ? await res.json() : { items: [] };
            if (!data.items.length) return false;
            state.items.unshift(data.items[0]);
            renderList();
        } catch (err) {
            return false;
        }
    }
    selectItem(wanted);
    return true;
}

function renderList() {
    const list = $('reviewList');
    $('listCount').textContent = String(state.total);
    $('btnMore').classList.toggle('hidden', state.items.length >= state.total);
    if (!state.items.length) {
        list.innerHTML = `<div class="review-empty">${state.filters.status === 'PENDING' ? 'Nothing left to review. 🎉' : 'No incidents match these filters.'}</div>`;
        return;
    }
    list.replaceChildren(...state.items.map((item) => {
        const manual = item.source_type === 'MANUAL_BOOKMARK';
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'queue-row';
        row.setAttribute('role', 'listitem');
        row.dataset.id = itemId(item);
        row.dataset.severity = manual ? 'MANUAL' : (item.severity || 'MEDIUM');
        row.dataset.status = item.review_status || 'PENDING';
        row.setAttribute('aria-current', String(row.dataset.id === state.selectedId));

        const seat = document.createElement('span');
        seat.className = 'queue-seat';
        const room = document.createElement('small');
        room.textContent = roomShort(item.room_code) || (manual ? 'MARK' : '');
        const seatLabel = document.createElement('strong');
        seatLabel.textContent = seatShort(item.subject_ref);
        seat.append(room, seatLabel);

        const main = document.createElement('span');
        main.className = 'queue-main';
        const title = document.createElement('span');
        title.className = 'queue-title';
        title.textContent = manual ? (item.note || 'Proctor bookmark') : prettyName(item.primary_signal);
        const meta = document.createElement('span');
        meta.className = 'queue-meta';
        meta.textContent = [compactTiming(item), manual ? 'proctor' : `x${item.occurrence_count || 1}`].filter(Boolean).join(' · ');
        main.append(title, meta);

        const side = document.createElement('span');
        side.className = 'queue-side';
        if (!manual) {
            const score = document.createElement('span');
            score.className = 'queue-score';
            const value = Number(item.review_priority_score);
            score.dataset.level = Number.isFinite(value) ? (value >= 85 ? 'high' : 'medium') : 'none';
            score.textContent = Number.isFinite(value) ? String(Math.round(value)) : '--';
            side.append(score);
        }
        const status = document.createElement('span');
        status.className = 'queue-status';
        status.dataset.status = row.dataset.status;
        status.textContent = { PENDING: 'Pending', CONFIRMED: 'Confirmed', REJECTED: 'Rejected', INCONCLUSIVE: 'Unsure' }[row.dataset.status] || row.dataset.status;
        if (item.session_closed) {
            // Left undecided when the session's report was approved
            row.dataset.closed = 'true';
            if (row.dataset.status === 'PENDING') status.textContent = 'Closed';
            row.title = 'Session closed: read-only';
        }
        side.append(status);

        row.append(seat, main, side);
        row.addEventListener('click', () => selectItem(row.dataset.id));
        return row;
    }));
}

function currentItem() {
    return state.items.find((item) => itemId(item) === state.selectedId) || null;
}

// -----------------------------------------------------------------------------
// Detail: evidence + decision
// -----------------------------------------------------------------------------

function selectItem(id) {
    state.selectedId = id;
    document.querySelectorAll('#reviewList .queue-row').forEach((row) => {
        row.setAttribute('aria-current', String(row.dataset.id === id));
        if (row.dataset.id === id) row.scrollIntoView({ block: 'nearest' });
    });
    renderDetail(currentItem());
}

function renderDetail(item) {
    const video = $('dVideo');
    video.pause();
    video.removeAttribute('src');
    video.load();
    $('detailEmpty').classList.toggle('hidden', Boolean(item));
    $('detailBody').classList.toggle('hidden', !item);
    if (!item) return;

    const manual = item.source_type === 'MANUAL_BOOKMARK';
    $('dSeat').textContent = `${roomShort(item.room_code)} ${seatShort(item.subject_ref)}`.trim();
    $('dTitle').textContent = manual ? 'Proctor bookmark' : prettyName(item.primary_signal);
    $('dSeverity').textContent = manual ? 'PROCTOR' : (item.severity || 'MEDIUM');
    $('dSeverity').dataset.severity = manual ? 'MANUAL' : (item.severity || 'MEDIUM');
    $('dStatus').textContent = item.review_status || 'PENDING';
    $('dStatus').dataset.status = item.review_status || 'PENDING';
    const score = Number(item.review_priority_score);
    $('dScore').textContent = manual || !Number.isFinite(score) ? '' : `${Math.round(score)}/100`;
    $('dContext').textContent = [item.room_name, item.subject_ref].filter(Boolean).join(' · ');
    $('dTiming').textContent = timingText(item);
    $('dSession').textContent = [item.session_name, item.session_status].filter(Boolean).join(' · ') || item.session_id || '--';
    const cues = manual ? [item.note].filter(Boolean) : (item.supporting_cues || []);
    $('dCues').textContent = cues.length ? cues.join(' · ') : '--';
    $('dLastDecision').textContent = item.reviewed_at
        ? `${item.review_decision || item.review_status} by ${item.reviewer_id || 'unknown'} · ${new Date(item.reviewed_at + 'Z').toLocaleString()}`
        : 'Not reviewed yet';

    // Evidence
    const videoUrl = item.video_url ? authenticatedUrl(item.video_url) : '';
    const snapshotUrl = authenticatedUrl(item.snapshot_url || (manual && item.bookmark_id ? `/api/v1/proctor/bookmarks/${item.bookmark_id}/snapshot` : ''));
    $('dVideo').closest('.evidence-frame').classList.toggle('is-missing', !videoUrl);
    if (videoUrl) {
        setVideoStatus('Preparing video…');
        video.src = videoUrl;
        video.load();
    } else {
        setVideoStatus(item.evidence_error || (manual ? 'Bookmarks keep a frame, not a clip.' : 'No clip for this incident.'));
    }
    $('dSnapshot').src = snapshotUrl || '';
    $('dSnapshotLink').href = snapshotUrl || '#';
    $('dSnapshotLink').classList.toggle('hidden', !snapshotUrl);
    renderFocus(item);

    $('dIntegrity').textContent = item.sha256 ? 'Checking…' : 'No digest stored';
    if (item.sha256 && !manual) checkIntegrity(item);

    // Decision form starts from the stored decision
    state.decision = item.review_status && item.review_status !== 'PENDING' ? item.review_status : null;
    $('dReason').value = item.reason_code || 'NONE';
    $('dNotes').value = item.review_note && !/^Occurrence x\d+$/.test(item.review_note) ? item.review_note : '';
    updateDecisionButtons();
    $('decisionMessage').textContent = '';

    // A closed session's decisions are frozen with its approved report
    const locked = Boolean(item.session_closed);
    $('closedNotice').classList.toggle('hidden', !locked);
    $('closedReportLink').href = `/reports?session=${encodeURIComponent(item.session_id || '')}`;
    $('decisionForm').classList.toggle('is-locked', locked);
    $('decisionForm').querySelectorAll('button, select, textarea, input').forEach((el) => { el.disabled = locked || (el.id === 'btnSubmit' && !state.decision); });
}

function renderFocus(item) {
    const label = `${seatShort(item.subject_ref)} · ${prettyName(item.primary_signal)}`;
    const focus = $('chkFocus').checked ? item.focus : null;
    drawFocusOverlay($('dVideoFocus'), focus, label);
    drawFocusOverlay($('dSnapshotFocus'), focus, label);
}

function setVideoStatus(message) {
    const el = $('dVideoStatus');
    el.textContent = message || '';
    el.classList.toggle('hidden', !message);
}

async function checkIntegrity(item) {
    try {
        const res = await apiFetch(`/api/v1/demo/events/${encodeURIComponent(item.event_id)}/integrity`);
        if (!res.ok) throw new Error();
        const data = await res.json();
        if (itemId(item) !== state.selectedId) return;
        const labels = { HASH_VERIFIED: 'SHA-256 verified ✓', HASH_MISMATCH: 'SHA-256 MISMATCH', HASH_NOT_AVAILABLE: 'No digest stored' };
        $('dIntegrity').textContent = labels[data.hash_status] || 'Digest stored, not checked';
        $('dIntegrity').dataset.state = data.hash_status || '';
    } catch (err) {
        $('dIntegrity').textContent = 'Integrity check unavailable';
    }
}

function updateDecisionButtons() {
    document.querySelectorAll('.decision-btn').forEach((btn) => {
        btn.setAttribute('aria-pressed', String(btn.dataset.decision === state.decision));
    });
    $('btnSubmit').disabled = !state.decision;
}

function isLocked() {
    const item = currentItem();
    return Boolean(item && item.session_closed);
}

function chooseDecision(decision) {
    if (isLocked()) return;
    state.decision = decision;
    updateDecisionButtons();
}

async function submitDecision() {
    const item = currentItem();
    if (!item || !state.decision || item.session_closed) return;
    const reviewer = ($('reviewerName').value || '').trim();
    if (!reviewer) {
        $('decisionMessage').textContent = 'Enter your name (top right) first.';
        $('reviewerName').focus();
        return;
    }
    const manual = item.source_type === 'MANUAL_BOOKMARK';
    const reason = $('dReason').value;
    const notes = $('dNotes').value.trim();
    const endpoint = manual
        ? `/api/v1/proctor/bookmarks/${encodeURIComponent(item.bookmark_id)}/review`
        : `/api/v1/demo/events/${encodeURIComponent(item.event_id)}/review`;
    const body = manual
        ? { decision: state.decision, reason_code: reason, note: notes, reviewer_id: reviewer }
        : { decision: state.decision, reason_code: reason, notes, reviewer_id: reviewer };
    $('btnSubmit').disabled = true;
    try {
        const res = await apiFetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const detail = await res.json().catch(() => ({}));
            throw new Error(detail.detail || `HTTP ${res.status}`);
        }
        // Move on to the next incident of the current list, then refresh counts
        const index = state.items.findIndex((x) => itemId(x) === state.selectedId);
        const next = state.items[index + 1] || state.items[index - 1];
        state.selectedId = next ? itemId(next) : null;
        await loadQueue(true);
    } catch (err) {
        $('decisionMessage').textContent = `Not saved: ${err.message}`;
        $('btnSubmit').disabled = false;
    }
}

function move(step) {
    if (!state.items.length) return;
    const index = state.items.findIndex((x) => itemId(x) === state.selectedId);
    const next = state.items[Math.min(state.items.length - 1, Math.max(0, index + step))];
    if (next) selectItem(itemId(next));
}

function showError(message) {
    const el = $('reviewError');
    el.textContent = message || '';
    el.classList.toggle('hidden', !message);
}

// -----------------------------------------------------------------------------
// Wiring
// -----------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    readFiltersFromUrl();
    state.openId = new URLSearchParams(window.location.search).get('open');
    syncFilterControls();

    try { $('reviewerName').value = localStorage.getItem(REVIEWER_KEY) || ''; } catch (e) { /* storage blocked */ }
    $('reviewerName').addEventListener('change', (e) => {
        try { localStorage.setItem(REVIEWER_KEY, e.target.value.trim()); } catch (err) { /* storage blocked */ }
    });

    document.querySelectorAll('.status-filters [data-status]').forEach((btn) => {
        btn.addEventListener('click', () => setFilter('status', btn.dataset.status));
    });
    $('filterRoom').addEventListener('change', (e) => setFilter('room', e.target.value));
    $('filterPattern').addEventListener('change', (e) => setFilter('pattern', e.target.value));
    $('filterSeverity').addEventListener('change', (e) => setFilter('severity', e.target.value));
    $('filterSource').addEventListener('change', (e) => setFilter('source', e.target.value));
    $('sortOrder').addEventListener('change', (e) => setFilter('sort', e.target.value));
    $('btnMore').addEventListener('click', () => { state.offset = state.items.length; loadQueue(false); });
    $('chkFocus').addEventListener('change', () => { const item = currentItem(); if (item) renderFocus(item); });

    document.querySelectorAll('.decision-btn').forEach((btn) => {
        btn.addEventListener('click', () => chooseDecision(btn.dataset.decision));
    });
    $('decisionForm').addEventListener('submit', (e) => { e.preventDefault(); submitDecision(); });

    const video = $('dVideo');
    video.addEventListener('loadedmetadata', () => setVideoStatus(''));
    video.addEventListener('error', () => {
        if (!video.getAttribute('src')) return;  // cleared on purpose
        setVideoStatus('The clip could not be played. Use the frame on the right.');
    });

    // Keyboard shortcuts, except while typing
    document.addEventListener('keydown', (e) => {
        const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
        if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
        const key = e.key.toLowerCase();
        if (key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); move(1); }
        else if (key === 'k' || e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
        else if (key === 'c') chooseDecision('CONFIRMED');
        else if (key === 'r') chooseDecision('REJECTED');
        else if (key === 'i') chooseDecision('INCONCLUSIVE');
        else if (e.key === 'Enter' && state.decision) { e.preventDefault(); submitDecision(); }
    });

    loadQueue(true);
    // New incidents from a running analysis appear without reloading
    setInterval(() => { if (!document.hidden && state.offset === 0) loadQueue(true); }, 15000);
});
