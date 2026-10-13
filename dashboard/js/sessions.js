/* 07 Exams & sessions. */
(function () {
    const { $, api, esc, chip, fmt, local } = P2;
    const state = { exams: [], exam: null, sessions: [], people: [], rooms: [], current: null };
    const NO_EXAM = 'none';

    function setError(message) {
        $('pageError').textContent = message || '';
        $('pageError').hidden = !message;
    }

    // --- Exams ----------------------------------------------------------------
    async function loadExams() {
        state.exams = await api('/api/v1/exams');
        const saved = new URLSearchParams(location.search).get('exam');
        if (!state.exam) state.exam = saved || (state.exams[0] ? state.exams[0].id : NO_EXAM);
        $('examCount').textContent = state.exams.length;
        const items = state.exams.map((e) => `
            <button type="button" class="p2-item" data-exam="${esc(e.id)}" aria-current="${e.id === state.exam}">
                <span class="p2-item-title">${esc(e.name)}</span>
                <span class="p2-item-meta">${esc(e.exam_date || 'no date')} · ${e.session_count} sessions${e.pending ? ` · <b class="p2-warn">${e.pending} pending</b>` : ''}${e.status === 'ARCHIVED' ? ' · archived' : ''}</span>
            </button>`);
        items.push(`
            <button type="button" class="p2-item p2-item--muted" data-exam="${NO_EXAM}" aria-current="${state.exam === NO_EXAM}">
                <span class="p2-item-title">Sessions outside an exam</span>
                <span class="p2-item-meta">demo runs and older sessions</span>
            </button>`);
        $('examList').innerHTML = items.join('');
        $('examList').querySelectorAll('[data-exam]').forEach((btn) => btn.addEventListener('click', () => selectExam(btn.dataset.exam)));
    }

    function selectExam(id) {
        state.exam = id;
        const url = new URL(location.href);
        url.searchParams.set('exam', id);
        history.replaceState(null, '', url);
        $('examList').querySelectorAll('[data-exam]').forEach((b) => b.setAttribute('aria-current', String(b.dataset.exam === id)));
        loadSessions();
    }

    // --- Sessions ----------------------------------------------------------------
    async function loadSessions() {
        const exam = state.exams.find((e) => e.id === state.exam);
        $('examTitle').textContent = exam ? exam.name : 'Sessions outside an exam';
        $('examMeta').textContent = exam ? [exam.subject_code, exam.exam_date].filter(Boolean).join(' · ') : 'most recent 150';
        $('scheduleBtn').hidden = !exam;
        $('archiveBtn').hidden = !exam;
        $('archiveBtn').textContent = exam && exam.status === 'ARCHIVED' ? 'Unarchive' : 'Archive';
        $('examReportLink').hidden = !exam;
        if (exam) $('examReportLink').href = `/reports?tab=exam&exam=${encodeURIComponent(exam.id)}`;
        const data = await api(`/api/v1/exam-sessions?exam_id=${encodeURIComponent(state.exam)}&limit=150`);
        state.sessions = data.items;
        if (!data.items.length) {
            $('sessionRows').innerHTML = `<tr><td colspan="8" class="vg-empty">${exam ? 'No sessions yet. Use “Schedule sessions”.' : 'No sessions.'}</td></tr>`;
            return;
        }
        $('sessionRows').innerHTML = data.items.map((s) => {
            const when = s.scheduled_start
                ? `${fmt(local(s.scheduled_start))}–${fmt(local(s.scheduled_end), false)}`
                : (s.started_at ? `ran ${fmt(P2.utc(s.started_at))}` : '—');
            const status = s.is_live ? chip('LIVE', 'warning') : chip(s.status);
            const report = s.report_version ? ` <span class="vg-label">report v${s.report_version}</span>` : '';
            return `<tr data-id="${esc(s.id)}">
                <td class="strong">${esc(s.name)}</td>
                <td style="font-family:var(--vigil-font-mono)">${esc(s.room_code || '—')}</td>
                <td>${esc(when)}</td>
                <td>${esc(s.proctor ? s.proctor.display_name : '—')}</td>
                <td>${status}${report}</td>
                <td class="num">${s.total_events}</td>
                <td class="num">${s.pending ? (s.status === 'CLOSED' ? `<span class="vg-label" title="Left undecided when the report was approved">${s.pending} closed</span>` : `<b class="p2-warn">${s.pending}</b>`) : 0}</td>
                <td class="num p2-row-actions">
                    <button type="button" class="vigil-btn vigil-btn--sm" data-open>Manage</button>
                    <a class="vigil-btn vigil-btn--sm vigil-btn--ghost" href="/playback?session=${encodeURIComponent(s.id)}">Playback</a>
                    <a class="vigil-btn vigil-btn--sm vigil-btn--ghost" href="/reports?session=${encodeURIComponent(s.id)}">Report</a>
                </td></tr>`;
        }).join('');
        $('sessionRows').querySelectorAll('[data-open]').forEach((btn) => {
            btn.addEventListener('click', () => openSession(btn.closest('tr').dataset.id));
        });
    }

    // --- Session dialog ------------------------------------------------------------
    function fillPeople(select, selected) {
        select.replaceChildren(new Option('— none —', ''));
        state.people.forEach((p) => select.append(new Option(`${p.display_name} (${p.role.toLowerCase()})`, p.id, false, p.id === selected)));
    }

    async function openSession(id) {
        const dialog = $('checkDialog');
        P2.showError(dialog, '');
        const s = await api(`/api/v1/exam-sessions/${encodeURIComponent(id)}`);
        state.current = s;
        $('checkTitle').textContent = s.name;
        $('checkStatus').outerHTML = `<span id="checkStatus">${s.is_live ? chip('LIVE', 'warning') : chip(s.status)}</span>`;
        $('checkItems').innerHTML = s.readiness.items.map((i) => `
            <li data-ok="${i.ok}" data-blocking="${i.blocking}">
                <span class="p2-check-mark">${i.ok ? '✓' : (i.blocking ? '✕' : '!')}</span>
                <span><strong>${esc(i.label)}</strong><small>${esc(i.detail)}</small></span>
            </li>`).join('');
        $('ckExam').replaceChildren(new Option('— no exam —', ''));
        state.exams.forEach((e) => $('ckExam').append(new Option(e.name, e.id, false, e.id === s.exam_id)));
        fillPeople($('ckProctor'), s.proctor ? s.proctor.id : '');
        const scheduled = s.status === 'DRAFT' || s.status === 'READY';
        $('ckStartLive').hidden = !scheduled;
        $('ckStartLive').disabled = !s.readiness.ready;
        $('ckStop').hidden = !s.is_live;
        $('ckMonitor').hidden = !s.is_live;
        $('ckDelete').hidden = !(scheduled && !s.total_events);
        $('ckExam').disabled = $('ckProctor').disabled = $('ckSave').disabled = s.status === 'CLOSED';
        if (!dialog.open) dialog.showModal();
    }

    async function sessionAction(fn) {
        const dialog = $('checkDialog');
        P2.showError(dialog, '');
        try {
            await fn(state.current);
            await loadSessions();
            if (dialog.open && state.current) await openSession(state.current.id);
        } catch (err) {
            P2.showError(dialog, err.message);
        }
    }

    $('ckSave').addEventListener('click', () => sessionAction((s) => api(`/api/v1/exam-sessions/${s.id}`, {
        method: 'PATCH', body: JSON.stringify({ exam_id: $('ckExam').value || null, proctor_user_id: $('ckProctor').value || null }),
    }).then(loadExams)));
    $('ckStartLive').addEventListener('click', () => sessionAction((s) => api(`/api/v1/exam-sessions/${s.id}/start`, { method: 'POST', body: JSON.stringify({ mode: 'LIVE' }) })));
    $('ckStop').addEventListener('click', () => sessionAction((s) => api(`/api/v1/exam-sessions/${s.id}/stop`, { method: 'POST' })));
    $('ckDelete').addEventListener('click', () => {
        if (!confirm('Delete this scheduled session?')) return;
        sessionAction(async (s) => {
            await api(`/api/v1/exam-sessions/${s.id}`, { method: 'DELETE' });
            state.current = null;
            $('checkDialog').close();
            await loadExams();
        });
    });

    // --- New exam / scheduling ----------------------------------------------------------
    $('newExamBtn').addEventListener('click', () => {
        $('examForm').reset();
        P2.showError($('examDialog'), '');
        $('examDialog').showModal();
    });
    $('examForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        try {
            const exam = await api('/api/v1/exams', {
                method: 'POST',
                body: JSON.stringify({ name: $('exName').value, subject_code: $('exSubject').value || null, exam_date: $('exDate').value || null, description: $('exDesc').value || null }),
            });
            $('examDialog').close();
            state.exam = exam.id;
            await loadExams();
            selectExam(exam.id);
        } catch (err) {
            P2.showError($('examDialog'), err.message);
        }
    });

    $('archiveBtn').addEventListener('click', async () => {
        const exam = state.exams.find((e) => e.id === state.exam);
        if (!exam) return;
        try {
            await api(`/api/v1/exams/${exam.id}`, { method: 'PATCH', body: JSON.stringify({ status: exam.status === 'ARCHIVED' ? 'ACTIVE' : 'ARCHIVED' }) });
            await loadExams();
            loadSessions();
        } catch (err) { setError(err.message); }
    });

    $('scheduleBtn').addEventListener('click', async () => {
        P2.showError($('scheduleDialog'), '');
        state.rooms = await api('/api/v1/rooms-overview');
        $('roomPicker').innerHTML = state.rooms.map((r) => `
            <label class="p2-check-row" data-disabled="${!r.seat_count}">
                <input type="checkbox" value="${esc(r.id)}">
                <span class="strong" style="font-family:var(--vigil-font-mono)">${esc(r.room_code || '—')}</span>
                <span>${esc(r.name)}</span>
                <span class="vg-label">${r.seat_count} seats${r.preset ? ' · video ✓' : ''}</span>
            </label>`).join('') || '<div class="vg-empty">No rooms.</div>';
        fillPeople($('scProctor'), '');
        $('scheduleDialog').showModal();
    });
    $('scheduleForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        const roomIds = [...$('roomPicker').querySelectorAll('input:checked')].map((i) => i.value);
        if (!roomIds.length) { P2.showError($('scheduleDialog'), 'Choose at least one room.'); return; }
        try {
            await api(`/api/v1/exams/${state.exam}/sessions`, {
                method: 'POST',
                body: JSON.stringify({ room_ids: roomIds, scheduled_start: $('scStart').value || null, scheduled_end: $('scEnd').value || null, proctor_user_id: $('scProctor').value || null }),
            });
            $('scheduleDialog').close();
            await loadExams();
            loadSessions();
        } catch (err) {
            P2.showError($('scheduleDialog'), err.message);
        }
    });

    $('refreshBtn').addEventListener('click', () => loadExams().then(loadSessions));
    P2.wireDialogs();

    (async () => {
        try {
            state.people = await api('/api/v1/users/directory');
            await loadExams();
            await loadSessions();
        } catch (err) {
            setError(err.message);
        }
    })();
    setInterval(() => { if (!document.querySelector('dialog[open]')) loadSessions().catch(() => {}); }, 10000);
})();
