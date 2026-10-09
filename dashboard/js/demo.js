/**
 * VIGIL AI SRS v2.0 — Competition Demo Frontend Controller
 * Handles live MJPEG stream, WebSocket telemetry, Review Queue, and Human-in-the-Loop Adjudication.
 */

// Set from the first demo video reported by the server (GET /api/v1/demo/presets)
let activePreset = null;
// Room, camera and seat count per preset as reported by the server (seats come
// from the web calibration page).
let presetInfo = {};
// The behaviour-label checkbox follows the server once, on page load.
let behaviorLabelsSynced = false;
let activeMode = 'LIVE';
let activeFilter = 'ALL';
let activeSourceFilter = 'ALL';
let activeSort = 'newest';
// The room picker becomes a drop-down list beyond this many rooms
const ROOM_BUTTON_LIMIT = 4;
let activeEventId = null;
let activeItemType = 'AI';
let selectedDecision = 'CONFIRMED';
let pendingCapture = null;
let activeSessionId = null;
let activeEvidenceUrls = { overall: '', crop: '' };
let evidenceZoom = 1.0;
let ws = null;
let pollTimer = null;
let allIncidents = new Map(); // event_id -> event object
let allBookmarks = new Map(); // bookmark_id -> manual bookmark object
const demoToken = new URLSearchParams(window.location.search).get('token') || '';

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

document.addEventListener('DOMContentLoaded', () => {
    selectPreset(activePreset);
    selectMode(activeMode);
    filterQueue(activeFilter);
    filterQueueSource(activeSourceFilter);
    initWebSocket();
    fetchPresets();
    fetchStatus();
    fetchEvents();
    fetchBookmarks();
    initEvidenceVideoStatus();

    // Start polling fallback every 1500ms
    pollTimer = setInterval(() => {
        fetchStatus();
        fetchEvents();
        fetchBookmarks();
    }, 1500);
});

function clearVideoPlayer(player) {
    if (!player) return;
    player.removeAttribute('src');
    player.load();
}

function initEvidenceVideoStatus() {
    const player = document.getElementById('modalVideoPlayer');
    const status = document.getElementById('modalVideoStatus');
    if (!player || !status) return;
    player.addEventListener('loadstart', () => {
        status.textContent = 'Preparing browser-compatible evidence video…';
        status.classList.remove('hidden');
    });
    player.addEventListener('loadedmetadata', () => {
        status.textContent = `Evidence ready • ${player.duration.toFixed(1)} seconds`;
        window.setTimeout(() => status.classList.add('hidden'), 1400);
    });
    player.addEventListener('error', () => {
        if (!player.getAttribute('src')) return;  // cleared on purpose, nothing to play
        const mediaError = player.error;
        const detail = mediaError ? ` (media error ${mediaError.code})` : '';
        status.textContent = `Video could not be played${detail}. Use Open / download or check server logs.`;
        status.classList.remove('hidden');
    });
}

// -----------------------------------------------------------------------------
// WebSocket Realtime Telemetry
// -----------------------------------------------------------------------------

function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsToken = demoToken ? `?token=${encodeURIComponent(demoToken)}` : '';
    const wsUrl = `${protocol}//${window.location.host}/ws/demo${wsToken}`;

    try {
        ws = new WebSocket(wsUrl);
        ws.onopen = () => {
            updateWsBadge(true);
        };
        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleWebSocketMessage(data);
            } catch (err) {
                console.debug('WS Non-JSON message:', event.data);
            }
        };
        ws.onclose = () => {
            updateWsBadge(false);
            setTimeout(initWebSocket, 3000); // Auto reconnect
        };
        ws.onerror = () => {
            updateWsBadge(false);
        };
    } catch (e) {
        updateWsBadge(false);
    }
}

function updateWsBadge(connected) {
    const badge = document.getElementById('wsBadge');
    const txt = document.getElementById('wsText');
    if (!badge || !txt) return;

    badge.dataset.connected = String(connected);
    txt.textContent = connected ? 'LIVE WS' : 'WS OFFLINE';
}

function handleWebSocketMessage(msg) {
    if (msg.type === 'DEMO_STATUS' || msg.type === 'DEMO_STARTED') {
        renderStatus(msg.status);
    } else if (msg.type === 'REVIEW_INCIDENT') {
        const ev = msg.event;
        allIncidents.set(ev.event_id, ev);
        renderReviewQueue();
    } else if (msg.type === 'REVIEW_DECISION') {
        if (allIncidents.has(msg.event_id)) {
            const ev = allIncidents.get(msg.event_id);
            ev.review_status = msg.decision;
            ev.decision_reason = msg.reason_code;
            ev.reviewer_notes = msg.notes;
            renderReviewQueue();
        }
    } else if (msg.type === 'PROCTOR_BOOKMARK' || msg.type === 'PROCTOR_BOOKMARK_REVIEW') {
        const bookmark = msg.bookmark;
        allBookmarks.set(bookmark.bookmark_id, bookmark);
        renderReviewQueue();
    }
}

// -----------------------------------------------------------------------------
// UI Preset & Mode Toggles
// -----------------------------------------------------------------------------

function selectPreset(preset) {
    activePreset = preset;
    document.querySelectorAll('#presetButtons [data-preset]').forEach((btn) => {
        btn.setAttribute('aria-pressed', String(btn.dataset.preset === preset));
    });
    const roomSelect = document.getElementById('presetSelect');
    if (roomSelect && preset) roomSelect.value = preset;

    renderPresetDetails();
}

function renderPresetDetails() {
    const info = presetInfo[activePreset];
    if (!info) return;
    document.getElementById('lblRoomCode').textContent = info.room_code;
    document.getElementById('lblCameraId').textContent = info.camera_id;
    document.getElementById('lblCalibratedSeats').textContent = info.calibrated ? String(info.seat_count) : '0';
}

