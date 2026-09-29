/**
 * VIGIL AI SRS v2.0 — Competition Demo Frontend Controller
 * Handles live MJPEG stream, WebSocket telemetry, Review Queue, and Human-in-the-Loop Adjudication.
 */

let activePreset = 'india';
let activeMode = 'LIVE';
let activeFilter = 'ALL';
let activeSourceFilter = 'ALL';
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
    const btnIndia = document.getElementById('btnPresetIndia');
    const btnStudent = document.getElementById('btnPresetStudent');

    btnIndia.setAttribute('aria-pressed', String(preset === 'india'));
    btnStudent.setAttribute('aria-pressed', String(preset === 'student'));

    if (preset === 'india') {
        document.getElementById('lblRoomCode').textContent = 'ROOM-CALIB-01';
        document.getElementById('lblCameraId').textContent = 'CAM-CALIB-01';
        document.getElementById('lblCalibratedSeats').textContent = '21';
    } else {
        document.getElementById('lblRoomCode').textContent = 'ROOM-STUDENT-01';
        document.getElementById('lblCameraId').textContent = 'CAM-STUDENT-01';
        document.getElementById('lblCalibratedSeats').textContent = '12';
    }
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
            }),
        });
        const data = await res.json();
        renderStatus(data);
        refreshStream();
        fetchEvents();
    } catch (err) {
        console.error('Failed to start demo:', err);
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
        // Presets populated
    } catch (e) {}
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

    if (stateText) stateText.textContent = st.state;
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

