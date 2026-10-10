/*
 * 05 Playback: one session's video with its incidents.
 *
 * Boxes are drawn in the browser from the incident data (no re-rendering):
 * amber from the behaviour start until the incident is raised, then coloured
 * by the review decision until a few seconds after its last occurrence.
 * The timeline has one lane per seat with incidents: the seat's risk score
 * over time when the run recorded it, and a bar per incident.
 */
(function () {
    const { $, api, esc, clock, pretty, seat, chip } = P2;
    const HOLD_MS = 3000;
    const LEAD_MS = 5000;
    const NS = 'http://www.w3.org/2000/svg';
    const DECISION_COLOR = { CONFIRMED: '#ff3b3b', REJECTED: '#8a93a0', INCONCLUSIVE: '#b9a7e8', PENDING: '#ff8a3b' };
    const state = { data: null, videoKind: 'source', durationMs: 0, lastKey: '', playhead: null };
    const video = $('video');

    const svgEl = (tag, attrs, text) => {
        const node = document.createElementNS(NS, tag);
        Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
        if (text) node.textContent = text;
        return node;
    };

    function boxOf(inc) {
        const f = inc.focus || {};
        if (Array.isArray(f.person_bbox)) return f.person_bbox;
        if (Array.isArray(f.seat_polygon) && f.seat_polygon.length > 2) {
            const xs = f.seat_polygon.map((p) => p[0]);
            const ys = f.seat_polygon.map((p) => p[1]);
            return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
        }
        return null;
    }

    function timing(inc) {
        const flagged = inc.flagged_ms ?? inc.behavior_start_ms;
        return {
            start: inc.behavior_start_ms ?? flagged,
            flagged,
            end: (inc.last_seen_ms ?? flagged),
        };
    }

    // --- Sessions -------------------------------------------------------------------
    async function loadSessionOptions() {
        const data = await api('/api/v1/exam-sessions?limit=300');
        const wanted = new URLSearchParams(location.search).get('session');
        const items = data.items.filter((s) => s.total_events > 0 || s.id === wanted || s.preset);
        $('sessionSelect').innerHTML = items.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} · ${esc(s.room_code || '')} · ${s.total_events} incidents</option>`).join('');
        if (wanted && items.some((s) => s.id === wanted)) $('sessionSelect').value = wanted;
        if ($('sessionSelect').value) await loadSession($('sessionSelect').value);
    }

    async function loadSession(id) {
        const url = new URL(location.href);
        url.searchParams.set('session', id);
        history.replaceState(null, '', url);
        state.data = await api(`/api/v1/exam-sessions/${encodeURIComponent(id)}/playback`);
        state.lastKey = '';
        $('reviewLink').href = `/review?session_id=${encodeURIComponent(id)}&status=ALL`;
        document.querySelector('[data-video="rendered"]').disabled = !state.data.video.rendered_url;
        if (state.videoKind === 'rendered' && !state.data.video.rendered_url) state.videoKind = 'source';
        setVideo();
        renderIncidents();
        renderTimeline();
    }

    function setVideo() {
        const url = state.videoKind === 'rendered' ? state.data.video.rendered_url : state.data.video.source_url;
        document.querySelectorAll('[data-video]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.video === state.videoKind)));
        $('videoEmpty').hidden = Boolean(url);
        const at = video.currentTime || 0;
        if (url) {
            video.src = url;
            video.addEventListener('loadedmetadata', () => { video.currentTime = Math.min(at, video.duration || at); }, { once: true });
        } else {
            video.removeAttribute('src');
            video.load();
        }
        // The rendered video already has its own boxes
        $('boxes').hidden = state.videoKind === 'rendered';
    }

    // --- Incident list -------------------------------------------------------------------
    function renderIncidents() {
        const list = $('incidentList');
        const incidents = [...state.data.incidents].sort((a, b) => (timing(a).start ?? 0) - (timing(b).start ?? 0));
        if (!incidents.length) {
            list.innerHTML = '<div class="vg-empty">No incidents in this session.</div>';
            return;
        }
        list.innerHTML = incidents.map((inc) => {
            const t = timing(inc);
            return `<button type="button" class="pb-incident" data-id="${esc(inc.id)}">
                <span class="pb-incident-time">${clock(t.start)}</span>
                <span class="pb-incident-main"><strong>${esc(seat(inc.seat_code))} · ${esc(pretty(inc.pattern))}</strong>
                <small>risk ${inc.risk ?? '—'} · flagged ${clock(t.flagged)}${inc.reviewer_id ? ` · ${esc(inc.reviewer_id)}` : ''}</small></span>
                ${chip(inc.review_status)}
            </button>`;
        }).join('');
        list.querySelectorAll('[data-id]').forEach((btn) => btn.addEventListener('click', () => {
            const inc = state.data.incidents.find((i) => i.id === btn.dataset.id);
            seekTo(timing(inc).start - LEAD_MS);
            video.play().catch(() => {});
        }));
    }

    // --- Timeline ----------------------------------------------------------------------------
    function durationMs() {
        if (video.duration && Number.isFinite(video.duration)) return video.duration * 1000;
        const tl = state.data.timeline;
        const fromTimeline = tl ? tl.samples * tl.interval_ms : 0;
        const fromIncidents = Math.max(0, ...state.data.incidents.map((i) => (timing(i).end ?? 0) + HOLD_MS));
        return Math.max(fromTimeline, fromIncidents, 1000);
    }

    function renderTimeline() {
        const box = $('timeline');
        const data = state.data;
        state.durationMs = durationMs();
        const tl = data.timeline;
        const seats = [...new Set(data.incidents.map((i) => i.seat_code).filter(Boolean))].sort();
        $('timelineNote').textContent = tl ? 'risk score per second · bars = incidents' : 'bars = incidents (no per-second risk recorded for this run)';
        const W = 1000;
        const LABEL = 56;
        const LANE = 22;
        const AXIS = 18;
        const H = AXIS + Math.max(1, seats.length) * LANE + 4;
        const x = (ms) => LABEL + (Math.max(0, ms) / state.durationMs) * (W - LABEL);
        const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pb-timeline-svg', preserveAspectRatio: 'none' });

        // Axis: a tick every minute (or 10 s for short videos)
        const step = state.durationMs > 120000 ? 60000 : 10000;
        for (let t = 0; t <= state.durationMs; t += step) {
            svg.append(svgEl('line', { x1: x(t), x2: x(t), y1: AXIS - 4, y2: H, class: 'pb-grid' }));
            svg.append(svgEl('text', { x: x(t) + 2, y: 11, class: 'pb-axis' }, clock(t)));
        }
        seats.forEach((code, i) => {
            const top = AXIS + i * LANE;
            svg.append(svgEl('text', { x: 4, y: top + LANE * 0.68, class: 'pb-lane-label' }, seat(code)));
            svg.append(svgEl('line', { x1: LABEL, x2: W, y1: top + LANE, y2: top + LANE, class: 'pb-lane-line' }));
            const series = tl && tl.seats ? tl.seats[code] : null;
            if (series && series.length) {
                const pts = series.map((v, k) => `${x(k * tl.interval_ms).toFixed(1)},${(top + LANE - 2 - (v / 100) * (LANE - 4)).toFixed(1)}`);
                svg.append(svgEl('polygon', { points: `${x(0)},${top + LANE - 2} ${pts.join(' ')} ${x((series.length - 1) * tl.interval_ms)},${top + LANE - 2}`, class: 'pb-risk' }));
            }
            data.incidents.filter((inc) => inc.seat_code === code).forEach((inc) => {
                const t = timing(inc);
                if (t.start == null) return;
                const bar = svgEl('rect', {
                    x: x(t.start), y: top + 4, width: Math.max(3, x(t.end + HOLD_MS) - x(t.start)), height: LANE - 8,
                    class: 'pb-bar', fill: DECISION_COLOR[inc.review_status] || DECISION_COLOR.PENDING,
                });
                const title = svgEl('title', {}, `${seat(code)} ${pretty(inc.pattern)} · ${inc.review_status} · ${clock(t.start)}–${clock(t.end)}`);
                bar.append(title);
                bar.addEventListener('click', (event) => {
                    event.stopPropagation();
                    seekTo(t.start - LEAD_MS);
                });
                svg.append(bar);
                svg.append(svgEl('line', { x1: x(t.flagged), x2: x(t.flagged), y1: top + 2, y2: top + LANE - 2, class: 'pb-flag' }));
            });
        });
        state.playhead = svgEl('line', { x1: LABEL, x2: LABEL, y1: 0, y2: H, class: 'pb-playhead' });
        svg.append(state.playhead);
        svg.addEventListener('click', (event) => {
            const rect = svg.getBoundingClientRect();
            const px = ((event.clientX - rect.left) / rect.width) * W;
            if (px < LABEL) return;
            seekTo(((px - LABEL) / (W - LABEL)) * state.durationMs);
        });
        svg.style.height = `${H}px`;
        box.replaceChildren(svg);
        if (!seats.length) box.append(Object.assign(document.createElement('div'), { className: 'vg-empty', textContent: 'No incidents to show.' }));
        state.x = x;
    }

    function seekTo(ms) {
        if (!video.src) return;
        video.currentTime = Math.max(0, ms / 1000);
    }

    // --- Per-frame drawing -------------------------------------------------------------------------
    function draw() {
        requestAnimationFrame(draw);
        if (!state.data) return;
        const nowMs = video.currentTime * 1000;
        $('clock').textContent = `${clock(nowMs)} / ${clock(durationMs())}`;
        if (state.playhead && state.x) {
            const px = state.x(nowMs);
            state.playhead.setAttribute('x1', px);
            state.playhead.setAttribute('x2', px);
        }
        const active = state.data.incidents.filter((inc) => {
            const t = timing(inc);
            return t.start != null && nowMs >= t.start && nowMs <= t.end + HOLD_MS;
        });
        $('nowChip').textContent = active.length ? `${active.length} ACTIVE` : 'CLEAR';
        $('nowChip').dataset.tone = active.length ? 'danger' : '';
        const frame = state.data.frame_size;
        const stageW = $('stage').clientWidth || 1;
        const key = `${active.map((i) => `${i.id}:${nowMs >= timing(i).flagged}`).join('|')}@${stageW}@${$('boxesToggle').checked}`;
        if (key === state.lastKey || !frame) return;
        state.lastKey = key;
        const svg = $('boxes');
        svg.setAttribute('viewBox', `0 0 ${frame[0]} ${frame[1]}`);
        const unit = frame[0] / stageW;
        const font = 12 * unit;
        const nodes = [];
        if ($('boxesToggle').checked) {
            active.forEach((inc) => {
                const b = boxOf(inc);
                if (!b) return;
                const flagged = nowMs >= timing(inc).flagged;
                const color = flagged ? (DECISION_COLOR[inc.review_status] || '#ff3b3b') : '#ffb020';
                nodes.push(svgEl('rect', { x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1], class: 'wall-box', stroke: color }));
                const text = flagged ? `${seat(inc.seat_code)} ${pretty(inc.pattern)} · ${inc.review_status}` : `${seat(inc.seat_code)} suspect`;
                const h = font * 1.35;
                const top = b[1] - h >= 0 ? b[1] - h : b[3];
                nodes.push(svgEl('rect', { x: b[0], y: top, width: text.length * font * 0.62 + 8 * unit, height: h, class: 'wall-label-bg' }));
                nodes.push(svgEl('text', { x: b[0] + 4 * unit, y: top + h * 0.76, 'font-size': font, class: 'wall-label' }, text));
            });
        }
        svg.replaceChildren(...nodes);
    }

    // --- Controls ----------------------------------------------------------------------------------
    video.addEventListener('loadedmetadata', () => {
        if (video.videoWidth) $('stage').style.setProperty('--ar', String(video.videoWidth / video.videoHeight));
        video.playbackRate = Number($('speedSelect').value);
        renderTimeline();
    });
    video.addEventListener('play', () => { $('playBtn').textContent = 'Pause'; });
    video.addEventListener('pause', () => { $('playBtn').textContent = 'Play'; });
    $('playBtn').addEventListener('click', () => (video.paused ? video.play().catch(() => {}) : video.pause()));
    $('backBtn').addEventListener('click', () => seekTo(video.currentTime * 1000 - 5000));
    $('fwdBtn').addEventListener('click', () => seekTo(video.currentTime * 1000 + 5000));
    $('speedSelect').addEventListener('change', () => { video.playbackRate = Number($('speedSelect').value); });
    $('boxesToggle').addEventListener('change', () => { state.lastKey = ''; });
    $('sessionSelect').addEventListener('change', () => loadSession($('sessionSelect').value));
    document.querySelectorAll('[data-video]').forEach((btn) => btn.addEventListener('click', () => {
        if (btn.disabled) return;
        state.videoKind = btn.dataset.video;
        state.lastKey = '';
        setVideo();
    }));
    document.addEventListener('keydown', (event) => {
        if (event.target.closest('input, select, textarea')) return;
        if (event.key === ' ') { event.preventDefault(); $('playBtn').click(); }
        if (event.key === 'ArrowLeft') $('backBtn').click();
        if (event.key === 'ArrowRight') $('fwdBtn').click();
    });

    loadSessionOptions().catch((err) => { $('incidentList').innerHTML = `<div class="vg-empty">${esc(err.message)}</div>`; });
    requestAnimationFrame(draw);
})();