function selectMode(mode) {
    activeMode = mode;
    const btnLive = document.getElementById('btnModeLive');
    const btnReplay = document.getElementById('btnModeReplay');
    const modeText = document.getElementById('modeText');
    const modeBadge = document.getElementById('modeBadge');

    btnLive.setAttribute('aria-pressed', String(mode === 'LIVE'));
    btnReplay.setAttribute('aria-pressed', String(mode === 'REPLAY'));
    if (modeBadge) modeBadge.dataset.mode = mode;

    if (mode === 'LIVE') {
        modeText.textContent = 'LIVE AI ANALYSIS';
    } else {
        modeText.textContent = 'RECORDED REPLAY';
    }

    // A replay plays the video as it was rendered, so overlays cannot change
    ['chkDebugOverlay', 'chkBehaviorLabels'].forEach((id) => {
        const chk = document.getElementById(id);
        if (!chk) return;
        const label = chk.closest('label');
        chk.disabled = mode === 'REPLAY';
        if (label) {
            if (label.dataset.liveTitle === undefined) label.dataset.liveTitle = label.title || '';
            label.title = mode === 'REPLAY'
                ? 'Not available in Recorded Replay: the replay shows the video as it was recorded. Use Live Analysis.'
                : label.dataset.liveTitle;
            label.classList.toggle('opacity-50', mode === 'REPLAY');
            label.classList.toggle('cursor-not-allowed', mode === 'REPLAY');
        }
    });
}

// -----------------------------------------------------------------------------
// Playback Control Actions
// -----------------------------------------------------------------------------

async function startDemo() {
    const btnStart = document.getElementById('btnStart');
    if (btnStart) {
        btnStart.disabled = true;
        btnStart.classList.add('opacity-50');
    }
    try {
        const debug = document.getElementById('chkDebugOverlay').checked;
        const res = await apiFetch('/api/v1/demo/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                preset: activePreset,
                mode: activeMode,
                debug_overlay: debug,
                behavior_labels: document.getElementById('chkBehaviorLabels').checked,
            }),
        });
        const data = await res.json();
        if (!res.ok) {
            showDemoError(data.detail || `Could not start (HTTP ${res.status})`);
            return;
        }
        showDemoError(null);
        renderStatus(data);
        refreshStream();
        fetchEvents();
    } catch (err) {
        console.error('Failed to start demo:', err);
        showDemoError('Could not reach the VIGIL server.');
    } finally {
        setTimeout(() => {
            if (btnStart) {
                btnStart.disabled = false;
                btnStart.classList.remove('opacity-50');
            }
        }, 1500);
    }
}

async function pauseDemo() {
    const res = await apiFetch('/api/v1/demo/pause', { method: 'POST' });
    const data = await res.json();
    renderStatus(data);
}

async function resumeDemo() {
    const res = await apiFetch('/api/v1/demo/resume', { method: 'POST' });
    const data = await res.json();
    renderStatus(data);
}

async function stopDemo() {
    const res = await apiFetch('/api/v1/demo/stop', { method: 'POST' });
    const data = await res.json();
    renderStatus(data);
}

async function resetDemo() {
    const res = await apiFetch('/api/v1/demo/reset', { method: 'POST' });
    const data = await res.json();
    allIncidents.clear();
    allBookmarks.clear();
    activeSessionId = null;
    renderStatus(data);
    renderReviewQueue();
    refreshStream();
}

function refreshStream() {
    const img = document.getElementById('videoStreamImg');
    const placeholder = document.getElementById('videoPlaceholder');
    if (img) {
        img.src = authenticatedUrl('/api/v1/demo/stream?t=' + Date.now());
        if (placeholder) placeholder.style.display = 'none';
    }
}

function handleStreamError(img) {
    const placeholder = document.getElementById('videoPlaceholder');
    if (placeholder) placeholder.style.display = 'flex';
}

// -----------------------------------------------------------------------------
// API Polling & Telemetry Fetching
// -----------------------------------------------------------------------------

async function fetchPresets() {
    try {
        const res = await apiFetch('/api/v1/demo/presets');
        const presets = await res.json();
        const container = document.getElementById('presetButtons');
        const roomSelect = document.getElementById('presetSelect');
        // A few rooms fit as buttons; more rooms become a list so the toolbar keeps one row
        const asList = presets.length > ROOM_BUTTON_LIMIT;
        document.getElementById('presetSegment')?.classList.toggle('hidden', asList);
        roomSelect?.classList.toggle('hidden', !asList);
        if (container) {
            container.innerHTML = presets.map((p) => `
                <button type="button" data-preset="${p.name}" onclick="selectPreset('${p.name}')" aria-pressed="false"
                    class="vigil-btn vigil-btn--sm"></button>`).join('');
        }
        if (roomSelect) roomSelect.replaceChildren();
        presets.forEach((p) => {
            presetInfo[p.name] = p;
            // Seat counts come from the web calibration, never from fixed numbers
            const seats = p.calibrated ? `${p.seat_count} seats` : 'not calibrated';
            const hint = p.calibrated ? `${p.title} · ${seats}` : (p.calibration_error || 'Draw Seat ROIs on the calibration page');
            if (roomSelect) {
                const option = document.createElement('option');
                option.value = p.name;
                option.textContent = `${p.title} · ${seats}`;
                roomSelect.append(option);
            }
            const btn = container ? container.querySelector(`[data-preset="${p.name}"]`) : null;
            if (!btn) return;
            btn.textContent = p.calibrated ? `${p.title} · ${p.seat_count}` : `${p.title} · no seats`;
            btn.title = hint;
        });
        if (!activePreset || !presetInfo[activePreset]) activePreset = presets.length ? presets[0].name : null;
        selectPreset(activePreset);
    } catch (e) {}
}

