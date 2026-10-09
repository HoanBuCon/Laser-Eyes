/**
 * VIGIL AI Enterprise Proctoring Dashboard Application (SRS v1.0)
 *
 * Implements:
 * - 10-20 Concurrent Exam Rooms Multi-Card Grid
 * - Real-time WebSocket event ingestion with HTTP polling fallback
 * - Seat-anchored review incident feed
 * - Dual media evidence viewer (Peak JPEG snapshot + 10s MP4 video clip)
 * - Cryptographic SHA-256 integrity hash verification
 * - Human-in-the-Loop review actions (CONFIRM, REJECT, INCONCLUSIVE)
 */

let behaviorChart = null;
let currentEventId = null;
let currentSha256 = '';
let selectedRoomId = null;
let currentRoomFilterType = 'ALL';
let roomCodeById = {};
let ws = null;

document.addEventListener('DOMContentLoaded', () => {
    initChart();
    initWebSocket();
    loadDashboardData();
    // Auto-refresh every 5 seconds as heartbeat fallback
    setInterval(loadDashboardData, 5000);
});

// ==============================================================================
// 1. WebSocket Real-time Ingestion & Heartbeat
// ==============================================================================
function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/events`;

    try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            setWsBadge(true, 'LIVE STREAM CONNECTED');
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleIncomingLiveEvent(data);
            } catch (e) {
                // Keep-alive or non-JSON message
            }
        };

        ws.onclose = () => {
            setWsBadge(false, 'RECONNECTING...');
            setTimeout(initWebSocket, 4000);
        };

        ws.onerror = () => {
            setWsBadge(false, 'STREAM OFFLINE');
        };
    } catch (err) {
        setWsBadge(false, 'POLLING MODE');
    }
}

function setWsBadge(isLive, text) {
    const badge = document.getElementById('wsStatusBadge');
    const label = document.getElementById('wsStatusText');
    if (!badge || !label) return;

    label.innerText = text;
    badge.dataset.connected = String(isLive);
}

function handleIncomingLiveEvent(eventData) {
    // Refresh stats and events when a live event arrives
    loadDashboardData();
}

function triggerManualRefresh() {
    loadDashboardData();
}

// ==============================================================================
// 2. Behavior Signal Chart
// ==============================================================================
function initChart() {
    const ctx = document.getElementById('behaviorDonutChart');
    if (!ctx) return;

    behaviorChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: [],
            datasets: [{
                data: [],
                backgroundColor: BEHAVIOR_COLORS,
                borderWidth: 0,
                hoverOffset: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            // The legend is drawn next to the chart as compact rows
            plugins: { legend: { display: false } },
            cutout: '68%'
        }
    });
}

// Categorical palette for behaviour patterns (as many as the backend reports)
const BEHAVIOR_COLORS = ['#f59e0b', '#3b82f6', '#8b5cf6', '#ef4444', '#10b981', '#06b6d4', '#ec4899', '#a3a3a3'];

function prettyPattern(name) {
    return String(name || '').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (l) => l.toUpperCase());
}

// Counts come from the backend per pattern name, so new patterns appear without code changes
function updateBehaviorChart(counts = {}) {
    const entries = Object.entries(counts).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
    if (behaviorChart) {
        behaviorChart.data.labels = entries.map(([name]) => prettyPattern(name));
        behaviorChart.data.datasets[0].data = entries.map(([, n]) => n);
        behaviorChart.update();
    }
    const legend = document.getElementById('behaviorLegend');
    if (!legend) return;
    if (!entries.length) {
        legend.innerHTML = '<div class="vg-empty">No incidents yet.</div>';
        return;
    }
    legend.replaceChildren(...entries.map(([name, n], i) => {
        const row = document.createElement('div');
        row.className = 'ops-legend-row';
        const label = document.createElement('span');
        label.textContent = prettyPattern(name);
        label.title = name;
        label.style.borderLeft = `3px solid ${BEHAVIOR_COLORS[i % BEHAVIOR_COLORS.length]}`;
        const value = document.createElement('span');
        value.textContent = n;
        row.append(label, value);
        return row;
    }));
}

// ==============================================================================
// 3. Main Data Fetching & Rendering
// ==============================================================================
async function loadDashboardData() {
    try {
        // Fetch Summary Stats
        const statsRes = await fetch('/api/v1/statistics/summary');
        if (statsRes.ok) {
            const stats = await statsRes.json();
            updateSummaryCounters(stats);
            updateBehaviorChart(stats.events_by_pattern || stats.events_by_behavior);
        }

        // Fetch Room Rankings / Grid
        const roomsRes = await fetch('/api/v1/rooms');
        const rankRes = await fetch('/api/v1/statistics/rankings');
        let rooms = [];
        let rankings = [];

        if (roomsRes.ok) rooms = await roomsRes.json();
        if (rankRes.ok) rankings = await rankRes.json();

        roomCodeById = Object.fromEntries(rooms.map((r) => [r.id, r.room_code || r.name]));
        renderRoomGrid(rooms, rankings);

        // Fetch Events List with active filters
        loadEvents();
    } catch (err) {
        console.warn('Dashboard sync telemetry error:', err);
    }
}

function updateSummaryCounters(stats) {
    const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
    setEl('statTotalEvents', stats.total_events || 0);
    // Rooms that can be analysed (they have Seat ROIs), out of all configured rooms
    setEl('statActiveRooms', stats.monitored_rooms ?? stats.active_rooms ?? 0);
    setEl('statActiveRoomsHint', `of ${stats.active_rooms || 0} rooms have seat ROIs`);

    const highCount = (stats.events_by_severity && stats.events_by_severity.HIGH) || 0;
    setEl('statHighSeverity', highCount);
    setEl('statPendingHint', `${stats.unreviewed_events_count || 0} incidents pending review`);

    // Share of incidents with a human decision; nothing to review is not "100 %"
    const totalEvents = stats.total_events || 0;
    const pending = stats.unreviewed_events_count || 0;
    const decided = totalEvents - pending;
    setEl('statReviewRate', totalEvents > 0 ? `${Math.round((decided / totalEvents) * 100)}%` : '—');
    setEl('statReviewRateHint', totalEvents > 0 ? `${decided} of ${totalEvents} decided by proctors` : 'no incidents yet');
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function riskTone(score) {
    if (score >= 60) return 'danger';
    if (score >= 25) return 'warning';
    return 'good';
}

// One dense row per room, busiest first; clicking a row filters the incident list
function renderRoomGrid(rooms, rankings) {
    const body = document.getElementById('roomCardsGrid');
    if (!body) return;
    document.querySelectorAll('[data-room-filter]').forEach((btn) => {
        btn.setAttribute('aria-pressed', String(btn.dataset.roomFilter === currentRoomFilterType));
    });

    const rankMap = {};
    rankings.forEach(r => { rankMap[r.room_id] = r; });
    let displayRooms = (rooms || []).map((room) => ({ room, rank: rankMap[room.id] || { risk_score: 0, total_events: 0 } }));
    if (currentRoomFilterType === 'HIGH_RISK') displayRooms = displayRooms.filter(({ rank }) => rank.risk_score >= 30);
    if (currentRoomFilterType === 'MONITORED') displayRooms = displayRooms.filter(({ rank }) => rank.total_events > 0);
    displayRooms.sort((a, b) => b.rank.total_events - a.rank.total_events || b.rank.risk_score - a.rank.risk_score);

    const count = document.getElementById('roomCount');
    if (count) count.textContent = `${displayRooms.length}/${(rooms || []).length}`;
    if (!displayRooms.length) {
        body.innerHTML = '<tr><td colspan="6" class="vg-empty">No rooms match this filter.</td></tr>';
        return;
    }
    body.innerHTML = displayRooms.map(({ room, rank }) => {
        const tone = riskTone(rank.risk_score);
        const selected = selectedRoomId === room.id;
        return `
        <tr aria-selected="${selected}" data-room-id="${escapeHtml(room.id)}" data-room-name="${escapeHtml(room.name)}" title="${selected ? 'Click to clear the filter' : 'Click to filter incidents'}">
            <td class="strong" style="font-family:var(--vigil-font-mono)">${escapeHtml(room.room_code || 'ROOM')}</td>
            <td>${escapeHtml(room.name)}</td>
            <td class="num">${room.capacity ?? '—'}</td>
            <td class="num strong">${rank.total_events}</td>
            <td><div class="vg-bar" data-tone="${tone}"><i style="width:${Math.min(100, rank.risk_score)}%"></i></div></td>
            <td class="num"><span class="vg-chip" data-tone="${rank.total_events ? tone : ''}">${rank.risk_score}</span></td>
        </tr>`;
    }).join('');
    body.querySelectorAll('tr[data-room-id]').forEach((row) => {
        row.addEventListener('click', () => selectRoom(row.dataset.roomId, row.dataset.roomName));
    });
}

function filterRooms(type) {
    currentRoomFilterType = type;
    loadDashboardData();
}

function selectRoom(roomId, roomName) {
    if (selectedRoomId === roomId) {
        selectedRoomId = null;
        const badge = document.getElementById('selectedRoomFilterBadge');
        if (badge) badge.classList.add('hidden');
    } else {
        selectedRoomId = roomId;
        const badge = document.getElementById('selectedRoomFilterBadge');
        if (badge) {
            badge.innerText = `Room: ${roomName}`;
            badge.classList.remove('hidden');
        }
    }
    loadDashboardData();
}

// ==============================================================================
// 4. Events Feed & Filtering
// ==============================================================================
async function loadEvents() {
    const sevFilter = document.getElementById('severityFilter')?.value || '';
    const revFilter = document.getElementById('reviewFilter')?.value || '';

    let url = '/api/v1/events?limit=50';
    if (selectedRoomId) url += `&room_id=${encodeURIComponent(selectedRoomId)}`;
    if (sevFilter) url += `&severity=${encodeURIComponent(sevFilter)}`;
    if (revFilter) url += `&review_status=${encodeURIComponent(revFilter)}`;

    try {
        const res = await fetch(url);
        if (!res.ok) return;
        const events = await res.json();
        renderEventFeed(events);
    } catch (e) {
        console.warn('Failed to load events:', e);
    }
}

function applyEventFilters() {
    loadEvents();
}

const SEVERITY_TONE = { HIGH: 'danger', MEDIUM: 'warning', LOW: '' };
const REVIEW_TONE = { CONFIRMED: 'danger', REJECTED: 'good', INCONCLUSIVE: 'warning', PENDING: '' };

function renderEventFeed(events) {
    const feed = document.getElementById('eventsFeed');
    if (!feed) return;

    if (!events || events.length === 0) {
        feed.innerHTML = '<tr><td colspan="8" class="vg-empty">No incidents match the current filter.</td></tr>';
        return;
    }

    feed.innerHTML = events.map(ev => {
        const seatLabel = ev.seat_id || `TRACK-#${ev.track_id}`;
        const shaShort = ev.evidence_hash ? `${ev.evidence_hash.substring(0, 10)}…` : '—';
        const when = ev.created_at ? new Date(`${ev.created_at}${/Z|[+-]\d\d:?\d\d$/.test(ev.created_at) ? '' : 'Z'}`) : null;
        const whenText = when && !Number.isNaN(when.getTime())
            ? when.toLocaleString([], { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
            : '—';
        const pattern = ev.primary_pattern || ev.behavior;
        const args = [ev.id, seatLabel, ev.behavior, ev.confidence_peak, ev.duration_seconds, ev.evidence_hash || '', ev.review_status]
            .map((v) => `'${escapeHtml(String(v ?? ''))}'`).join(', ');
        return `
        <tr>
            <td style="font-family:var(--vigil-font-mono)">${escapeHtml(whenText)}</td>
            <td class="strong" style="font-family:var(--vigil-font-mono)">${escapeHtml(roomCodeById[ev.room_id] || '—')}</td>
            <td class="strong" title="${escapeHtml(pattern)}">${escapeHtml(prettyPattern(pattern))}</td>
            <td><span class="vg-chip" data-tone="${SEVERITY_TONE[ev.severity] || ''}">${escapeHtml(ev.severity)}</span></td>
            <td><span class="vg-chip" data-tone="${REVIEW_TONE[ev.review_status] || ''}">${escapeHtml(ev.review_status)}</span></td>
            <td class="num">${ev.risk_score ?? Math.round((ev.confidence_peak || 0) * 100)}</td>
            <td style="font-family:var(--vigil-font-mono);color:var(--vigil-text-dim)">${escapeHtml(shaShort)}</td>
            <td class="num"><button type="button" class="vigil-btn vigil-btn--sm" onclick="openEvidenceModal(${args})">Evidence</button></td>
        </tr>`;
    }).join('');
}

