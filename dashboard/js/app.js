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
            labels: ['PROLONGED_HEAD_TURN', 'BODY_LEAN_SIDE', 'LOOK_DOWN_LONG', 'LOW_HAND_POSTURE'],
            datasets: [{
                data: [0, 0, 0, 0],
                backgroundColor: [
                    '#f59e0b', // Head turn - Amber
                    '#3b82f6', // Body lean - Blue
                    '#8b5cf6', // Look down - Purple
                    '#ef4444'  // Low hand - Red
                ],
                borderWidth: 0,
                hoverOffset: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#9ca3af',
                        font: { size: 11, family: 'Inter' },
                        padding: 10
                    }
                }
            },
            cutout: '70%'
        }
    });
}

function updateBehaviorChart(counts = {}) {
    if (!behaviorChart) return;

    const headTurn = counts['PROLONGED_HEAD_TURN'] || counts['side peeking'] || counts['back peeking'] || 0;
    const bodyLean = counts['BODY_LEAN_SIDE'] || 0;
    const lookDown = counts['LOOK_DOWN_LONG'] || counts['front peeking'] || 0;
    const lowHand = counts['LOW_HAND_POSTURE'] || counts['phone using'] || 0;

    const dataArr = [headTurn, bodyLean, lookDown, lowHand];
    const total = dataArr.reduce((a, b) => a + b, 0);

    if (total === 0) {
        behaviorChart.data.datasets[0].data = [1, 1, 1, 1];
    } else {
        behaviorChart.data.datasets[0].data = dataArr;
    }
    behaviorChart.update();

    // Update text labels
    const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
    setEl('statHeadTurn', headTurn);
    setEl('statBodyLean', bodyLean);
    setEl('statLookDown', lookDown);
    setEl('statLowHand', lowHand);
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
            updateBehaviorChart(stats.events_by_behavior);
        }

        // Fetch Room Rankings / Grid
        const roomsRes = await fetch('/api/v1/rooms');
        const rankRes = await fetch('/api/v1/statistics/rankings');
        let rooms = [];
        let rankings = [];

        if (roomsRes.ok) rooms = await roomsRes.json();
        if (rankRes.ok) rankings = await rankRes.json();

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
    setEl('statActiveRooms', stats.active_rooms || 0);

    const highCount = (stats.events_by_severity && stats.events_by_severity.HIGH) || 0;
    setEl('statHighSeverity', highCount);

    // Human Review Rate calculation
    const totalEvents = stats.total_events || 0;
    const reviewedCount = (stats.total_events || 0) - ((stats.events_by_review_status && stats.events_by_review_status.PENDING) || 0);
    const reviewRate = totalEvents > 0 ? Math.round((reviewedCount / totalEvents) * 100) : 100;
    setEl('statReviewRate', `${reviewRate}%`);
}