async function toggleOverlay(change) {
    try {
        await apiFetch('/api/v1/demo/overlay', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(change),
        });
    } catch (e) {
        console.error('Failed to switch overlay:', e);
    }
}

function showDemoError(message) {
    const banner = document.getElementById('demoErrorBanner');
    const text = document.getElementById('demoErrorText');
    const link = document.getElementById('demoErrorLink');
    if (!banner || !text) return;
    if (!message) {
        banner.classList.add('hidden');
        return;
    }
    text.textContent = message;
    if (link) link.classList.toggle('hidden', !/calibrat|seat roi/i.test(message));
    banner.classList.remove('hidden');
}

async function fetchStatus() {
    try {
        const res = await apiFetch('/api/v1/demo/status');
        const data = await res.json();
        renderStatus(data);
    } catch (e) {}
}

async function fetchEvents() {
    try {
        const res = await apiFetch('/api/v1/demo/events');
        const events = await res.json();
        allIncidents.clear();
        events.forEach((ev) => allIncidents.set(ev.event_id, ev));
        renderReviewQueue();
    } catch (e) {}
}

async function fetchBookmarks() {
    if (!activeSessionId) return;
    try {
        const res = await apiFetch(`/api/v1/proctor/bookmarks?session_id=${encodeURIComponent(activeSessionId)}`);
        if (!res.ok) return;
        const bookmarks = await res.json();
        allBookmarks.clear();
        bookmarks.forEach((item) => allBookmarks.set(item.bookmark_id, item));
        renderReviewQueue();
    } catch (e) {}
}

function renderStatus(st) {
    if (!st) return;
    if (st.session_id && st.session_id !== activeSessionId) {
        activeSessionId = st.session_id;
        fetchBookmarks();
    }

    const stateText = document.getElementById('stateText');
    const stateBadge = document.getElementById('stateBadge');
    const fpsText = document.getElementById('fpsText');
    const lblFrameIdx = document.getElementById('lblFrameIdx');
    const lblTotalFrames = document.getElementById('lblTotalFrames');
    const lblTimeSec = document.getElementById('lblTimeSec');
    const progressBar = document.getElementById('progressBar');
    const lblOccupied = document.getElementById('lblOccupiedSeats');
    const lblSuspicious = document.getElementById('lblSuspiciousSeats');
    const lblIncidents = document.getElementById('lblIncidentCount');
    const placeholder = document.getElementById('videoPlaceholder');

    if (!behaviorLabelsSynced && typeof st.behavior_labels === 'boolean') {
        const chk = document.getElementById('chkBehaviorLabels');
        if (chk) chk.checked = st.behavior_labels;
        behaviorLabelsSynced = true;
    }
    if (stateText) stateText.textContent = st.state;
    if (st.state === 'ERROR' && st.last_error) {
        showDemoError(st.last_error);
    } else if (st.state === 'RUNNING') {
        showDemoError(null);
    }
    if (stateBadge) {
        stateBadge.dataset.state = st.state;
        if (st.state === 'RUNNING') {
            if (placeholder) placeholder.style.display = 'none';
        } else {
            if (st.state === 'IDLE' && placeholder) placeholder.style.display = 'flex';
        }
    }

    if (fpsText) {
        fpsText.textContent = `${st.processing_fps.toFixed(1)} FPS (${st.realtime_factor.toFixed(2)}x)`;
    }

    if (lblFrameIdx) lblFrameIdx.textContent = st.frame_index;
    if (lblTotalFrames) lblTotalFrames.textContent = st.total_frames;
    if (lblTimeSec) lblTimeSec.textContent = `${(st.source_timestamp_ms / 1000).toFixed(1)}s`;

    if (progressBar && st.total_frames > 0) {
        const pct = Math.min(100, Math.round((st.frame_index / st.total_frames) * 100));
        progressBar.style.width = pct + '%';
    }

    if (lblOccupied) lblOccupied.textContent = st.occupied_seats || '--';
    if (lblSuspicious) lblSuspicious.textContent = st.suspicious_seats || 0;
    if (lblIncidents) lblIncidents.textContent = st.emitted_review_incidents || allIncidents.size;
}

// -----------------------------------------------------------------------------
// Review Queue Rendering & Filtering
// -----------------------------------------------------------------------------

