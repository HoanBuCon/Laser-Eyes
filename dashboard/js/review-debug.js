/*
 * Review debug overlay: what the analysis saw on each frame of the evidence clip,
 * drawn in step with the video.
 *   VIGIL Classroom: every person box with its seat (the incident's seat in red).
 *   VIGIL One: face box, gaze arrow (from the eyes, as on the live view), objects.
 * Data comes from GET /api/v1/demo/events/{id}/trace (times in clip ms).  Display only.
 */

const ReviewDebug = (() => {
    const NS = 'http://www.w3.org/2000/svg';
    const HOLD_MS = 600; // a record stays on screen this long when the next frame was not analysed
    let current = null; // { trace, video, svg, info, subject, raf, lastKey }

    const el = (tag, attrs, text) => {
        const node = document.createElementNS(NS, tag);
        Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
        if (text != null) node.textContent = text;
        return node;
    };

    // Last digits of a seat ref: "SEAT-CLASSROOM-03-15" and "S15" both -> "15"
    const seatKey = (ref) => {
        const m = String(ref || '').match(/(\d+)(?!.*\d)/);
        return m ? String(Number(m[1])) : String(ref || '');
    };

    function recordAt(records, ms) {
        let lo = 0;
        let hi = records.length - 1;
        let found = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (records[mid].t <= ms) { found = mid; lo = mid + 1; } else hi = mid - 1;
        }
        if (found < 0) return null;
        return ms - records[found].t <= HOLD_MS ? records[found] : null;
    }

    function setInfo(text) {
        if (!current) return;
        current.info.textContent = text || '';
        current.info.classList.toggle('hidden', !text);
    }

    function stop() {
        if (current && current.raf) cancelAnimationFrame(current.raf);
        if (current) {
            current.video.removeEventListener('seeked', current.onSeek);
            current.video.removeEventListener('play', current.onPlay);
            current.svg.replaceChildren();
            current.svg.classList.add('hidden');
            current.info.classList.add('hidden');
        }
        current = null;
    }

    function scaleFor(svg, w, h) {
        const rect = svg.getBoundingClientRect();
        const s = rect.width && rect.height ? Math.min(rect.width / w, rect.height / h) : 0;
        return (screenPx, fallback) => (s ? screenPx / s : fallback);
    }

    function drawBoxes(rec, w, h, px) {
        const out = [];
        const stroke = px(2.5, 3);
        const font = px(13, 16);
        const mine = seatKey(current.subject);
        (rec.p || []).forEach(([x1, y1, x2, y2, seat]) => {
            const isSubject = seat != null && seatKey(seat) === mine;
            const color = isSubject ? '#ef4444' : (seat != null ? '#facc15' : '#38bdf8');
            out.push(el('rect', {
                x: x1, y: y1, width: x2 - x1, height: y2 - y1, fill: 'none',
                stroke: color, 'stroke-width': isSubject ? stroke * 1.8 : stroke,
            }));
            const label = seat != null ? `S${seatKey(seat).padStart(2, '0')}` : 'roaming';
            out.push(el('text', {
                x: x1 + font * 0.2, y: Math.max(font, y1 - font * 0.35), fill: color, 'font-size': font,
                'font-family': 'monospace', 'font-weight': 700, stroke: '#000', 'stroke-width': font / 5, 'paint-order': 'stroke',
            }, label));
        });
        return { nodes: out, text: `${(rec.p || []).length} people · t=${(rec.t / 1000).toFixed(2)}s` };
    }

    function drawGaze(rec, w, h, px) {
        const out = [];
        const stroke = px(3.5, 4);
        const font = px(13, 16);
        const many = (rec.fb || []).length > 1;
        const attentive = !rec.out && ['center', 'unknown', undefined].includes(rec.head);
        const faceColor = many ? '#ef4444' : (attentive ? '#22c55e' : '#f59e0b');
        (rec.fb || []).forEach(([x, y, bw, bh], i) => {
            out.push(el('rect', { x, y, width: bw, height: bh, fill: 'none', stroke: faceColor, 'stroke-width': stroke, rx: stroke }));
            if (many) {
                out.push(el('text', {
                    x, y: Math.max(font, y - font * 0.4), fill: faceColor, 'font-size': font, 'font-family': 'monospace',
                    'font-weight': 700, stroke: '#000', 'stroke-width': font / 5, 'paint-order': 'stroke',
                }, `FACE ${i + 1}`));
            }
        });
        // Gaze arrow: same geometry as the live overlay (exam_monitor OverlayRenderer._gaze_vector)
        if (rec.n === 1 && rec.gx != null) {
            const face = (rec.fb || [])[0];
            const origin = face ? [face[0] + face[2] / 2, face[1] + face[3] * 0.39] : [w / 2, h / 2];
            const scale = Math.max(90, Math.min(w, h) * 0.2);
            const color = rec.out ? '#f59e0b' : '#22c55e';
            out.push(el('ellipse', {
                cx: origin[0], cy: origin[1], rx: Math.max(24, scale * 0.3), ry: Math.max(20, scale * 0.34),
                fill: 'none', stroke: '#e5e7eb', 'stroke-opacity': 0.85, 'stroke-width': px(2, 2.5), 'stroke-dasharray': `${px(6, 8)} ${px(4, 5)}`,
            }));
            const tx = origin[0] + rec.gx * scale;
            const ty = origin[1] + rec.gy * scale;
            if (Math.abs(rec.gx) + Math.abs(rec.gy) >= 0.05) {
                const head = px(9, 14);
                const ang = Math.atan2(ty - origin[1], tx - origin[0]);
                const wing = (a) => `${tx - head * Math.cos(ang - a)},${ty - head * Math.sin(ang - a)}`;
                [['#14161a', stroke * 2.6], [color, stroke * 1.4]].forEach(([c, sw]) => {
                    out.push(el('line', { x1: origin[0], y1: origin[1], x2: tx, y2: ty, stroke: c, 'stroke-width': sw, 'stroke-linecap': 'round' }));
                    out.push(el('polyline', { points: `${wing(0.45)} ${tx},${ty} ${wing(-0.45)}`, fill: 'none', stroke: c, 'stroke-width': sw, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
                });
            }
            out.push(el('circle', { cx: origin[0], cy: origin[1], r: px(5, 6), fill: color }));
        }
        (rec.obj || []).forEach(([label, conf, [x, y, bw, bh]]) => {
            if (label === 'person' && (rec.p || 0) < 2) return;
            const color = label === 'person' ? '#f59e0b' : '#ef4444';
            out.push(el('rect', { x, y, width: bw, height: bh, fill: 'none', stroke: color, 'stroke-width': stroke }));
            out.push(el('text', {
                x, y: Math.max(font, y - font * 0.4), fill: color, 'font-size': font, 'font-family': 'monospace',
                'font-weight': 700, stroke: '#000', 'stroke-width': font / 5, 'paint-order': 'stroke',
            }, `${label === 'cell phone' ? 'PHONE' : label.toUpperCase()} ${Math.round(conf * 100)}%`));
        });
        const parts = [
            `faces ${rec.n ?? 0}`, `people ${rec.p ?? 0}`,
            rec.n ? `eye ${rec.cal ? (rec.out ? `OUT ${rec.eye}` : 'in zone') : 'calibrating'}` : null,
            rec.n ? `head ${rec.head} · yaw ${rec.yaw}° pitch ${rec.pitch}°` : null,
            `t=${(rec.t / 1000).toFixed(2)}s`,
        ];
        return { nodes: out, text: parts.filter(Boolean).join(' · ') };
    }

    function draw() {
        if (!current) return;
        const { trace, video, svg } = current;
        const size = trace.frame_size;
        if (!size || !size[0]) { if (current.ensure) current.ensure(current.timeMs()); return; }
        const [w, h] = size;
        const ms = current.timeMs ? current.timeMs() : video.currentTime * 1000;
        if (current.ensure) current.ensure(ms);
        const rec = recordAt(trace.records, ms);
        const box = svg.getBoundingClientRect();
        const key = `${rec ? rec.t : 'none'}@${Math.round(box.width)}`;
        if (key === current.lastKey) return;
        current.lastKey = key;
        svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
        if (!rec) {
            svg.replaceChildren();
            setInfo('DEBUG · no analysed frame here');
            return;
        }
        const px = scaleFor(svg, w, h);
        const drawn = trace.kind === 'gaze' ? drawGaze(rec, w, h, px) : drawBoxes(rec, w, h, px);
        svg.replaceChildren(...drawn.nodes);
        setInfo(`DEBUG · ${drawn.text}`);
    }

    function loop() {
        if (!current) return;
        draw();
        current.raf = current.video.paused ? 0 : requestAnimationFrame(loop);
    }

    async function load(item, { video, svg, info, fetchJson }) {
        stop();
        if (!item || item.source_type === 'MANUAL_BOOKMARK' || !item.event_id) return;
        const mine = { video, svg, info, subject: item.subject_ref, trace: null, raf: 0, lastKey: '' };
        current = mine;
        svg.classList.remove('hidden');
        setInfo('DEBUG · loading trace…');
        let trace;
        try {
            trace = await fetchJson(`/api/v1/demo/events/${encodeURIComponent(item.event_id)}/trace`);
        } catch (err) {
            if (current === mine) setInfo(`DEBUG · trace unavailable (${err.message})`);
            return;
        }
        if (current !== mine) return; // another incident was opened meanwhile
        if (!trace.available) {
            setInfo(`DEBUG · ${trace.reason || 'no trace'}`);
            return;
        }
        mine.trace = trace;
        mine.onSeek = () => { mine.lastKey = ''; draw(); };
        mine.onPlay = () => { if (!mine.raf) mine.raf = requestAnimationFrame(loop); };
        video.addEventListener('seeked', mine.onSeek);
        video.addEventListener('play', mine.onPlay);
        draw();
        if (!video.paused) mine.onPlay();
    }

    /**
     * The whole-exam video (VIGIL One): a session's trace is long, so it is read in windows around
     * the playing position. timeMs() gives the exam moment shown; loadWindow(from, to) its records.
     */
    function attach({ video, svg, info, timeMs, loadWindow, windowMs = 60000 }) {
        stop();
        const mine = { video, svg, info, subject: null, raf: 0, lastKey: '', timeMs,
                       trace: { kind: 'gaze', frame_size: null, records: [] }, from: 0, to: -1, loading: false };
        current = mine;
        svg.classList.remove('hidden');
        setInfo('DEBUG · loading trace…');
        mine.ensure = (ms) => {
            if (mine.loading || (ms >= mine.from && ms <= mine.to - 5000)) return;
            mine.loading = true;
            const from = Math.max(0, ms - 10000);
            loadWindow(from, from + windowMs).then((data) => {
                if (current !== mine) return;
                mine.trace = { kind: 'gaze', frame_size: data.frame_size, records: data.records || [] };
                mine.from = from; mine.to = from + windowMs; mine.lastKey = '';
                if (!mine.trace.records.length) setInfo('DEBUG · no trace for this part of the exam');
                draw();
            }).catch((err) => { if (current === mine) setInfo(`DEBUG · trace unavailable (${err.message})`); })
              .finally(() => { mine.loading = false; });
        };
        mine.onSeek = () => { mine.lastKey = ''; draw(); };
        mine.onPlay = () => { if (!mine.raf) mine.raf = requestAnimationFrame(loop); };
        video.addEventListener('seeked', mine.onSeek);
        video.addEventListener('play', mine.onPlay);
        mine.ensure(timeMs());
        if (!video.paused) mine.onPlay();
    }

    function redraw() {
        if (current) { current.lastKey = ''; draw(); }
    }

    return { load, attach, stop, redraw };
})();
