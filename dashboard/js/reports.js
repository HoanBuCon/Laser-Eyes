/* 06 Reports: session report (approve → frozen version), exam summary, AI quality. */
(function () {
    const { $, api, esc, chip, clock, pretty, seat, utc, fmt } = P2;
    const params = new URLSearchParams(location.search);
    const state = { tab: params.get('tab') || 'session', report: null, sessions: [], exams: [] };

    function setTab(tab) {
        state.tab = tab;
        document.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.tab === tab)));
        document.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== tab; });
        const url = new URL(location.href);
        url.searchParams.set('tab', tab);
        history.replaceState(null, '', url);
        if (tab === 'exam') loadExamTab();
        if (tab === 'quality') loadQuality();
    }

    function kpi(label, value, hint = '', tone = '') {
        return `<div class="vg-kpi" data-tone="${tone}"><span class="vg-label">${esc(label)}</span><span class="vg-kpi-value">${esc(value)}</span><span class="vg-kpi-hint">${esc(hint)}</span></div>`;
    }

    // --- Session report ----------------------------------------------------------------
    async function loadSessionList() {
        const data = await api('/api/v1/exam-sessions?limit=300');
        state.sessions = data.items.filter((s) => s.total_events > 0 || s.exam_id || s.id === params.get('session'));
        $('rpSession').innerHTML = state.sessions.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} · ${esc(s.room_code || '')} · ${esc(s.status)}</option>`).join('');
        if (params.get('session')) $('rpSession').value = params.get('session');
        if ($('rpSession').value) await loadReport();
        else $('rpConfirmed').innerHTML = '<div class="vg-empty">No sessions yet.</div>';
    }

    async function loadReport() {
        const id = $('rpSession').value;
        const version = $('rpVersion').dataset.session === id ? $('rpVersion').value : '';
        const data = await api(`/api/v1/reports/sessions/${encodeURIComponent(id)}${version ? `?version=${version}` : ''}`);
        state.report = data.report;
        const url = new URL(location.href);
        url.searchParams.set('session', id);
        history.replaceState(null, '', url);
        $('rpVersion').dataset.session = id;
        $('rpVersion').innerHTML = '<option value="">Live (not approved)</option>' + data.versions.map((v) =>
            `<option value="${v.version}">Approved v${v.version} · ${esc(v.approved_by)} · ${fmt(utc(v.approved_at))}</option>`).join('');
        $('rpVersion').value = data.frozen ? String(data.frozen.version) : '';
        $('rpFrozen').hidden = !data.frozen;
        if (data.frozen) {
            const a = state.report.approval || {};
            $('rpFrozen').textContent = `Frozen version ${data.frozen.version}, approved by ${data.frozen.approved_by} on ${fmt(utc(data.frozen.approved_at))}. SHA-256 ${data.frozen.sha256}.${a.note ? ` Note: ${a.note}` : ''}`;
        }
        const v = data.frozen ? `&version=${data.frozen.version}` : '';
        $('rpPrint').href = `/reports/print?session=${encodeURIComponent(id)}${v}`;
        $('rpCsv').href = `/api/v1/reports/sessions/${encodeURIComponent(id)}/incidents.csv`;
        $('rpPlayback').href = `/playback?session=${encodeURIComponent(id)}`;
        renderReport();
    }

    function renderReport() {
        const r = state.report;
        const s = r.session;
        const by = r.incidents.by_review_status;
        // A closed session is reopened (with a reason) before anything changes
        const live = $('rpVersion').value === '';
        $('rpApprove').hidden = !['COMPLETED', 'STOPPED', 'FAILED', 'INTERRUPTED'].includes(s.status) || !live;
        $('rpReopen').hidden = s.status !== 'CLOSED' || !live;
        $('rpKpis').innerHTML = [
            kpi('Incidents', r.incidents.total, 'raised by the AI for review'),
            kpi('Confirmed', by.CONFIRMED || 0, 'recorded violations', by.CONFIRMED ? 'danger' : ''),
            kpi('Rejected', by.REJECTED || 0, 'false alarms'),
            kpi('Pending', by.PENDING || 0, 'still to review', by.PENDING ? 'warning' : 'good'),
            kpi('Avg. review time', r.incidents.avg_review_minutes == null ? '—' : `${r.incidents.avg_review_minutes} min`, 'flag → decision'),
        ].join('');
        const cov = r.coverage;
        const facts = [
            ['Session', s.name],
            ['Exam', r.exam ? `${r.exam.name}${r.exam.exam_date ? ` · ${r.exam.exam_date}` : ''}` : '—'],
            ['Room', r.room ? `${r.room.code} · ${r.room.name}` : '—'],
            ['Proctor', r.proctor || '—'],
            ['Scheduled', s.scheduled_start ? `${fmt(new Date(s.scheduled_start))} – ${fmt(new Date(s.scheduled_end), false)}` : '—'],
            ['Analysed', s.started_at ? `${fmt(utc(s.started_at))} – ${s.ended_at ? fmt(utc(s.ended_at), false) : '…'}` : '—'],
            ['Seat coverage', cov.capacity ? `${cov.seats_with_roi} of ${cov.capacity} seats (${Math.round((cov.ratio || 0) * 100)} %)` : `${cov.seats_with_roi} seats`],
            ['Status', s.status],
        ];
        $('rpFacts').innerHTML = facts.map(([k, v]) => `<tr><td class="vg-label">${esc(k)}</td><td class="strong">${esc(v)}</td></tr>`).join('');
        const patterns = Object.entries(r.incidents.by_pattern);
        $('rpPatterns').innerHTML = patterns.length ? patterns.map(([name, c]) => `<tr><td class="strong">${esc(pretty(name))}</td>
            <td class="num">${c.PENDING || 0}</td><td class="num">${c.CONFIRMED || 0}</td><td class="num">${c.REJECTED || 0}</td><td class="num">${c.INCONCLUSIVE || 0}</td></tr>`).join('')
            : '<tr><td colspan="5" class="vg-empty">No incidents.</td></tr>';
        $('rpConfirmedCount').textContent = r.confirmed.length;
        $('rpConfirmed').innerHTML = r.confirmed.length ? r.confirmed.map((c) => `
            <article class="rp-violation">
                ${c.snapshot_url ? `<img src="${esc(c.snapshot_url)}" alt="Evidence frame" loading="lazy">` : '<div class="rp-noimg">no image</div>'}
                <div class="rp-violation-body">
                    <strong>${esc(seat(c.seat_code))} · ${esc(pretty(c.pattern))}</strong>
                    <small>${clock(c.behavior_start_ms)} → flagged ${clock(c.flagged_ms)} · risk ${c.risk}</small>
                    <small>Confirmed by ${esc(c.reviewer || '—')}${c.reason_code ? ` · ${esc(c.reason_code)}` : ''}${c.note ? ` · “${esc(c.note)}”` : ''}</small>
                    <small class="rp-hash">SHA-256 ${esc(c.sha256 || 'not recorded')}</small>
                </div>
                <a class="vigil-btn vigil-btn--sm" target="_blank" href="/reports/print?incident=${encodeURIComponent(c.id)}">Record</a>
            </article>`).join('') : '<div class="vg-empty">No confirmed violations.</div>';
    }

    $('rpApprove').addEventListener('click', () => {
        const pending = state.report.incidents.by_review_status.PENDING || 0;
        $('approveText').textContent = pending
            ? `${pending} incidents are still pending. You can approve anyway with a note explaining why.`
            : 'Every incident has a decision. The report is frozen with a SHA-256 hash and the session is closed.';
        $('approveNote').value = '';
        P2.showError($('approveDialog'), '');
        $('approveDialog').showModal();
    });
    $('approveForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        try {
            const res = await api(`/api/v1/reports/sessions/${encodeURIComponent($('rpSession').value)}/approve`, {
                method: 'POST', body: JSON.stringify({ note: $('approveNote').value }),
            });
            $('approveDialog').close();
            $('rpVersion').dataset.session = $('rpSession').value;
            await loadReport();
            $('rpVersion').value = String(res.version);
            await loadReport();
        } catch (err) {
            const msg = /Sign in|role/.test(err.message) ? `${err.message} Approving needs a signed-in chief proctor.` : err.message;
            P2.showError($('approveDialog'), msg);
        }
    });
    $('rpReopen').addEventListener('click', () => {
        $('reopenReason').value = '';
        P2.showError($('reopenDialog'), '');
        $('reopenDialog').showModal();
    });
    $('reopenForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        try {
            await api(`/api/v1/reports/sessions/${encodeURIComponent($('rpSession').value)}/reopen`, {
                method: 'POST', body: JSON.stringify({ reason: $('reopenReason').value }),
            });
            $('reopenDialog').close();
            $('rpVersion').value = '';
            await loadReport();
        } catch (err) {
            const msg = /Sign in|role/.test(err.message) ? `${err.message} Reopening needs a signed-in chief proctor.` : err.message;
            P2.showError($('reopenDialog'), msg);
        }
    });
    $('rpSession').addEventListener('change', () => { $('rpVersion').dataset.session = ''; loadReport(); });
    $('rpVersion').addEventListener('change', loadReport);

    // --- Exam summary ----------------------------------------------------------------------
    function qualityTable(head, body, rows) {
        head.innerHTML = '<tr><th>Pattern</th><th class="num">Total</th><th class="num">Confirmed</th><th class="num">Rejected</th><th class="num">Inconclusive</th><th class="num">Pending</th><th>Precision</th></tr>';
        body.innerHTML = rows.length ? rows.map((q) => `<tr><td class="strong">${esc(pretty(q.pattern))}</td>
            <td class="num">${q.total}</td><td class="num">${q.confirmed}</td><td class="num">${q.rejected}</td><td class="num">${q.inconclusive}</td><td class="num">${q.pending}</td>
            <td>${q.precision == null ? '<span class="vg-label">no decisions</span>' : `<div class="rp-precision"><div class="vg-bar" data-tone="${q.precision >= 0.6 ? 'good' : q.precision >= 0.3 ? 'warning' : 'danger'}"><i style="width:${Math.round(q.precision * 100)}%"></i></div><span>${Math.round(q.precision * 100)} %</span></div>`}</td></tr>`).join('')
            : '<tr><td colspan="7" class="vg-empty">No incidents.</td></tr>';
    }

    async function loadExamTab() {
        if (!state.exams.length) {
            state.exams = await api('/api/v1/exams');
            $('rpExam').innerHTML = state.exams.map((e) => `<option value="${esc(e.id)}">${esc(e.name)}${e.exam_date ? ` · ${esc(e.exam_date)}` : ''}</option>`).join('');
            if (params.get('exam')) $('rpExam').value = params.get('exam');
        }
        if (!$('rpExam').value) {
            $('exRows').innerHTML = '<tr><td colspan="8" class="vg-empty">No exams yet. Create one under Exams &amp; sessions.</td></tr>';
            return;
        }
        const data = await api(`/api/v1/reports/exams/${encodeURIComponent($('rpExam').value)}`);
        const t = data.totals;
        $('exKpis').innerHTML = [
            kpi('Sessions', data.sessions.length), kpi('Incidents', t.total),
            kpi('Confirmed', t.confirmed, '', t.confirmed ? 'danger' : ''), kpi('Rejected', t.rejected),
            kpi('Pending', t.pending, '', t.pending ? 'warning' : 'good'),
        ].join('');
        $('exRows').innerHTML = data.sessions.length ? data.sessions.map((s) => `<tr>
            <td class="strong">${esc(s.name)}</td><td style="font-family:var(--vigil-font-mono)">${esc(s.room_code || '—')}</td><td>${chip(s.status)}</td>
            <td class="num">${s.total}</td><td class="num">${s.pending}</td><td class="num">${s.confirmed}</td><td class="num">${s.rejected}</td>
            <td><a href="/reports?tab=session&session=${encodeURIComponent(s.session_id)}">${s.report_version ? `v${s.report_version}` : 'open'}</a></td></tr>`).join('')
            : '<tr><td colspan="8" class="vg-empty">No sessions in this exam.</td></tr>';
        qualityTable($('exQualityHead'), $('exQuality'), data.ai_quality);
    }
    $('rpExam').addEventListener('change', loadExamTab);

    // --- AI quality ---------------------------------------------------------------------------
    async function loadQuality() {
        const data = await api('/api/v1/reports/ai-quality');
        qualityTable($('qHead'), $('qRows'), data.patterns);
        $('qRejected').innerHTML = data.recent_rejected.length ? data.recent_rejected.map((e) => `<tr>
            <td>${fmt(utc(e.created_at))}</td><td class="strong">${esc(pretty(e.pattern))}</td><td class="num">${e.risk}</td><td>${esc(e.reason_code || '—')}</td>
            <td class="num"><a class="vigil-btn vigil-btn--sm vigil-btn--ghost" href="/playback?session=${encodeURIComponent(e.session_id)}">Playback</a></td></tr>`).join('')
            : '<tr><td colspan="5" class="vg-empty">Nothing rejected yet.</td></tr>';
    }

    document.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));
    P2.wireDialogs();
    loadSessionList().catch((err) => { $('rpConfirmed').innerHTML = `<div class="vg-empty">${esc(err.message)}</div>`; });
    setTab(state.tab);
})();