async function captureProctorFrame() {
    const button = document.getElementById('btnMarkFrame');
    const error = document.getElementById('captureError');
    if (button) button.disabled = true;
    if (error) error.classList.add('hidden');
    try {
        const response = await apiFetch('/api/v1/proctor/captures', { method: 'POST' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'No frame is available');
        pendingCapture = payload;
        document.getElementById('capturePreview').src = authenticatedUrl(payload.snapshot_url);
        document.getElementById('captureMeta').textContent = `Frame ${payload.frame_id} • ${(payload.source_timestamp_ms / 1000).toFixed(1)}s • token expires shortly`;
        document.getElementById('captureSubject').value = '';
        document.getElementById('captureNote').value = '';
        document.getElementById('captureModal').classList.remove('hidden');
    } catch (err) {
        if (error) {
            error.textContent = err.message;
            error.classList.remove('hidden');
        }
        window.alert(`Cannot mark frame: ${err.message}`);
    } finally {
        if (button) button.disabled = false;
    }
}

function closeCaptureModal() {
    document.getElementById('captureModal').classList.add('hidden');
    document.getElementById('capturePreview').src = '';
    pendingCapture = null;
}

async function saveProctorBookmark() {
    if (!pendingCapture) return;
    const button = document.getElementById('btnSaveBookmark');
    const error = document.getElementById('captureError');
    button.disabled = true;
    error.classList.add('hidden');
    try {
        const requestId = window.crypto && window.crypto.randomUUID
            ? window.crypto.randomUUID()
            : `bookmark-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const response = await apiFetch('/api/v1/proctor/bookmarks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                capture_id: pendingCapture.capture_id,
                request_id: requestId,
                subject_ref: document.getElementById('captureSubject').value.trim() || null,
                note: document.getElementById('captureNote').value.trim(),
                created_by: 'Lead_Proctor',
            }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Bookmark could not be saved');
        allBookmarks.set(payload.bookmark_id, payload);
        renderReviewQueue();
        closeCaptureModal();
    } catch (err) {
        error.textContent = err.message;
        error.classList.remove('hidden');
    } finally {
        button.disabled = false;
    }
}

function filterQueue(status) {
    activeFilter = status;
    document.querySelectorAll('.filter-btn').forEach((b) => {
        b.setAttribute('aria-pressed', String(b.dataset.filter === status));
    });
    renderReviewQueue();
}

function setQueueSort(sort) {
    activeSort = sort === 'priority' ? 'priority' : 'newest';
    renderReviewQueue();
}

// Source-video time of an item, for "newest first"
function queueItemTime(item) {
    const ms = item._source === 'MANUAL' ? item.source_timestamp_ms : item.first_seen_ms;
    return Number(ms || 0);
}

function queuePriority(item) {
    const value = Number(item.peak_risk_score ?? item.risk_score);
    return Number.isFinite(value) ? value : -1;
}

// "SEAT-ROOM-CHINA-03-15" -> "CHINA-03"
function roomLabelFromSeat(seatId) {
    const match = String(seatId || '').match(/^SEAT-(?:ROOM-)?(.+)-\d+$/);
    return match ? match[1] : '';
}

// Compact "when": behaviour start → alert, as in the review modal
function queueTimeText(item) {
    if (item._source === 'MANUAL') return `${(Number(item.source_timestamp_ms || 0) / 1000).toFixed(1)}s`;
    const flagged = Number(item.first_seen_ms || 0) / 1000;
    const hasStart = item.behavior_start_ms !== undefined && item.behavior_start_ms !== null;
    const start = hasStart ? Number(item.behavior_start_ms) / 1000 : flagged;
    return flagged - start >= 0.1 ? `${start.toFixed(1)}→${flagged.toFixed(1)}s` : `${flagged.toFixed(1)}s`;
}

const QUEUE_STATUS_LABEL = {
    PENDING: 'Pending',
    CONFIRMED: 'Confirmed',
    REJECTED: 'Rejected',
    INCONCLUSIVE: 'Unsure',
};

function renderReviewQueue() {
    const list = document.getElementById('reviewQueueGrid');
    const badgeCount = document.getElementById('queueBadgeCount');
    if (!list) return;

    const incidents = Array.from(allIncidents.values()).map((item) => ({ ...item, _source: 'AI' }));
    const bookmarks = Array.from(allBookmarks.values()).map((item) => ({
        ...item,
        _source: 'MANUAL',
        review_status: item.review_decision || item.review_status || 'PENDING',
    }));
    const items = [...incidents, ...bookmarks];
    const pendingCount = items.filter((item) => item.review_status === 'PENDING').length;
    if (badgeCount) badgeCount.textContent = `${pendingCount} Pending`;

    // Counts per decision for the current source filter
    const inSource = items.filter((item) => activeSourceFilter === 'ALL' || item._source === activeSourceFilter);
    document.querySelectorAll('[data-count-for]').forEach((el) => {
        const status = el.dataset.countFor;
        el.textContent = String(status === 'ALL' ? inSource.length : inSource.filter((item) => item.review_status === status).length);
    });

    const filtered = inSource
        .filter((item) => activeFilter === 'ALL' || item.review_status === activeFilter)
        .sort((left, right) => (activeSort === 'priority'
            ? queuePriority(right) - queuePriority(left) || queueItemTime(right) - queueItemTime(left)
            : queueItemTime(right) - queueItemTime(left)));

    if (filtered.length === 0) {
        list.innerHTML = `<div class="review-empty">${items.length
            ? `No incidents match this filter.`
            : 'No review incidents yet. Start the stream to begin monitoring.'}</div>`;
        return;
    }

    // Show the room on each row only when the queue spans several rooms
    const rooms = new Set(items.map((item) => roomLabelFromSeat(item.seat_id || item.subject_ref)).filter(Boolean));
    const showRoom = rooms.size > 1;

    list.innerHTML = filtered
        .map((ev) => {
            const isManual = ev._source === 'MANUAL';
            const itemId = escapeHtml(String(isManual ? ev.bookmark_id : ev.event_id));
            // Show only the score the core recorded; never invent a default priority.
            const priority = queuePriority(ev);
            const score = isManual ? '' : (priority >= 0 ? String(Math.round(priority)) : '--');
            const level = priority < 0 ? 'none' : (priority >= 85 ? 'high' : 'medium');
            const seatRef = ev.subject_ref || ev.seat_id || '';
            const seatShort = seatRef ? shortSeatLabel(seatRef) : '—';
            const room = roomLabelFromSeat(seatRef);
            const title = isManual ? (ev.note || 'Proctor-marked observation') : formatBehaviorLabel(ev.primary_pattern || ev.behavior);
            const meta = [
                showRoom && room ? room : null,
                queueTimeText(ev),
                isManual ? `frame ${ev.frame_id}` : `x${ev.occurrence_count || 1}`,
                isManual ? 'proctor' : 'AI',
            ].filter(Boolean).join(' · ');
            const status = ev.review_status || 'PENDING';
            const tooltip = isManual ? `${seatRef || 'No seat'} · ${title}` : `${seatRef} · ${title} · ${incidentTimeText(ev, false)}`;
            const current = (isManual ? ev.bookmark_id : ev.event_id) === activeEventId;
            return `
                <button type="button" role="listitem" class="queue-row" data-item-id="${itemId}" data-item-source="${ev._source}"
                    data-severity="${isManual ? 'MANUAL' : escapeHtml(ev.severity || 'MEDIUM')}" data-status="${escapeHtml(status)}"
                    aria-current="${current}" title="${escapeHtml(tooltip)}">
                    <span class="queue-seat">${room ? `<small>${escapeHtml(room)}</small>` : ''}<strong>${escapeHtml(seatShort)}</strong></span>
                    <span class="queue-main">
                        <span class="queue-title">${escapeHtml(title)}</span>
                        <span class="queue-meta">${escapeHtml(meta)}</span>
                    </span>
                    <span class="queue-side">
                        ${score ? `<span class="queue-score" data-level="${level}" title="Review priority">${score}</span>` : ''}
                        <span class="queue-status" data-status="${escapeHtml(status)}">${escapeHtml(QUEUE_STATUS_LABEL[status] || status)}</span>
                    </span>
                </button>`;
        })
        .join('');
    list.querySelectorAll('.queue-row[data-item-id]').forEach((row) => {
        row.addEventListener('click', () => openReviewItem(row.dataset.itemSource, row.dataset.itemId));
    });
}

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

// When the behaviour began, when the AI flagged it, and until when it re-flagged
function incidentTimeText(ev, compact) {
    const flagged = Number(ev.first_seen_ms || 0) / 1000;
    const hasStart = ev.behavior_start_ms !== undefined && ev.behavior_start_ms !== null;
    const start = hasStart ? Number(ev.behavior_start_ms) / 1000 : flagged;
    const last = Number(ev.last_seen_ms || ev.first_seen_ms || 0) / 1000;
    const parts = [];
    if (hasStart && flagged - start >= 0.1) {
        parts.push(`Behavior ${start.toFixed(1)}s → ${flagged.toFixed(1)}s`);
        parts.push(`flagged at ${flagged.toFixed(1)}s`);
    } else {
        parts.push(`Flagged at ${flagged.toFixed(1)}s`);
    }
    if ((ev.occurrence_count || 1) > 1 && last - flagged >= 0.1) {
        parts.push(`re-flagged until ${last.toFixed(1)}s`);
    }
    if (!compact && hasStart && flagged - start >= 0.1) {
        parts.push(`${(flagged - start).toFixed(1)}s before the alert`);
    }
    return parts.join(' · ');
}

function formatBehaviorLabel(raw) {
    if (!raw) return 'Suspicious Behavior';
    return raw
        .replace(/_/g, ' ')
        .toLowerCase()
        .replace(/\b\w/g, (l) => l.toUpperCase());
}

// -----------------------------------------------------------------------------
// Human Review Modal Adjudication
// -----------------------------------------------------------------------------

function openReviewItem(source, itemId) {
    if (source === 'MANUAL') {
        openBookmarkModal(itemId);
    } else {
        openReviewModal(itemId);
    }
    renderReviewQueue();
}

async function openBookmarkModal(bookmarkId) {
    const bookmark = allBookmarks.get(bookmarkId);
    if (!bookmark) return;
    activeItemType = 'MANUAL';
    activeEventId = bookmarkId;
    selectedDecision = bookmark.review_decision && bookmark.review_decision !== 'PENDING'
        ? bookmark.review_decision
        : 'INCONCLUSIVE';

    document.getElementById('modalSeatBadge').textContent = bookmark.subject_ref || 'UNASSIGNED';
    document.getElementById('modalPatternTitle').textContent = 'Proctor-marked observation';
    document.getElementById('modalSeverityBadge').textContent = 'HUMAN MARK';
    document.getElementById('modalBehaviorText').textContent = 'Manual observation bookmark';
    document.getElementById('modalRiskScore').textContent = 'Not an AI score';
    document.getElementById('modalOccurrence').textContent = 'x1';
    document.getElementById('modalTimeRange').textContent = `${(Number(bookmark.source_timestamp_ms || 0) / 1000).toFixed(1)}s • frame ${bookmark.frame_id}`;
    document.getElementById('modalSha256').textContent = bookmark.sha256
        ? 'HASH AVAILABLE / NOT CHECKED'
        : 'HASH NOT AVAILABLE';
    if (bookmark.sha256) {
        try {
            const integrityResponse = await apiFetch(`/api/v1/proctor/bookmarks/${encodeURIComponent(bookmarkId)}/integrity`);
            if (integrityResponse.ok) {
                const integrity = await integrityResponse.json();
                const labels = {
                    HASH_VERIFIED: 'HASH VERIFIED',
                    HASH_MISMATCH: 'HASH MISMATCH',
                    HASH_NOT_AVAILABLE: 'HASH NOT AVAILABLE',
                };
                document.getElementById('modalSha256').textContent = labels[integrity.hash_status] || 'HASH AVAILABLE / NOT CHECKED';
            }
        } catch (err) {
            console.debug('Bookmark integrity check unavailable:', err);
        }
    }
    const cuesList = document.getElementById('modalSupportingCues');
    const cues = [
        `Source: ${bookmark.source_kind || 'VIDEO'}`,
        `Evidence: ${bookmark.evidence_status || 'PENDING'}`,
    ];
    cuesList.replaceChildren(...cues.map((cue) => {
        const item = document.createElement('li');
        item.textContent = cue;
        return item;
    }));
    document.getElementById('modalVideoSection').classList.add('hidden');
    clearVideoPlayer(document.getElementById('modalVideoPlayer'));
    activeEvidenceUrls = {
        overall: bookmark.snapshot_url ? authenticatedUrl(bookmark.snapshot_url) : '',
        crop: bookmark.crop_url ? authenticatedUrl(bookmark.crop_url) : '',
    };
    evidenceZoom = 1.0;
    // A proctor bookmark has no AI-flagged person to highlight
    activeFocus = { focus: null, label: '' };
    document.getElementById('btnViewSuspect').classList.add('hidden');
    document.getElementById('btnViewCrop').classList.toggle('hidden', !activeEvidenceUrls.crop);
    setEvidenceView('overall');
    document.getElementById('modalReasonSelect').value = 'NONE';
    document.getElementById('modalNotesText').value = bookmark.note || '';
    updateDecisionButtons();
    document.getElementById('reviewModal').classList.remove('hidden');
}

async function openReviewModal(eventId) {
    activeItemType = 'AI';
    activeEventId = eventId;
    const ev = allIncidents.get(eventId);
    if (!ev) return;

    selectedDecision = ev.review_status && ev.review_status !== 'PENDING' ? ev.review_status : 'CONFIRMED';

    document.getElementById('modalSeatBadge').textContent = `SEAT ${ev.seat_id || '--'}`;
    document.getElementById('modalPatternTitle').textContent = formatBehaviorLabel(ev.primary_pattern || ev.behavior);
    document.getElementById('modalSeverityBadge').textContent = ev.severity || 'MEDIUM';
    document.getElementById('modalBehaviorText').textContent = ev.primary_pattern || ev.behavior;
    const modalRisk = ev.peak_risk_score ?? ev.risk_score;
    document.getElementById('modalRiskScore').textContent = Number.isFinite(Number(modalRisk)) ? `${Math.round(Number(modalRisk))} / 100` : 'Not recorded';
    document.getElementById('modalOccurrence').textContent = `x${ev.occurrence_count || 1}`;

    document.getElementById('modalTimeRange').textContent = incidentTimeText(ev, false);

    const hashEl = document.getElementById('modalSha256');
    const hashLabels = {
        HASH_VERIFIED: 'HASH VERIFIED',
        HASH_MISMATCH: 'HASH MISMATCH',
        HASH_AVAILABLE_NOT_CHECKED: 'HASH AVAILABLE / NOT CHECKED',
        HASH_NOT_AVAILABLE: 'HASH NOT AVAILABLE',
    };
    hashEl.textContent = hashLabels[ev.hash_status] || (ev.video_sha256 ? 'HASH AVAILABLE / NOT CHECKED' : 'HASH NOT AVAILABLE');
    try {
        const integrityRes = await apiFetch(`/api/v1/demo/events/${encodeURIComponent(eventId)}/integrity`);
        if (integrityRes.ok) {
            const integrity = await integrityRes.json();
            ev.hash_status = integrity.hash_status;
            hashEl.textContent = hashLabels[integrity.hash_status] || 'HASH NOT AVAILABLE';
        }
    } catch (err) {
        console.debug('Evidence integrity check unavailable:', err);
    }

    // Supporting cues
    const cuesList = document.getElementById('modalSupportingCues');
    const cues = ev.metadata && ev.metadata.supporting_cues ? ev.metadata.supporting_cues : [`Frequency: x${ev.occurrence_count || 1} repetitions`];
    cuesList.replaceChildren(...cues.map((cue) => {
        const item = document.createElement('li');
        item.textContent = String(cue);
        return item;
    }));

    // Video & Snapshot
    const videoPlayer = document.getElementById('modalVideoPlayer');
    const snapshotImg = document.getElementById('modalSnapshotImg');
    const videoStatus = document.getElementById('modalVideoStatus');
    const videoDownload = document.getElementById('modalVideoDownload');
    document.getElementById('modalVideoSection').classList.remove('hidden');

    if (ev.video_url) {
        const videoUrl = authenticatedUrl(ev.video_url);
        videoStatus.textContent = 'Preparing browser-compatible evidence video…';
        videoStatus.classList.remove('hidden');
        videoDownload.href = videoUrl;
        videoDownload.classList.remove('hidden');
        videoPlayer.src = videoUrl;
        videoPlayer.load();
        videoPlayer.play().catch(() => {
            // Closed or switched to another incident meanwhile
            if (videoPlayer.getAttribute('src') !== videoUrl) return;
            videoStatus.textContent = 'Evidence ready. Press Play to start.';
            videoStatus.classList.remove('hidden');
        });
    } else {
        clearVideoPlayer(videoPlayer);
        videoDownload.href = '#';
        videoDownload.classList.add('hidden');
        videoStatus.textContent = ev.evidence_error || (
            ev.evidence_status === 'PENDING'
                ? 'The evidence clip is still being recorded (about 5 seconds after the incident). Reopen this review in a moment.'
                : 'No video clip is available for this incident.'
        );
        videoStatus.classList.remove('hidden');
    }

    if (ev.snapshot_url) {
        activeEvidenceUrls = { overall: authenticatedUrl(ev.snapshot_url), crop: '' };
        evidenceZoom = 1.0;
        document.getElementById('btnViewCrop').classList.add('hidden');
        setEvidenceView('overall');
    } else {
        activeEvidenceUrls = { overall: '', crop: '' };
        snapshotImg.src = '';
    }

    // Where to look: flagged student + seat ROI, drawn over clip and snapshot
    activeFocus = {
        focus: (ev.metadata && ev.metadata.focus) || null,
        label: `${shortSeatLabel(ev.seat_id)} · ${formatBehaviorLabel(ev.primary_pattern || ev.behavior)}`,
    };
    const canZoomToSuspect = Boolean(ev.snapshot_url && focusBox(activeFocus.focus));
    document.getElementById('btnViewSuspect').classList.toggle('hidden', !canZoomToSuspect);
    if (canZoomToSuspect) {
        setEvidenceView('suspect');
    } else {
        renderActiveFocus();
    }

    // Reason & notes
    document.getElementById('modalReasonSelect').value = ev.decision_reason || 'NONE';
    document.getElementById('modalNotesText').value = ev.reviewer_notes || '';

    updateDecisionButtons();
    document.getElementById('reviewModal').classList.remove('hidden');
    // Zoom and on-screen label sizes need the visible layout, so apply them once the modal is shown
    requestAnimationFrame(() => {
        renderActiveFocus();
        if (document.getElementById('modalSnapshotImg').dataset.view === 'suspect') zoomToSuspect();
    });
}

function closeReviewModal() {
    const videoPlayer = document.getElementById('modalVideoPlayer');
    if (videoPlayer) {
        videoPlayer.pause();
        clearVideoPlayer(videoPlayer);
    }
    const videoDownload = document.getElementById('modalVideoDownload');
    if (videoDownload) {
        videoDownload.href = '#';
        videoDownload.classList.add('hidden');
    }
    document.getElementById('reviewModal').classList.add('hidden');
    activeFocus = { focus: null, label: '' };
    renderActiveFocus();
    activeEvidenceUrls = { overall: '', crop: '' };
    evidenceZoom = 1.0;
    activeEventId = null;
    activeItemType = 'AI';
    renderReviewQueue();
}

function setEvidenceView(view) {
    const image = document.getElementById('modalSnapshotImg');
    const source = view === 'crop' ? activeEvidenceUrls.crop : activeEvidenceUrls.overall;
    image.src = source || activeEvidenceUrls.overall || '';
    image.dataset.view = view;
    evidenceZoom = 1.0;
    document.getElementById('modalSnapshotStage').style.transform = 'none';
    document.getElementById('btnViewOverall').setAttribute('aria-pressed', String(view === 'overall'));
    document.getElementById('btnViewCrop').setAttribute('aria-pressed', String(view === 'crop'));
    document.getElementById('btnViewSuspect').setAttribute('aria-pressed', String(view === 'suspect'));
    renderActiveFocus();
    if (view === 'suspect') {
        // Wait for layout so the viewport size is known
        requestAnimationFrame(zoomToSuspect);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const image = document.getElementById('modalSnapshotImg');
    if (image) {
        image.addEventListener('load', () => {
            if (image.dataset.view === 'suspect') zoomToSuspect();
        });
    }
});

function focusBox(focus) {
    if (!focus || !Array.isArray(focus.frame_size)) return null;
    if (Array.isArray(focus.person_bbox)) return focus.person_bbox;
    const polygon = Array.isArray(focus.seat_polygon) ? focus.seat_polygon : [];
    if (polygon.length < 3) return null;
    const xs = polygon.map((p) => p[0]);
    const ys = polygon.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function zoomToSuspect() {
    const focus = activeFocus.focus;
    const box = focusBox(focus);
    const stage = document.getElementById('modalSnapshotStage');
    if (!box || !stage) return;
    const [w, h] = focus.frame_size;
    const stageW = stage.clientWidth;
    const stageH = stage.clientHeight;
    if (!stageW || !stageH) return;
    // Where the frame is drawn inside the stage (object-contain letterboxing)
    const fit = Math.min(stageW / w, stageH / h);
    const offX = (stageW - w * fit) / 2;
    const offY = (stageH - h * fit) / 2;
    const cx = offX + ((box[0] + box[2]) / 2) * fit;
    const cy = offY + ((box[1] + box[3]) / 2) * fit;
    // Show the student about 2.5x their size so neighbours stay in view as context
    const zoom = Math.max(1, Math.min(
        6,
        stageW / ((box[2] - box[0]) * fit * 2.5),
        stageH / ((box[3] - box[1]) * fit * 2.5),
    ));
    evidenceZoom = zoom;
    stage.style.transform = `translate(${stageW / 2 - cx * zoom}px, ${stageH / 2 - cy * zoom}px) scale(${zoom})`;
    if (document.getElementById('chkFocusOverlay')?.checked !== false) {
        drawFocusOverlay(document.getElementById('modalSnapshotFocus'), focus, activeFocus.label);
    }
}

// -----------------------------------------------------------------------------
// Suspect highlight over review evidence (display only; files stay unmodified)
// -----------------------------------------------------------------------------

let activeFocus = { focus: null, label: '' };

function shortSeatLabel(seatId) {
    const digits = String(seatId || '').match(/(\d+)(?!.*\d)/);
    return digits ? `S${digits[1].padStart(2, '0')}` : String(seatId || 'Seat');
}

function renderActiveFocus() {
    const enabled = document.getElementById('chkFocusOverlay')?.checked !== false;
    // The overall snapshot is the full camera frame; a seat crop has other coordinates.
    const snapshotIsFullFrame = document.getElementById('modalSnapshotImg')?.dataset.view !== 'crop';  // overall + suspect
    drawFocusOverlay(document.getElementById('modalVideoFocus'), enabled ? activeFocus.focus : null, activeFocus.label);
    drawFocusOverlay(
        document.getElementById('modalSnapshotFocus'),
        enabled && snapshotIsFullFrame ? activeFocus.focus : null,
        activeFocus.label,
    );
}

function drawFocusOverlay(svg, focus, label) {
    if (!svg) return;
    const size = focus && Array.isArray(focus.frame_size) ? focus.frame_size : null;
    const polygon = focus && Array.isArray(focus.seat_polygon) ? focus.seat_polygon : [];
    if (!size || (!focus.person_bbox && polygon.length < 3)) {
        svg.replaceChildren();
        svg.classList.add('hidden');
        return;
    }
    const [w, h] = size;
    const ns = 'http://www.w3.org/2000/svg';
    const el = (tag, attrs) => {
        const node = document.createElementNS(ns, tag);
        Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
        return node;
    };

    // Region to keep bright: the person's box, or the seat ROI's bounds without one
    let box = focus.person_bbox;
    if (!box) {
        const xs = polygon.map((p) => p[0]);
        const ys = polygon.map((p) => p[1]);
        box = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }
    // Measure how many screen pixels one frame pixel occupies (includes any zoom),
    // so lines and the label keep a readable on-screen size at every view.
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.classList.remove('hidden');
    const rect = svg.getBoundingClientRect();
    const screenScale = rect.width && rect.height ? Math.min(rect.width / w, rect.height / h) : 0;
    const px = (screenPx, fallback) => (screenScale ? screenPx / screenScale : fallback);
    const pad = px(4, Math.max(w, h) * 0.01);
    const [x1, y1, x2, y2] = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad];
    const stroke = px(2, Math.max(2, w / 360));
    const maskId = `${svg.id}-mask`;

    const defs = el('defs', {});
    const mask = el('mask', { id: maskId });
    mask.append(
        el('rect', { x: 0, y: 0, width: w, height: h, fill: 'white' }),
        el('rect', { x: x1, y: y1, width: x2 - x1, height: y2 - y1, rx: stroke * 2, fill: 'black' }),
    );
    defs.append(mask);

    const children = [defs, el('rect', { x: 0, y: 0, width: w, height: h, fill: 'black', 'fill-opacity': 0.5, mask: `url(#${maskId})` })];
    if (polygon.length >= 3) {
        children.push(el('polygon', {
            points: polygon.map((p) => p.join(',')).join(' '),
            fill: 'none', stroke: '#fbbf24', 'stroke-width': stroke, 'stroke-dasharray': `${stroke * 4} ${stroke * 3}`,
        }));
    }
    children.push(el('rect', {
        x: x1, y: y1, width: x2 - x1, height: y2 - y1, rx: stroke * 2,
        fill: 'none', stroke: '#ef4444', 'stroke-width': stroke * 1.6,
    }));

    if (label) {
        const fontSize = px(13, Math.max(14, w / 70));
        const textY = y1 - fontSize * 0.6 > fontSize ? y1 - fontSize * 0.6 : y2 + fontSize * 1.3;
        const text = el('text', {
            x: x1, y: textY, fill: '#ffffff', 'font-size': fontSize, 'font-family': 'monospace', 'font-weight': 700,
            stroke: '#000000', 'stroke-width': fontSize / 6, 'paint-order': 'stroke',
        });
        text.textContent = label;
        children.push(text);
    }
    svg.replaceChildren(...children);
}

function zoomEvidence(delta) {
    evidenceZoom = Math.min(3.0, Math.max(0.5, evidenceZoom + delta));
    // Manual zoom is centred on the image
    const stage = document.getElementById('modalSnapshotStage');
    const dx = (stage.clientWidth * (1 - evidenceZoom)) / 2;
    const dy = (stage.clientHeight * (1 - evidenceZoom)) / 2;
    stage.style.transform = `translate(${dx}px, ${dy}px) scale(${evidenceZoom})`;
}

function selectDecision(decision) {
    selectedDecision = decision;
    updateDecisionButtons();
}

function updateDecisionButtons() {
    const btnConf = document.getElementById('btnDecConfirm');
    const btnRej = document.getElementById('btnDecReject');
    const btnInc = document.getElementById('btnDecInconclusive');

    btnConf.setAttribute('aria-pressed', String(selectedDecision === 'CONFIRMED'));
    btnRej.setAttribute('aria-pressed', String(selectedDecision === 'REJECTED'));
    btnInc.setAttribute('aria-pressed', String(selectedDecision === 'INCONCLUSIVE'));
}

function filterQueueSource(source) {
    activeSourceFilter = source;
    document.querySelectorAll('.source-filter-btn').forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.sourceFilter === source));
    });
    renderReviewQueue();
}

