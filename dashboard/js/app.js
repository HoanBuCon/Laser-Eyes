/**
 * VIGIL AI Enterprise Proctoring Dashboard Application
 */

let behaviorChart = null;
let currentSessionId = null;

document.addEventListener('DOMContentLoaded', () => {
    initChart();
    loadDashboardData();
    // Auto-refresh every 4 seconds
    setInterval(loadDashboardData, 4000);
});

function initChart() {
    const ctx = document.getElementById('behaviorDonutChart');
    if (!ctx) return;

    behaviorChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Phone Using', 'Back Peeking', 'Side Peeking', 'Front Peeking', 'No Cheating'],
            datasets: [{
                data: [0, 0, 0, 0, 1],
                backgroundColor: [
                    '#ef4444', // Phone - Red
                    '#f97316', // Back - Orange
                    '#eab308', // Side - Yellow
                    '#3b82f6', // Front - Blue
                    '#10b981'  // Normal - Green
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
                        font: { size: 12, family: 'Inter' },
                        padding: 14
                    }
                }
            },
            cutout: '72%'
        }
    });
}

async function loadDashboardData() {
    try {
        // 1. Fetch Summary Stats
        const statsRes = await fetch('/api/statistics/summary');
        if (statsRes.ok) {
            const stats = await statsRes.json();
            updateSummaryCounters(stats);
            updateBehaviorChart(stats.events_by_behavior);
        }

        // 2. Fetch Room Risk Rankings
        const rankRes = await fetch('/api/statistics/rankings');
        if (rankRes.ok) {
            const rankings = await rankRes.json();
            renderRoomRankings(rankings);
        }

        // 3. Fetch Active Sessions
        const sessRes = await fetch('/api/sessions/active');
        if (sessRes.ok) {
            const sessions = await sessRes.json();
            renderActiveSessions(sessions);
            if (sessions.length > 0) {
                loadSessionEvents(sessions[0].id);
            }
        }
    } catch (err) {
        console.warn('Telemetry update sync error:', err);
    }
}

function updateSummaryCounters(stats) {
    document.getElementById('statTotalEvents').innerText = stats.total_events || 0;
    document.getElementById('statActiveRooms').innerText = stats.active_rooms || 0;
    document.getElementById('statCompletedSessions').innerText = stats.completed_sessions || 0;

    const highCount = (stats.events_by_severity && stats.events_by_severity.HIGH) || 0;
    document.getElementById('statHighSeverity').innerText = highCount;
}

function updateBehaviorChart(behaviorCounts = {}) {
    if (!behaviorChart) return;

    const counts = [
        behaviorCounts['phone using'] || 0,
        behaviorCounts['back peeking'] || 0,
        behaviorCounts['side peeking'] || 0,
        behaviorCounts['front peeking'] || 0,
        behaviorCounts['no cheating'] || 0,
    ];

    const total = counts.reduce((a, b) => a + b, 0);
    if (total === 0) {
        behaviorChart.data.datasets[0].data = [0, 0, 0, 0, 1];
    } else {
        behaviorChart.data.datasets[0].data = counts;
    }
    behaviorChart.update();
}

function renderRoomRankings(rankings) {
    const tbody = document.getElementById('roomRankingsTable');
    if (!tbody) return;

    if (rankings.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-gray-500">No rooms monitored yet</td></tr>`;
        return;
    }

    tbody.innerHTML = rankings.map((r, i) => {
        let badgeColor = 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
        if (r.risk_score >= 60) {
            badgeColor = 'bg-rose-500/20 text-rose-400 border-rose-500/30 badge-pulse-red';
        } else if (r.risk_score >= 25) {
            badgeColor = 'bg-amber-500/20 text-amber-400 border-amber-500/30';
        }

        return `
        <tr class="border-b border-gray-800/50 hover:bg-gray-800/30 transition">
            <td class="py-3 px-4 font-mono text-gray-400">#${i + 1}</td>
            <td class="py-3 px-4 font-medium text-white">${r.room_name}</td>
            <td class="py-3 px-4 text-gray-400 text-sm">${r.site_name}</td>
            <td class="py-3 px-4 font-semibold text-center">${r.total_events}</td>
            <td class="py-3 px-4 text-right">
                <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold border ${badgeColor}">
                    ${r.risk_score} / 100
                </span>
            </td>
        </tr>`;
    }).join('');
}