function renderReviewQueue() {
    const grid = document.getElementById('reviewQueueGrid');
    const badgeCount = document.getElementById('queueBadgeCount');
    if (!grid) return;

    const incidents = Array.from(allIncidents.values()).map((item) => ({ ...item, _source: 'AI' }));
    const bookmarks = Array.from(allBookmarks.values()).map((item) => ({
        ...item,
        _source: 'MANUAL',
        review_status: item.review_decision || item.review_status || 'PENDING',
    }));
    const items = [...incidents, ...bookmarks].sort((left, right) => {
        const leftTime = left.created_at || left.captured_at || '';
        const rightTime = right.created_at || right.captured_at || '';
        return rightTime.localeCompare(leftTime);
    });
    const pendingCount = items.filter((item) => item.review_status === 'PENDING').length;
    if (badgeCount) badgeCount.textContent = `${pendingCount} Pending`;

    const filtered = items.filter((item) => {
        const statusMatch = activeFilter === 'ALL' || item.review_status === activeFilter;
        const sourceMatch = activeSourceFilter === 'ALL' || item._source === activeSourceFilter;
        return statusMatch && sourceMatch;
    });

    if (filtered.length === 0) {
        grid.innerHTML = `
            <div class="col-span-full py-10 flex flex-col items-center justify-center text-gray-500 font-mono text-xs gap-2">
                <svg class="w-8 h-8 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                <span>No incidents matching filter [${escapeHtml(activeFilter)}].</span>
            </div>
        `;
        return;
    }

    grid.innerHTML = filtered
        .map((ev) => {
            const isManual = ev._source === 'MANUAL';
            const risk = Math.round(ev.peak_risk_score || ev.risk_score || 75);
            const occ = ev.occurrence_count || 1;
            const pattern = escapeHtml(isManual ? (ev.note || 'Proctor-marked observation') : formatBehaviorLabel(ev.primary_pattern || ev.behavior));
            const seat = escapeHtml(ev.subject_ref || ev.seat_id || 'UNASSIGNED');
            const itemId = escapeHtml(String(isManual ? ev.bookmark_id : ev.event_id));
            const severity = escapeHtml(isManual ? 'HUMAN MARK' : (ev.severity || 'MEDIUM'));
            const sourceSeconds = isManual
                ? Number(ev.source_timestamp_ms || 0) / 1000
                : Number(ev.first_seen_ms || 0) / 1000;
            const firstSeen = sourceSeconds.toFixed(1);
            const lastSeen = isManual
                ? firstSeen
                : (ev.last_seen_ms ? ev.last_seen_ms / 1000 : sourceSeconds).toFixed(1);

            let statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-amber-500/20 text-amber-300 border border-amber-500/40">PENDING REVIEW</span>`;
            if (ev.review_status === 'CONFIRMED') {
                statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">CONFIRMED</span>`;
            } else if (ev.review_status === 'REJECTED') {
                statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-gray-800 text-gray-400 border border-gray-700">REJECTED</span>`;
            } else if (ev.review_status === 'INCONCLUSIVE') {
                statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-purple-500/20 text-purple-300 border border-purple-500/40">INCONCLUSIVE</span>`;
            }

            const sevBadge = isManual
                ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
                : ev.severity === 'HIGH'
                    ? 'bg-red-500/20 text-red-300 border-red-500/40'
                    : 'bg-amber-500/20 text-amber-300 border-amber-500/40';

            const sourceBadge = isManual ? 'PROCTOR' : 'AI SIGNAL';
            const footerMetric = isManual
                ? `Evidence: <strong class="${ev.evidence_status === 'READY' ? 'text-emerald-400' : 'text-amber-400'}">${escapeHtml(ev.evidence_status || 'PENDING')}</strong>`
                : `Review Priority: <strong class="text-red-400">${risk}/100</strong>`;

            return `
                <div class="incident-card bg-gray-950/80 border border-gray-800/90 rounded-xl p-4 flex flex-col justify-between space-y-3 cursor-pointer" data-item-id="${itemId}" data-item-source="${ev._source}">
                    <div class="flex items-start justify-between gap-2">
                        <div class="flex items-center gap-2">
                            <span class="px-2 py-1 rounded-md bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 text-xs font-bold font-mono">[${seat}]</span>
                            <span class="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${sevBadge} border">${severity}</span>
                            <span class="text-[10px] font-mono text-gray-500">${sourceBadge}</span>
                        </div>
                        ${statusBadge}
                    </div>

                    <div>
                        <div class="text-sm font-bold text-gray-100">${pattern}</div>
                        <div class="text-xs text-gray-400 font-mono mt-1 flex items-center gap-2">
                            <span>Time: ${firstSeen}s – ${lastSeen}s</span>
                            <span>&bull;</span>
                            <span class="text-amber-300 font-semibold">${isManual ? `Frame ${escapeHtml(ev.frame_id)}` : `x${occ} Occurrences`}</span>
                        </div>
                    </div>

                    <div class="pt-2 border-t border-gray-900 flex items-center justify-between text-xs font-mono">
                        <span class="text-gray-400">${footerMetric}</span>
                        <button class="vigil-btn vigil-btn--primary vigil-btn--sm px-2.5 py-1 rounded bg-gray-800 hover:bg-cyan-600 text-gray-200 hover:text-white transition text-xs font-semibold">
                            Review &rarr;
                        </button>
                    </div>
                </div>
            `;
        })
        .join('');
    grid.querySelectorAll('.incident-card[data-item-id]').forEach((card) => {
        card.addEventListener('click', () => openReviewItem(card.dataset.itemSource, card.dataset.itemId));
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
    document.getElementById('modalVideoPlayer').src = '';
    activeEvidenceUrls = {
        overall: bookmark.snapshot_url ? authenticatedUrl(bookmark.snapshot_url) : '',
        crop: bookmark.crop_url ? authenticatedUrl(bookmark.crop_url) : '',
    };
    evidenceZoom = 1.0;
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
    document.getElementById('modalRiskScore').textContent = `${Math.round(ev.peak_risk_score || ev.risk_score || 80)} / 100`;
    document.getElementById('modalOccurrence').textContent = `x${ev.occurrence_count || 1}`;

    const firstSeen = (ev.first_seen_ms ? ev.first_seen_ms / 1000 : 0).toFixed(1);
    const lastSeen = (ev.last_seen_ms ? ev.last_seen_ms / 1000 : firstSeen).toFixed(1);
    document.getElementById('modalTimeRange').textContent = `${firstSeen}s – ${lastSeen}s (${((ev.last_seen_ms || 0) - (ev.first_seen_ms || 0)) / 1000}s span)`;

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
            videoStatus.textContent = 'Evidence ready. Press Play to start.';
            videoStatus.classList.remove('hidden');
        });
    } else {
        videoPlayer.src = '';
        videoDownload.href = '#';
        videoDownload.classList.add('hidden');
        videoStatus.textContent = ev.evidence_error || 'No video clip is available for this incident.';
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

    // Reason & notes
    document.getElementById('modalReasonSelect').value = ev.decision_reason || 'NONE';
    document.getElementById('modalNotesText').value = ev.reviewer_notes || '';

    updateDecisionButtons();
    document.getElementById('reviewModal').classList.remove('hidden');
}

function closeReviewModal() {
    const videoPlayer = document.getElementById('modalVideoPlayer');
    if (videoPlayer) {
        videoPlayer.pause();
        videoPlayer.src = '';
    }
    const videoDownload = document.getElementById('modalVideoDownload');
    if (videoDownload) {
        videoDownload.href = '#';
        videoDownload.classList.add('hidden');
    }
    document.getElementById('reviewModal').classList.add('hidden');
    activeEvidenceUrls = { overall: '', crop: '' };
    evidenceZoom = 1.0;
    activeEventId = null;
    activeItemType = 'AI';
}

function setEvidenceView(view) {
    const image = document.getElementById('modalSnapshotImg');
    const target = activeEvidenceUrls[view] || activeEvidenceUrls.overall || '';
    image.src = target;
    image.dataset.view = view;
    evidenceZoom = 1.0;
    image.style.transform = 'scale(1)';
    document.getElementById('btnViewOverall').setAttribute('aria-pressed', String(view === 'overall'));
    document.getElementById('btnViewCrop').setAttribute('aria-pressed', String(view === 'crop'));
}

function zoomEvidence(delta) {
    evidenceZoom = Math.min(3.0, Math.max(0.5, evidenceZoom + delta));
    document.getElementById('modalSnapshotImg').style.transform = `scale(${evidenceZoom})`;
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