// ==============================================================================
// 5. Dual Evidence Modal (Snapshot + MP4 Video + SHA-256)
// ==============================================================================
function openEvidenceModal(eventId, seatLabel, behavior, confPeak, duration, sha256, reviewStatus) {
    currentEventId = eventId;
    currentSha256 = sha256;

    const modal = document.getElementById('evidenceModal');
    const title = document.getElementById('modalEventTitle');
    const seatBadge = document.getElementById('modalSeatBadge');
    const subtitle = document.getElementById('modalEventSubtitle');
    const shaLabel = document.getElementById('modalSha256');
    const curRevBadge = document.getElementById('modalCurrentReviewStatus');
    const img = document.getElementById('modalEvidenceImg');
    const videoSource = document.getElementById('modalVideoSource');
    const videoPlayer = document.getElementById('modalEvidenceVideo');

    if (!modal) return;

    seatBadge.innerText = seatLabel;
    subtitle.innerText = `Behavior: ${behavior} | Peak Confidence: ${(Number(confPeak) * 100).toFixed(0)}% | Duration: ${duration}s`;
    shaLabel.innerText = sha256 || 'SHA-256 NOT GENERATED';
    curRevBadge.innerText = reviewStatus || 'PENDING';

    // Set media URLs
    const snapUrl = `/api/v1/events/${eventId}/evidence`;
    const videoUrl = `/api/v1/events/${eventId}/video`;

    if (img) img.src = snapUrl;
    if (videoSource && videoPlayer) {
        videoSource.src = videoUrl;
        videoPlayer.load();
    }

    switchEvidenceTab('snapshot');

    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

function closeEvidenceModal() {
    const modal = document.getElementById('evidenceModal');
    const videoPlayer = document.getElementById('modalEvidenceVideo');
    if (videoPlayer) videoPlayer.pause();

    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
}

function switchEvidenceTab(tab) {
    const snapViewer = document.getElementById('snapshotViewer');
    const vidViewer = document.getElementById('videoViewer');
    const btnSnap = document.getElementById('btnTabSnapshot');
    const btnVid = document.getElementById('btnTabVideo');
    const videoPlayer = document.getElementById('modalEvidenceVideo');

    if (btnSnap) btnSnap.setAttribute('aria-selected', String(tab === 'snapshot'));
    if (btnVid) btnVid.setAttribute('aria-selected', String(tab === 'video'));

    if (tab === 'snapshot') {
        if (snapViewer) snapViewer.classList.remove('hidden');
        if (vidViewer) vidViewer.classList.add('hidden');
        if (videoPlayer) videoPlayer.pause();
    } else {
        if (snapViewer) snapViewer.classList.add('hidden');
        if (vidViewer) vidViewer.classList.remove('hidden');
        if (videoPlayer) videoPlayer.play().catch(() => {});
    }
}

function copySha256() {
    if (!currentSha256) return;
    navigator.clipboard.writeText(currentSha256).then(() => {
        alert('SHA-256 Hash copied to clipboard:\n' + currentSha256);
    }).catch(() => {
        prompt('Copy SHA-256 Hash:', currentSha256);
    });
}

// ==============================================================================
// 6. Human-in-the-Loop Review Submission
// ==============================================================================
async function submitReviewDecision(decision) {
    if (!currentEventId) return;

    const reasonCode = document.getElementById('reviewReasonCode')?.value || 'CLEAR_CHEATING';
    const note = document.getElementById('reviewNoteInput')?.value || '';

    try {
        const payload = {
            reviewer_id: 'proctor_admin',
            decision: decision,
            reason_code: reasonCode,
            note: note
        };

        const res = await fetch(`/api/v1/events/${currentEventId}/review`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            const curRevBadge = document.getElementById('modalCurrentReviewStatus');
            if (curRevBadge) curRevBadge.innerText = decision;

            // Refresh events and stats
            loadDashboardData();
            setTimeout(closeEvidenceModal, 600);
        } else {
            alert('Failed to submit review decision.');
        }
    } catch (err) {
        console.error('Review submission error:', err);
        alert('Error submitting review decision.');
    }
}
