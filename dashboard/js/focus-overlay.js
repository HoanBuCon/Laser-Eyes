/*
 * Shared reviewer highlight: outline the flagged student and seat over an
 * evidence frame or clip.  ``focus`` is the incident's {frame_size,
 * seat_polygon, person_bbox}; the SVG's viewBox is the camera frame.
 * Display only: archived evidence files are never modified.
 */

function focusBox(focus) {
    if (!focus || !Array.isArray(focus.frame_size)) return null;
    if (Array.isArray(focus.person_bbox)) return focus.person_bbox;
    const polygon = Array.isArray(focus.seat_polygon) ? focus.seat_polygon : [];
    if (polygon.length < 3) return null;
    const xs = polygon.map((p) => p[0]);
    const ys = polygon.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
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
    // The seat ROI is not drawn (calibration geometry clutters the evidence);
    // it only stands in for the student's box when no person was recorded.
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
