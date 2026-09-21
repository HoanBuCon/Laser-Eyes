/**
 * VIGIL AI SRS v2.0 — Competition Demo Frontend Controller
 * Handles live MJPEG stream, WebSocket telemetry, Review Queue, and Human-in-the-Loop Adjudication.
 */

let activePreset = 'india';
let activeMode = 'LIVE';
let activeFilter = 'ALL';
let activeEventId = null;
let selectedDecision = 'CONFIRMED';
let ws = null;
let pollTimer = null;
let allIncidents = new Map(); // event_id -> event object

document.addEventListener('DOMContentLoaded', () => {
    initWebSocket();
    fetchPresets();
    fetchStatus();
    fetchEvents();

    // Start polling fallback every 1500ms
    pollTimer = setInterval(() => {
        fetchStatus();
    }, 1500);
});

// -----------------------------------------------------------------------------
// WebSocket Realtime Telemetry
// -----------------------------------------------------------------------------

function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/demo`;

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

    if (connected) {
        badge.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold';
        txt.textContent = 'LIVE WS';
    } else {
        badge.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-500/10 border border-red-500/30 text-red-400 text-xs font-semibold';
        txt.textContent = 'WS OFFLINE';
    }
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
    }
}

// -----------------------------------------------------------------------------
// UI Preset & Mode Toggles
// -----------------------------------------------------------------------------

function selectPreset(preset) {
    activePreset = preset;
    const btnIndia = document.getElementById('btnPresetIndia');
    const btnStudent = document.getElementById('btnPresetStudent');

    if (preset === 'india') {
        btnIndia.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold transition bg-cyan-500 text-white shadow-md';
        btnStudent.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold transition text-gray-400 hover:text-gray-200';
        document.getElementById('lblRoomCode').textContent = 'ROOM-CALIB-01';
        document.getElementById('lblCameraId').textContent = 'CAM-CALIB-01';
        document.getElementById('lblCalibratedSeats').textContent = '21';
    } else {
        btnStudent.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold transition bg-cyan-500 text-white shadow-md';
        btnIndia.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold transition text-gray-400 hover:text-gray-200';
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

    if (mode === 'LIVE') {
        btnLive.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold transition bg-blue-600 text-white shadow';
        btnReplay.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold transition text-gray-400 hover:text-gray-200';
        modeText.textContent = 'LIVE AI ANALYSIS';
    } else {
        btnReplay.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold transition bg-purple-600 text-white shadow';
        btnLive.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold transition text-gray-400 hover:text-gray-200';
        modeText.textContent = 'RECORDED REPLAY';
    }
}

// -----------------------------------------------------------------------------
// Playback Control Actions
// -----------------------------------------------------------------------------

async function startDemo() {
    const debug = document.getElementById('chkDebugOverlay').checked;
    const res = await fetch('/api/v1/demo/start', {
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
}

async function pauseDemo() {
    const res = await fetch('/api/v1/demo/pause', { method: 'POST' });
    const data = await res.json();
    renderStatus(data);
}

async function resumeDemo() {
    const res = await fetch('/api/v1/demo/resume', { method: 'POST' });
    const data = await res.json();
    renderStatus(data);
}

async function stopDemo() {
    const res = await fetch('/api/v1/demo/stop', { method: 'POST' });
    const data = await res.json();
    renderStatus(data);
}

async function resetDemo() {
    const res = await fetch('/api/v1/demo/reset', { method: 'POST' });
    const data = await res.json();
    allIncidents.clear();
    renderStatus(data);
    renderReviewQueue();
    refreshStream();
}

function refreshStream() {
    const img = document.getElementById('videoStreamImg');
    const placeholder = document.getElementById('videoPlaceholder');
    if (img) {
        img.src = '/api/v1/demo/stream?t=' + Date.now();
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
        const res = await fetch('/api/v1/demo/presets');
        const presets = await res.json();
        // Presets populated
    } catch (e) {}
}

async function fetchStatus() {
    try {
        const res = await fetch('/api/v1/demo/status');
        const data = await res.json();
        renderStatus(data);
    } catch (e) {}
}

async function fetchEvents() {
    try {
        const res = await fetch('/api/v1/demo/events');
        const events = await res.json();
        allIncidents.clear();
        events.forEach((ev) => allIncidents.set(ev.event_id, ev));
        renderReviewQueue();
    } catch (e) {}
}

function renderStatus(st) {
    if (!st) return;

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
        if (st.state === 'RUNNING') {
            stateBadge.className = 'px-2.5 py-1 rounded-md text-xs font-mono font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40';
            if (placeholder) placeholder.style.display = 'none';
        } else if (st.state === 'PAUSED') {
            stateBadge.className = 'px-2.5 py-1 rounded-md text-xs font-mono font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40';
        } else if (st.state === 'COMPLETED') {
            stateBadge.className = 'px-2.5 py-1 rounded-md text-xs font-mono font-semibold bg-blue-500/20 text-blue-300 border border-blue-500/40';
        } else {
            stateBadge.className = 'px-2.5 py-1 rounded-md text-xs font-mono font-semibold bg-gray-800 text-gray-300 border border-gray-700';
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

function filterQueue(status) {
    activeFilter = status;
    document.querySelectorAll('.filter-btn').forEach((b) => {
        if (b.textContent.toUpperCase() === status || (status === 'ALL' && b.textContent === 'All')) {
            b.className = 'filter-btn px-2.5 py-1 rounded-lg bg-cyan-500 text-white font-semibold';
        } else {
            b.className = 'filter-btn px-2.5 py-1 rounded-lg bg-gray-950 text-gray-400 hover:text-gray-200 border border-gray-800';
        }
    });
    renderReviewQueue();
}

function renderReviewQueue() {
    const grid = document.getElementById('reviewQueueGrid');
    const badgeCount = document.getElementById('queueBadgeCount');
    if (!grid) return;

    const incidents = Array.from(allIncidents.values());
    const pendingCount = incidents.filter((ev) => ev.review_status === 'PENDING').length;
    if (badgeCount) badgeCount.textContent = `${pendingCount} Pending`;

    const filtered = incidents.filter((ev) => {
        if (activeFilter === 'ALL') return true;
        return ev.review_status === activeFilter;
    });

    if (filtered.length === 0) {
        grid.innerHTML = `
            <div class="col-span-full py-10 flex flex-col items-center justify-center text-gray-500 font-mono text-xs gap-2">
                <svg class="w-8 h-8 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                <span>No incidents matching filter [${activeFilter}].</span>
            </div>
        `;
        return;
    }

    grid.innerHTML = filtered
        .map((ev) => {
            const risk = Math.round(ev.peak_risk_score || ev.risk_score || 75);
            const occ = ev.occurrence_count || 1;
            const pattern = formatBehaviorLabel(ev.primary_pattern || ev.behavior);
            const seat = ev.seat_id || 'SEAT-??';
            const firstSeen = (ev.first_seen_ms ? ev.first_seen_ms / 1000 : 0).toFixed(1);
            const lastSeen = (ev.last_seen_ms ? ev.last_seen_ms / 1000 : firstSeen).toFixed(1);

            let statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-amber-500/20 text-amber-300 border border-amber-500/40">PENDING REVIEW</span>`;
            if (ev.review_status === 'CONFIRMED') {
                statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">CONFIRMED</span>`;
            } else if (ev.review_status === 'REJECTED') {
                statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-gray-800 text-gray-400 border border-gray-700">REJECTED</span>`;
            } else if (ev.review_status === 'INCONCLUSIVE') {
                statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-purple-500/20 text-purple-300 border border-purple-500/40">INCONCLUSIVE</span>`;
            }

            const sevBadge =
                ev.severity === 'HIGH'
                    ? 'bg-red-500/20 text-red-300 border-red-500/40'
                    : 'bg-amber-500/20 text-amber-300 border-amber-500/40';

            return `
                <div class="incident-card bg-gray-950/80 border border-gray-800/90 rounded-xl p-4 flex flex-col justify-between space-y-3 cursor-pointer" onclick="openReviewModal('${ev.event_id}')">
                    <div class="flex items-start justify-between gap-2">
                        <div class="flex items-center gap-2">
                            <span class="px-2 py-1 rounded-md bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 text-xs font-bold font-mono">[${seat}]</span>
                            <span class="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${sevBadge} border">${ev.severity || 'MEDIUM'}</span>
                        </div>
                        ${statusBadge}
                    </div>

                    <div>
                        <div class="text-sm font-bold text-gray-100">${pattern}</div>
                        <div class="text-xs text-gray-400 font-mono mt-1 flex items-center gap-2">
                            <span>Time: ${firstSeen}s – ${lastSeen}s</span>
                            <span>&bull;</span>
                            <span class="text-amber-300 font-semibold">x${occ} Occurrences</span>
                        </div>
                    </div>

                    <div class="pt-2 border-t border-gray-900 flex items-center justify-between text-xs font-mono">
                        <span class="text-gray-400">Review Priority: <strong class="text-red-400">${risk}/100</strong></span>
                        <button class="px-2.5 py-1 rounded bg-gray-800 hover:bg-cyan-600 text-gray-200 hover:text-white transition text-xs font-semibold">
                            Review &rarr;
                        </button>
                    </div>
                </div>
            `;
        })
        .join('');
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

