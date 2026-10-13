/*
 * Room check on the calibration page: Seat ROI coverage against the room's
 * capacity, the neighbour links the analysis infers from the saved ROIs
 * (drawn between seat centres), seats without a side neighbour and ROIs that
 * overlap.  Reads /rooms/{id}/seat-layout after every reload or save.
 */
(function () {
    let layout = null;

    async function refreshLayout() {
        if (!currentRoomId) return;
        const camera = currentCameraId ? `?camera_id=${encodeURIComponent(currentCameraId)}` : '';
        try {
            const res = await fetch(`/api/v1/rooms/${encodeURIComponent(currentRoomId)}/seat-layout${camera}`, { cache: 'no-store' });
            layout = res.ok ? await res.json() : null;
        } catch (e) {
            layout = null;
        }
        renderCheck();
        redrawCanvas();
    }

    function short(code) {
        const parts = String(code || '').split('-');
        return `S${parts[parts.length - 1]}`;
    }

    function renderCheck() {
        const chip = document.getElementById('coverageChip');
        const bar = document.querySelector('#coverageBar > i');
        const list = document.getElementById('layoutWarnings');
        if (!layout) {
            chip.textContent = '—';
            list.innerHTML = '';
            return;
        }
        const ratio = layout.coverage;
        const tone = ratio == null ? '' : ratio >= 0.9 ? 'good' : ratio >= 0.5 ? 'warning' : 'danger';
        chip.textContent = layout.capacity ? `${layout.seat_count}/${layout.capacity} seats` : `${layout.seat_count} seats`;
        chip.dataset.tone = tone;
        bar.parentElement.dataset.tone = tone;
        bar.style.width = `${Math.min(100, Math.round((ratio || 0) * 100))}%`;
        const items = [];
        if (layout.capacity && layout.seat_count < layout.capacity) {
            items.push(`${layout.capacity - layout.seat_count} seats of the room have no ROI; students there are not analysed.`);
        }
        if (layout.seats_without_side_neighbour.length) {
            items.push(`No side neighbour: ${layout.seats_without_side_neighbour.map(short).join(', ')}. Glances toward a neighbour cannot be scored for them; check the ROI shape and the gap to the next desk.`);
        }
        layout.overlaps.slice(0, 6).forEach((o) => {
            items.push(`${short(o.seats[0])} and ${short(o.seats[1])} overlap ${Math.round(o.ratio * 100)} % (can cause “multi person” alerts).`);
        });
        if (layout.overlaps.length > 6) items.push(`…and ${layout.overlaps.length - 6} more overlaps.`);
        if (layout.error) items.push(`Neighbour graph could not be built: ${layout.error}`);
        list.innerHTML = items.length
            ? items.map((t) => `<li>${t.replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))}</li>`).join('')
            : '<li class="cal-ok">Every seat has a side neighbour and no ROIs overlap.</li>';
    }

    function centroid(poly) {
        return [poly.reduce((a, p) => a + p[0], 0) / poly.length, poly.reduce((a, p) => a + p[1], 0) / poly.length];
    }

    function drawOverlay() {
        if (!layout || !document.getElementById('showNeighbours').checked) return;
        const byCode = {};
        seats.forEach((seat) => { if (seat.polygon && seat.polygon.length > 2) byCode[seat.seat_code] = centroid(seat.polygon); });
        ctx.save();
        layout.edges.forEach((edge) => {
            const a = byCode[edge.a];
            const b = byCode[edge.b];
            if (!a || !b) return;
            ctx.beginPath();
            ctx.moveTo(a[0], a[1]);
            ctx.lineTo(b[0], b[1]);
            ctx.strokeStyle = edge.kind === 'side' ? 'rgba(215, 255, 95, 0.9)' : 'rgba(169, 183, 201, 0.55)';
            ctx.lineWidth = edge.kind === 'side' ? 2.5 : 1.5;
            ctx.setLineDash(edge.kind === 'side' ? [] : [6, 4]);
            ctx.stroke();
        });
        ctx.setLineDash([]);
        layout.seats_without_side_neighbour.forEach((code) => {
            const c = byCode[code];
            if (!c) return;
            ctx.beginPath();
            ctx.arc(c[0], c[1], 16, 0, Math.PI * 2);
            ctx.strokeStyle = '#f4c95d';
            ctx.lineWidth = 2.5;
            ctx.stroke();
        });
        ctx.restore();
    }

    // Draw after the editor's own drawing, and re-check after seats are (re)loaded
    const baseRedraw = redrawCanvas;
    redrawCanvas = function () {
        baseRedraw();
        drawOverlay();
    };
    const baseReload = reloadSeatsFromDB;
    reloadSeatsFromDB = async function (...args) {
        const result = await baseReload.apply(this, args);
        refreshLayout();
        return result;
    };
    if (typeof saveAllSeatsToDB === 'function') {
        const baseSave = saveAllSeatsToDB;
        saveAllSeatsToDB = async function (...args) {
            const result = await baseSave.apply(this, args);
            refreshLayout();
            return result;
        };
    }
    document.getElementById('showNeighbours').addEventListener('change', () => redrawCanvas());
    refreshLayout();
})();