function exportProctorReview(format) {
    if (!activeSessionId) {
        window.alert('Start a Classroom session before exporting review data.');
        return;
    }
    window.location.href = authenticatedUrl(`/api/v1/proctor/sessions/${encodeURIComponent(activeSessionId)}/export?format=${encodeURIComponent(format)}`);
}

async function submitHumanReview() {
    if (!activeEventId) return;

    const reason = document.getElementById('modalReasonSelect').value;
    const notes = document.getElementById('modalNotesText').value;

    const endpoint = activeItemType === 'MANUAL'
        ? `/api/v1/proctor/bookmarks/${encodeURIComponent(activeEventId)}/review`
        : `/api/v1/demo/events/${encodeURIComponent(activeEventId)}/review`;
    const body = activeItemType === 'MANUAL'
        ? {
            decision: selectedDecision,
            reason_code: reason,
            note: notes,
            reviewer_id: 'Lead_Proctor',
        }
        : {
            decision: selectedDecision,
            reason_code: reason,
            notes: notes,
            reviewer_id: 'Lead_Proctor',
        };
    const res = await apiFetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });

    if (res.ok) {
        if (activeItemType === 'MANUAL' && allBookmarks.has(activeEventId)) {
            const updated = await res.json();
            allBookmarks.set(activeEventId, updated);
        } else if (allIncidents.has(activeEventId)) {
            const ev = allIncidents.get(activeEventId);
            ev.review_status = selectedDecision;
            ev.decision_reason = reason;
            ev.reviewer_notes = notes;
        }
        renderReviewQueue();
        closeReviewModal();
    }
}