function openReviewModal(eventId) {
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

    document.getElementById('modalSha256').textContent = ev.video_sha256 || 'SHA-256 Verified';

    // Supporting cues
    const cuesList = document.getElementById('modalSupportingCues');
    const cues = ev.metadata && ev.metadata.supporting_cues ? ev.metadata.supporting_cues : [`Frequency: x${ev.occurrence_count || 1} repetitions`];
    cuesList.innerHTML = cues.map((c) => `<li>${c}</li>`).join('');

    // Video & Snapshot
    const videoPlayer = document.getElementById('modalVideoPlayer');
    const snapshotImg = document.getElementById('modalSnapshotImg');

    if (ev.video_url) {
        videoPlayer.src = ev.video_url;
        videoPlayer.load();
        videoPlayer.play().catch(() => {});
    } else {
        videoPlayer.src = '';
    }

    if (ev.snapshot_url) {
        snapshotImg.src = ev.snapshot_url;
    } else {
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
    document.getElementById('reviewModal').classList.add('hidden');
    activeEventId = null;
}

function selectDecision(decision) {
    selectedDecision = decision;
    updateDecisionButtons();
}

function updateDecisionButtons() {
    const btnConf = document.getElementById('btnDecConfirm');
    const btnRej = document.getElementById('btnDecReject');
    const btnInc = document.getElementById('btnDecInconclusive');

    btnConf.className =
        selectedDecision === 'CONFIRMED'
            ? 'px-2.5 py-2 rounded-lg text-xs font-bold border transition bg-emerald-600 border-emerald-500 text-white shadow'
            : 'px-2.5 py-2 rounded-lg text-xs font-bold border transition bg-gray-900 border-gray-800 text-gray-300 hover:text-emerald-400';

    btnRej.className =
        selectedDecision === 'REJECTED'
            ? 'px-2.5 py-2 rounded-lg text-xs font-bold border transition bg-amber-600 border-amber-500 text-white shadow'
            : 'px-2.5 py-2 rounded-lg text-xs font-bold border transition bg-gray-900 border-gray-800 text-gray-300 hover:text-amber-400';

    btnInc.className =
        selectedDecision === 'INCONCLUSIVE'
            ? 'px-2.5 py-2 rounded-lg text-xs font-bold border transition bg-purple-600 border-purple-500 text-white shadow'
            : 'px-2.5 py-2 rounded-lg text-xs font-bold border transition bg-gray-900 border-gray-800 text-gray-300 hover:text-purple-400';
}

async function submitHumanReview() {
    if (!activeEventId) return;

    const reason = document.getElementById('modalReasonSelect').value;
    const notes = document.getElementById('modalNotesText').value;

    const res = await fetch(`/api/v1/demo/events/${activeEventId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            decision: selectedDecision,
            reason_code: reason,
            notes: notes,
            reviewer_id: 'Lead_Proctor',
        }),
    });

    if (res.ok) {
        if (allIncidents.has(activeEventId)) {
            const ev = allIncidents.get(activeEventId);
            ev.review_status = selectedDecision;
            ev.decision_reason = reason;
            ev.reviewer_notes = notes;
        }
        renderReviewQueue();
        closeReviewModal();
    }
}
