/*
 * Printable session report (?session=ID[&version=N]) and the record of one
 * confirmed violation (?incident=ID).  The browser's "Save as PDF" makes the
 * PDF.  Wording describes observed behaviour and the proctor's decision; it
 * never states that the AI found cheating.
 */
(function () {
    const { esc, clock, pretty, seat, utc } = P2;
    const params = new URLSearchParams(location.search);
    const sheet = document.getElementById('sheet');
    const dt = (iso, isUtc = true) => {
        const d = isUtc ? utc(iso) : (iso ? new Date(iso) : null);
        return d && !Number.isNaN(d.getTime()) ? d.toLocaleString('vi-VN') : '—';
    };

    // Evidence frame with the student's box, so the printed page shows who it is about
    function figure(c, style = '') {
        if (!c.snapshot_url) return '<div class="empty">Không có ảnh</div>';
        const f = c.focus || {};
        let box = f.person_bbox;
        if (!box && Array.isArray(f.seat_polygon) && f.seat_polygon.length > 2) {
            const xs = f.seat_polygon.map((p) => p[0]);
            const ys = f.seat_polygon.map((p) => p[1]);
            box = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
        }
        const size = f.frame_size;
        const svg = box && size
            ? `<svg viewBox="0 0 ${size[0]} ${size[1]}" preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:100%">
                 <rect x="${box[0] - 6}" y="${box[1] - 6}" width="${box[2] - box[0] + 12}" height="${box[3] - box[1] + 12}" fill="none" stroke="#e00000" stroke-width="${Math.max(3, size[0] / 400)}"/></svg>`
            : '';
        return `<div style="position:relative;line-height:0;${style}"><img src="${esc(c.snapshot_url)}" alt="" style="width:100%;border:1px solid #bbb">${svg}</div>`;
    }

    function header() {
        return `<div class="header">
            <div><strong>HỘI ĐỒNG THI</strong><br><span class="dots"></span></div>
            <div><strong>CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</strong><br>Độc lập – Tự do – Hạnh phúc</div>
        </div>`;
    }

    function sessionReport(r, frozen) {
        const s = r.session;
        const by = r.incidents.by_review_status;
        const cov = r.coverage;
        const patterns = Object.entries(r.incidents.by_pattern);
        const approval = r.approval;
        document.title = `Báo cáo ca thi — ${s.name}`;
        return `${header()}
            <h1>Báo cáo ca thi</h1>
            <p class="sub">${esc(s.name)}${frozen ? ` · Phiên bản ${frozen.version} đã duyệt` : ' · Bản nháp (chưa duyệt)'}</p>
            <h2>1. Thông tin ca thi</h2>
            <table class="facts"><tbody>
                <tr><td>Kỳ thi</td><td>${esc(r.exam ? `${r.exam.name}${r.exam.subject_code ? ` (${r.exam.subject_code})` : ''}` : '—')}</td></tr>
                <tr><td>Phòng thi</td><td>${esc(r.room ? `${r.room.code} – ${r.room.name}` : '—')}</td></tr>
                <tr><td>Giám thị phụ trách</td><td>${esc(r.proctor || '—')}</td></tr>
                <tr><td>Thời gian theo lịch</td><td>${s.scheduled_start ? `${dt(s.scheduled_start, false)} – ${dt(s.scheduled_end, false)}` : '—'}</td></tr>
                <tr><td>Thời gian phân tích</td><td>${dt(s.started_at)} – ${dt(s.ended_at)}</td></tr>
                <tr><td>Độ phủ vị trí ngồi</td><td>${cov.capacity ? `${cov.seats_with_roi}/${cov.capacity} chỗ (${Math.round((cov.ratio || 0) * 100)} %)` : `${cov.seats_with_roi} chỗ`}</td></tr>
            </tbody></table>
            <h2>2. Tổng hợp sự việc AI đề nghị xem xét</h2>
            <table><thead><tr><th>Tổng</th><th class="num">Đã xác nhận</th><th class="num">Bác bỏ</th><th class="num">Chưa rõ</th><th class="num">Chưa xử lý</th><th class="num">TG xử lý TB</th></tr></thead>
            <tbody><tr><td>${r.incidents.total}</td><td class="num">${by.CONFIRMED || 0}</td><td class="num">${by.REJECTED || 0}</td><td class="num">${by.INCONCLUSIVE || 0}</td><td class="num">${by.PENDING || 0}</td>
            <td class="num">${r.incidents.avg_review_minutes == null ? '—' : `${r.incidents.avg_review_minutes} phút`}</td></tr></tbody></table>
            ${patterns.length ? `<table style="margin-top:6px"><thead><tr><th>Hành vi quan sát</th><th class="num">Chưa xử lý</th><th class="num">Xác nhận</th><th class="num">Bác bỏ</th><th class="num">Chưa rõ</th></tr></thead><tbody>
                ${patterns.map(([n, c]) => `<tr><td>${esc(pretty(n))}</td><td class="num">${c.PENDING || 0}</td><td class="num">${c.CONFIRMED || 0}</td><td class="num">${c.REJECTED || 0}</td><td class="num">${c.INCONCLUSIVE || 0}</td></tr>`).join('')}
            </tbody></table>` : ''}
            <h2>3. Vi phạm đã được giám thị xác nhận</h2>
            ${r.confirmed.length ? r.confirmed.map((c, i) => `<div class="violation">
                ${figure(c)}
                <div><strong>${i + 1}. Chỗ ${esc(seat(c.seat_code))} – ${esc(pretty(c.pattern))}</strong><br>
                Bắt đầu ${clock(c.behavior_start_ms)}, ghi nhận ${clock(c.flagged_ms)} (tính từ đầu video) · điểm ưu tiên ${c.risk}<br>
                Xác nhận bởi: ${esc(c.reviewer || '—')} lúc ${dt(c.reviewed_at)}${c.reason_code ? ` · Lý do: ${esc(c.reason_code)}` : ''}${c.note ? `<br>Ghi chú: ${esc(c.note)}` : ''}
                <div class="hash">SHA-256 bằng chứng: ${esc(c.sha256 || 'không có')}</div></div></div>`).join('') : '<p class="empty">Không có vi phạm nào được xác nhận.</p>'}
            <p class="note">AI chỉ đề xuất các đoạn hình cần xem lại; mọi kết luận trong báo cáo này do giám thị đưa ra.
            ${approval ? `<br>Duyệt bởi ${esc(approval.approved_by)} lúc ${dt(approval.approved_at)}${approval.note ? ` – ${esc(approval.note)}` : ''}.` : ''}
            ${frozen ? `<br>Mã kiểm tra nội dung (SHA-256): <span class="hash">${esc(frozen.sha256)}</span>` : ''}</p>
            <div class="signatures"><div><strong>Giám thị</strong><small>(ký, ghi rõ họ tên)</small></div><div></div><div><strong>Trưởng ban coi thi</strong><small>(ký, ghi rõ họ tên)</small></div></div>`;
    }

    function record(d) {
        const c = d.incident;
        document.title = `Biên bản – ${seat(c.seat_code)}`;
        return `${header()}
            <h1>Biên bản ghi nhận sự việc trong phòng thi</h1>
            <p class="sub">${esc(d.exam ? d.exam.name : d.session.name)}</p>
            <div class="lines">
                <p>Phòng thi: <strong>${esc(d.room ? `${d.room.code} – ${d.room.name}` : '—')}</strong> · Ca thi: ${esc(d.session.name)}</p>
                <p>Vị trí ngồi: <strong>${esc(seat(c.seat_code))}</strong> (${esc(c.seat_code || '—')}) · Họ tên thí sinh: <span class="dots"></span> · SBD: <span class="dots" style="min-width:25mm"></span></p>
                <p>Thời điểm (tính từ đầu video ca thi): hành vi bắt đầu ${clock(c.behavior_start_ms)}, được ghi nhận ${clock(c.flagged_ms)}, lần cuối ${clock(c.last_seen_ms)}.</p>
            </div>
            <h2>Mô tả hành vi quan sát được</h2>
            <p>${esc(pretty(c.pattern))}${c.supporting_cues && c.supporting_cues.length ? ': ' + c.supporting_cues.map(esc).join('; ') : ''}.</p>
            ${figure(c)}
            <h2>Kết luận của giám thị</h2>
            <p>Giám thị <strong>${esc(c.reviewer || '—')}</strong> đã xem lại bằng chứng và xác nhận sự việc lúc ${dt(c.reviewed_at)}.${c.reason_code ? ` Lý do: ${esc(c.reason_code)}.` : ''}${c.note ? ` Ghi chú: ${esc(c.note)}` : ''}</p>
            <p>Hình thức xử lý: <span class="dots" style="min-width:120mm"></span></p>
            <p class="note">Bằng chứng video được lưu kèm mã SHA-256 để kiểm tra tính toàn vẹn: <span class="hash">${esc(c.sha256 || 'không có')}</span><br>
            Hệ thống AI chỉ đánh dấu đoạn hình cần xem lại; kết luận vi phạm do giám thị đưa ra.</p>
            <div class="signatures"><div><strong>Thí sinh</strong><small>(ký, ghi rõ họ tên)</small></div><div><strong>Giám thị lập biên bản</strong><small>(ký, ghi rõ họ tên)</small></div><div><strong>Trưởng ban coi thi</strong><small>(ký, ghi rõ họ tên)</small></div></div>`;
    }

    (async () => {
        try {
            if (params.get('incident')) {
                sheet.innerHTML = record(await P2.api(`/api/v1/reports/incidents/${encodeURIComponent(params.get('incident'))}/record`));
            } else if (params.get('session')) {
                const v = params.get('version');
                const data = await P2.api(`/api/v1/reports/sessions/${encodeURIComponent(params.get('session'))}${v ? `?version=${encodeURIComponent(v)}` : ''}`);
                sheet.innerHTML = sessionReport(data.report, data.frozen);
            } else {
                sheet.innerHTML = '<p class="empty">Nothing to print.</p>';
            }
        } catch (err) {
            sheet.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
        }
    })();
})();