function renderActiveSessions(sessions) {
    const container = document.getElementById('activeSessionsList');
    if (!container) return;

    if (sessions.length === 0) {
        container.innerHTML = `<div class="p-4 text-sm text-gray-500 text-center">No active exam sessions</div>`;
        return;
    }

    container.innerHTML = sessions.map(s => `
        <div class="p-3 rounded-lg bg-gray-900/60 border border-gray-800 hover:border-cyan-500/40 transition flex items-center justify-between cursor-pointer" onclick="loadSessionEvents('${s.id}')">
            <div>
                <div class="font-medium text-sm text-white">${s.exam_name}</div>
                <div class="text-xs text-cyan-400 flex items-center gap-2 mt-0.5">
                    <span class="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span>
                    Frames: ${s.total_frames.toLocaleString()} | Events: ${s.total_events}
                </div>
            </div>
            <span class="text-xs px-2 py-1 rounded bg-red-500/20 text-red-300 border border-red-500/30 font-mono">
                Risk: ${s.risk_score}
            </span>
        </div>
    `).join('');
}

async function loadSessionEvents(sessionId) {
    currentSessionId = sessionId;
    try {
        const res = await fetch(`/api/events/session/${sessionId}`);
        if (!res.ok) return;
        const events = await res.json();
        renderEventFeed(events);
    } catch (e) {
        console.warn('Error fetching events:', e);
    }
}

function renderEventFeed(events) {
    const feed = document.getElementById('eventsFeed');
    if (!feed) return;

    if (events.length === 0) {
        feed.innerHTML = `<div class="p-8 text-center text-gray-500">No violations detected in this session.</div>`;
        return;
    }

    feed.innerHTML = events.map(ev => {
        let badgeCls = ev.severity === 'HIGH' ? 'text-red-400 bg-red-500/10 border-red-500/30' : 'text-amber-400 bg-amber-500/10 border-amber-500/30';
        let evidenceBtn = ev.evidence_url ? `
            <button onclick="openEvidenceModal('${ev.evidence_url}', '${ev.event_id}', '${ev.behavior}')" class="px-3 py-1 bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/30 rounded text-xs transition flex items-center gap-1">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                Evidence
            </button>
        ` : '';

        return `
        <div class="p-3.5 rounded-xl bg-gray-900/70 border border-gray-800/80 hover:border-gray-700 transition flex items-center justify-between gap-4">
            <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-lg bg-gray-800 flex items-center justify-center font-mono text-xs text-gray-300">
                    #${ev.track_id}
                </div>
                <div>
                    <div class="flex items-center gap-2">
                        <span class="font-semibold text-sm text-white uppercase tracking-wider">${ev.behavior}</span>
                        <span class="text-xs px-2 py-0.5 rounded border ${badgeCls} font-bold">${ev.severity}</span>
                    </div>
                    <div class="text-xs text-gray-400 mt-0.5">
                        Peak Conf: <span class="text-gray-200 font-mono">${(ev.confidence_peak * 100).toFixed(1)}%</span> | 
                        Duration: <span class="text-gray-200 font-mono">${ev.duration_seconds}s</span>
                        ${ev.room_context ? `<span class="text-amber-400/80 ml-1">(${ev.room_context})</span>` : ''}
                    </div>
                </div>
            </div>
            <div class="flex items-center gap-2">
                ${evidenceBtn}
            </div>
        </div>`;
    }).join('');
}

function openEvidenceModal(url, eventId, behavior) {
    const modal = document.getElementById('evidenceModal');
    const img = document.getElementById('modalEvidenceImg');
    const title = document.getElementById('modalEventTitle');

    if (!modal || !img) return;

    img.src = url;
    title.innerText = `Evidence: ${eventId} — ${behavior.toUpperCase()}`;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

function closeEvidenceModal() {
    const modal = document.getElementById('evidenceModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
}
