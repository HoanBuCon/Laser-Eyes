/**
 * 01 Operations: a read-only overview of rooms, incident volume and review
 * progress.  Decisions are taken in one place only, the review queue (/review):
 * every incident and room here links there.  Numbers come from the same
 * review-status data the queue uses.
 */

let behaviorChart = null;
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

    // Same number as the "Pending" tab of the review queue
    const pending = stats.unreviewed_events_count || 0;
    const pendingHigh = (stats.pending_by_severity && stats.pending_by_severity.HIGH) || 0;
    setEl('statPending', pending);
    const closedUnresolved = stats.closed_unresolved_count || 0;
    const parts = [pending ? `${pendingHigh} high severity · open review queue →` : 'nothing waiting'];
    if (closedUnresolved) parts.push(`${closedUnresolved} left undecided in closed sessions`);
    setEl('statPendingHint', parts.join(' · '));

    // Share of incidents with a human decision; nothing to review is not "100 %"
    const totalEvents = stats.total_events || 0;
    const decided = totalEvents - pending - closedUnresolved;
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

function reviewLink(params) {
    const query = new URLSearchParams(params);
    return `/review?${query}`;
}

// One dense row per room, rooms with review work first.  The room's priority
// is the highest score among its PENDING incidents: decided incidents are not
// open risk.  A row opens the review queue for that room.
function renderRoomGrid(rooms, rankings) {
    const body = document.getElementById('roomCardsGrid');
    if (!body) return;
    document.querySelectorAll('[data-room-filter]').forEach((btn) => {
        btn.setAttribute('aria-pressed', String(btn.dataset.roomFilter === currentRoomFilterType));
    });

    const empty = { risk_score: 0, total_events: 0, pending: 0, confirmed: 0, rejected: 0 };
    const rankMap = {};
    rankings.forEach((r) => { rankMap[r.room_id] = r; });
    let displayRooms = (rooms || []).map((room) => ({ room, rank: rankMap[room.id] || empty }));
    if (currentRoomFilterType === 'NEEDS_REVIEW') displayRooms = displayRooms.filter(({ rank }) => rank.pending > 0);
    if (currentRoomFilterType === 'MONITORED') displayRooms = displayRooms.filter(({ rank }) => rank.total_events > 0);
    displayRooms.sort((a, b) => b.rank.pending - a.rank.pending || b.rank.risk_score - a.rank.risk_score || b.rank.total_events - a.rank.total_events);

    const count = document.getElementById('roomCount');
    if (count) count.textContent = `${displayRooms.length}/${(rooms || []).length}`;
    if (!displayRooms.length) {
        body.innerHTML = '<tr><td colspan="8" class="vg-empty">No rooms match this filter.</td></tr>';
        return;
    }
    body.innerHTML = displayRooms.map(({ room, rank }) => {
        const tone = rank.pending ? riskTone(rank.risk_score) : '';
        const href = reviewLink({ room: room.room_code || '' });
        return `
        <tr data-href="${escapeHtml(href)}" title="${rank.pending ? 'Review this room’s pending incidents' : 'Open this room in the review queue'}">
            <td class="strong" style="font-family:var(--vigil-font-mono)">${escapeHtml(room.room_code || 'ROOM')}</td>
            <td>${escapeHtml(room.name)}</td>
            <td class="num">${rank.total_events}</td>
            <td class="num strong">${rank.pending ? `<span class="vg-chip" data-tone="warning">${rank.pending}</span>` : 0}</td>
            <td class="num">${rank.confirmed}</td>
            <td class="num">${rank.rejected}</td>
            <td><div class="ops-priority"><div class="vg-bar" data-tone="${tone}"><i style="width:${rank.pending ? Math.min(100, rank.risk_score) : 0}%"></i></div><span>${rank.pending ? rank.risk_score : '—'}</span></div></td>
            <td class="num"><a class="vigil-btn vigil-btn--sm vigil-btn--ghost" href="${escapeHtml(href)}">Review</a></td>
        </tr>`;
    }).join('');
    body.querySelectorAll('tr[data-href]').forEach((row) => {
        row.addEventListener('click', (event) => {
            if (!event.target.closest('a')) window.location.href = row.dataset.href;
        });
    });
}

function filterRooms(type) {
    currentRoomFilterType = type;
    loadDashboardData();
}

// ==============================================================================
// 4. Latest incidents (read-only; same data as the review queue)
// ==============================================================================
async function loadEvents() {
    const params = new URLSearchParams({ status: document.getElementById('reviewFilter')?.value || 'ALL', source: 'AI', sort: 'newest', limit: '50' });
    const severity = document.getElementById('severityFilter')?.value || '';
    if (severity) params.set('severity', severity);
    try {
        const res = await fetch(`/api/v1/proctor/review-queue?${params}`);
        if (!res.ok) return;
        const data = await res.json();
        renderEventFeed(data.items || []);
    } catch (e) {
        console.warn('Failed to load incidents:', e);
    }
}

function applyEventFilters() {
    loadEvents();
}

const SEVERITY_TONE = { HIGH: 'danger', MEDIUM: 'warning', LOW: '' };
const REVIEW_TONE = { CONFIRMED: 'danger', REJECTED: 'good', INCONCLUSIVE: '', PENDING: 'warning' };

function renderEventFeed(items) {
    const feed = document.getElementById('eventsFeed');
    if (!feed) return;
    if (!items.length) {
        feed.innerHTML = '<tr><td colspan="8" class="vg-empty">No incidents match the current filter.</td></tr>';
        return;
    }
    feed.innerHTML = items.map((item) => {
        const when = item.created_at ? new Date(`${item.created_at}${/Z|[+-]\d\d:?\d\d$/.test(item.created_at) ? '' : 'Z'}`) : null;
        const whenText = when && !Number.isNaN(when.getTime())
            ? when.toLocaleString([], { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
            : '—';
        const pattern = item.primary_signal;
        const pending = item.review_status === 'PENDING';
        const href = reviewLink({ status: 'ALL', open: `AI:${item.event_id}` });
        return `
        <tr data-href="${escapeHtml(href)}">
            <td style="font-family:var(--vigil-font-mono)">${escapeHtml(whenText)}</td>
            <td class="strong" style="font-family:var(--vigil-font-mono)">${escapeHtml(item.room_code || '—')}</td>
            <td class="strong" title="${escapeHtml(pattern)}">${escapeHtml(prettyPattern(pattern))}</td>
            <td><span class="vg-chip" data-tone="${SEVERITY_TONE[item.severity] || ''}">${escapeHtml(item.severity)}</span></td>
            <td><span class="vg-chip" data-tone="${REVIEW_TONE[item.review_status] || ''}">${escapeHtml(item.review_status)}</span></td>
            <td>${escapeHtml(item.reviewer_id || '—')}</td>
            <td class="num">${item.review_priority_score ?? '—'}</td>
            <td class="num"><a class="vigil-btn vigil-btn--sm ${pending ? 'vigil-btn--primary' : ''}" href="${escapeHtml(href)}">${pending ? 'Review' : 'Open'}</a></td>
        </tr>`;
    }).join('');
    feed.querySelectorAll('tr[data-href]').forEach((row) => {
        row.addEventListener('click', (event) => {
            if (!event.target.closest('a')) window.location.href = row.dataset.href;
        });
    });
}