function renderRoomGrid(rooms, rankings) {
    const grid = document.getElementById('roomCardsGrid');
    if (!grid) return;

    if (!rooms || rooms.length === 0) {
        grid.innerHTML = `<div class="p-6 text-center text-gray-500 col-span-full">No rooms configured in system.</div>`;
        return;
    }

    const rankMap = {};
    rankings.forEach(r => { rankMap[r.room_id] = r; });

    let displayRooms = rooms;
    if (currentRoomFilterType === 'HIGH_RISK') {
        displayRooms = rooms.filter(r => (rankMap[r.id]?.risk_score || 0) >= 30);
    }

    grid.innerHTML = displayRooms.map(room => {
        const rank = rankMap[room.id] || { risk_score: 0, total_events: 0 };
        const isSelected = selectedRoomId === room.id;

        let riskColor = 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
        let barColor = 'bg-emerald-500';
        if (rank.risk_score >= 60) {
            riskColor = 'text-red-400 bg-red-500/20 border-red-500/40 badge-pulse-red';
            barColor = 'bg-red-500';
        } else if (rank.risk_score >= 25) {
            riskColor = 'text-amber-400 bg-amber-500/10 border-amber-500/30';
            barColor = 'bg-amber-500';
        }

        const borderStyle = isSelected ? 'border-cyan-400 ring-2 ring-cyan-400/30' : 'border-gray-800 hover:border-gray-700';

        return `
        <div onclick="selectRoom('${room.id}', '${room.name}')" class="room-card p-4 rounded-xl bg-gray-900/80 border ${borderStyle} cursor-pointer transition flex flex-col justify-between" aria-current="${isSelected}">
            <div>
                <div class="flex items-center justify-between">
                    <span class="text-xs font-mono font-bold text-gray-400 uppercase">${room.room_code || 'ROOM'}</span>
                    <span class="text-xs px-2 py-0.5 rounded-full border font-bold font-mono ${riskColor}">
                        Score ${rank.risk_score}
                    </span>
                </div>
                <h3 class="font-bold text-sm text-white mt-1">${room.name}</h3>
                <p class="text-xs text-gray-400 font-mono mt-0.5">Capacity: ${room.capacity_seats || 24} seats</p>
            </div>

            <div class="mt-4 pt-3 border-t border-gray-800/80">
                <div class="flex items-center justify-between text-xs text-gray-400 mb-1">
                    <span>Risk Level</span>
                    <span class="font-mono text-gray-200">${rank.risk_score}/100</span>
                </div>
                <div class="w-full bg-gray-800 rounded-full h-1.5 overflow-hidden">
                    <div class="${barColor} h-1.5 rounded-full transition-all duration-500" style="width: ${Math.min(100, rank.risk_score)}%"></div>
                </div>
                <div class="flex items-center justify-between text-[11px] text-gray-500 mt-2">
                    <span>Events: <strong class="text-gray-300 font-mono">${rank.total_events}</strong></span>
                    <span class="${isSelected ? 'text-cyan-400 font-semibold' : 'text-gray-400'}">${isSelected ? 'Filtering Feed' : 'Click to filter'}</span>
                </div>
            </div>
        </div>
        `;
    }).join('');
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

function renderEventFeed(events) {
    const feed = document.getElementById('eventsFeed');
    if (!feed) return;

    if (!events || events.length === 0) {
        feed.innerHTML = `<div class="p-8 text-center text-gray-500">No detection events match the current filter.</div>`;
        return;
    }

    feed.innerHTML = events.map(ev => {
        let badgeCls = 'text-gray-400 bg-gray-500/10 border-gray-500/30';
        if (ev.severity === 'HIGH') {
            badgeCls = 'text-red-400 bg-red-500/20 border-red-500/40';
        } else if (ev.severity === 'MEDIUM') {
            badgeCls = 'text-amber-400 bg-amber-500/20 border-amber-500/40';
        }

        let reviewBadgeCls = 'text-gray-400 bg-gray-800';
        if (ev.review_status === 'CONFIRMED') {
            reviewBadgeCls = 'text-red-400 bg-red-500/10 border border-red-500/30';
        } else if (ev.review_status === 'REJECTED') {
            reviewBadgeCls = 'text-emerald-400 bg-emerald-500/10 border border-emerald-500/30';
        } else if (ev.review_status === 'INCONCLUSIVE') {
            reviewBadgeCls = 'text-amber-400 bg-amber-500/10 border border-amber-500/30';
        }

        const seatLabel = ev.seat_id || `TRACK-#${ev.track_id}`;
        const shaShort = ev.evidence_hash ? `${ev.evidence_hash.substring(0, 10)}...` : 'N/A';

        return `
        <div class="review-incident-row p-3.5 rounded-xl bg-gray-900/70 border border-gray-800/80 hover:border-gray-700 transition flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-lg bg-gray-800 flex flex-col items-center justify-center font-mono text-xs text-cyan-300 font-bold border border-gray-700">
                    <span class="text-[9px] text-gray-400">SEAT</span>
                    <span>${seatLabel.replace('SEAT-', '')}</span>
                </div>
                <div>
                    <div class="flex items-center gap-2">
                        <span class="font-bold text-sm text-white font-mono">${ev.behavior}</span>
                        <span class="text-[11px] px-2 py-0.5 rounded border font-bold ${badgeCls}">${ev.severity}</span>
                        <span class="text-[10px] px-2 py-0.5 rounded font-mono ${reviewBadgeCls}">${ev.review_status}</span>
                    </div>
                    <div class="text-xs text-gray-400 mt-1 flex items-center gap-3">
                        <span>Confidence: <strong class="text-gray-200 font-mono">${(ev.confidence_peak * 100).toFixed(0)}%</strong></span>
                        <span>Duration: <strong class="text-gray-200 font-mono">${ev.duration_seconds}s</strong></span>
                        <span class="font-mono text-[11px] text-gray-500">SHA: ${shaShort}</span>
                    </div>
                </div>
            </div>

            <div class="flex items-center gap-2 self-end sm:self-center">
                <button onclick="openEvidenceModal('${ev.id}', '${seatLabel}', '${ev.behavior}', '${ev.confidence_peak}', '${ev.duration_seconds}', '${ev.evidence_hash || ''}', '${ev.review_status}')" class="px-3.5 py-1.5 bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/40 rounded-lg text-xs font-semibold transition flex items-center gap-1.5 shadow">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                    Review Evidence
                </button>
            </div>
        </div>
        `;
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
