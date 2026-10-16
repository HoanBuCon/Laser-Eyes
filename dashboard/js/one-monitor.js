/*
 * Candidate monitor (VIGIL One): what the desktop app's monitor screen showed, on the web.
 * The candidate's computer only streams its camera; the Hub analyses it and this page shows
 * the live picture with the analysis drawn on it (MJPEG), the readings, the signals, and
 * lets the proctor mark a frame, restart the gaze calibration or allow books / notes.
 */
(function () {
    const $ = (id) => document.getElementById(id);
    const params = new URLSearchParams(location.search);
    const sessionId = params.get('session') || '';
    const enc = encodeURIComponent(sessionId);
    const state = { streamOn: false, overlay: true, ended: false, signalsAt: 0 };

    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    async function api(url, options = {}) {
        const res = await fetch(url, { cache: 'no-store', ...options });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
    }

    function say(text, tone = '') {
        $('mMsg').textContent = text;
        $('mMsg').dataset.tone = tone;
    }

    function setReading(id, text, tone = '') {
        const el = $(id);
        el.textContent = text;
        el.dataset.tone = tone;
    }

    // --- live picture -------------------------------------------------------------------
    function connectStream() {
        $('mLive').src = `/api/v1/one/sessions/${enc}/live.mjpg?overlay=${state.overlay ? 1 : 0}&t=${Date.now()}`;
    }

    function showStream(on) {
        if (on && !state.streamOn) connectStream();  // (re)connect when the stream comes (back)
        if (!on && state.streamOn) $('mLive').removeAttribute('src');
        state.streamOn = on;
        $('mLive').hidden = !on;
        $('mEmpty').hidden = on;
    }

    // --- state --------------------------------------------------------------------------
    async function refresh() {
        let s;
        try {
            s = await api(`/api/v1/one/sessions/${enc}/monitor`);
        } catch (e) {
            $('mEmpty').textContent = e.message;
            showStream(false);
            return;
        }
        const live = s.live || {};
        const a = live.analysis || {};
        const stream = s.stream || {};
        state.ended = s.connection === 'ENDED';
        $('mTitle').textContent = `${s.candidate_ref || 'Candidate'}${s.candidate_name ? ` · ${s.candidate_name}` : ''}`;
        $('mSubtitle').textContent = [s.exam_name, s.simulation ? 'SIMULATION' : '', s.analysis_mode === 'SERVER' ? 'analysed on this Hub' : 'analysed on the candidate computer']
            .filter(Boolean).join(' · ');
        const conn = { LIVE: ['SITTING', 'good'], LOST: ['CONNECTION LOST', 'danger'], ENDED: ['ENDED', ''] }[s.connection] || [s.connection, ''];
        $('mConn').textContent = conn[0];
        $('mConn').dataset.tone = conn[1];

        showStream(Boolean(stream.live));
        if (!stream.live) {
            $('mEmpty').textContent = state.ended ? 'The candidate has submitted; the live picture has ended. The whole exam video is on “Whole exam video”.'
                : (s.connection === 'LOST' ? 'No connection from the candidate’s computer.' : 'Waiting for the candidate’s camera…');
        }
        $('mStreamInfo').textContent = stream.live
            ? `${stream.fps_in} fps in · ${stream.fps_analysed} fps analysed${a.lip_reading_limited ? ' · lips too slow to read speech (Hub busy or weak network)' : ''}`
            : '—';

        const calibrating = a.calibration && a.calibration !== 'READY' && a.calibration !== '—';
        $('mCalib').hidden = !(stream.live && calibrating);
        setReading('rEye', live.eye || '—', String(live.eye || '').startsWith('NGOÀI') ? 'warning' : (live.eye === 'TRONG VÙNG' ? 'good' : ''));
        setReading('rHead', live.head || '—', live.head && !['THẲNG', '—'].includes(live.head) ? 'warning' : '');
        setReading('rPeople', live.people ?? '—', live.people > 1 || live.people === 0 ? 'danger' : (live.people === 1 ? 'good' : ''));
        setReading('rCalib', a.calibration === 'READY' ? 'Ready' : (calibrating ? 'Calibrating…' : '—'), a.calibration === 'READY' ? 'good' : (calibrating ? 'warning' : ''));
        if (s.analysis_mode !== 'SERVER') setReading('rLag', 'on the candidate computer');
        else if (!a.frames) setReading('rLag', 'waiting for video');
        else setReading('rLag', a.lag_s < 5 ? 'live' : `${Math.round(a.lag_s)} s behind`, a.lag_s < 5 ? 'good' : (a.lag_s < 30 ? '' : 'warning'));
        setReading('rStream', stream.live ? 'connected' : (stream.age_s != null ? `stopped ${Math.round(stream.age_s)} s ago` : 'not connected'), stream.live ? 'good' : 'warning');
        setReading('rBeat', s.seconds_since_heartbeat != null ? `${Math.round(s.seconds_since_heartbeat)} s ago` : '—');
        setReading('rSignals', String(s.total_events || 0), s.total_events ? 'warning' : '');
        if (document.activeElement !== $('mMaterials') && a.allow_materials != null) $('mMaterials').checked = Boolean(a.allow_materials);
        $('mUpdated').textContent = new Date().toLocaleTimeString();
        const serverMode = s.analysis_mode === 'SERVER' && !state.ended;
        ['mRecal', 'mMaterials'].forEach((id) => { $(id).disabled = !serverMode; });
        $('mMark').disabled = state.ended;
        $('mVideo').href = `/one-video?session=${enc}`;
        $('mReview').href = `/review?status=ALL&product=ONE&session_id=${enc}`;
        if (s.exam_id) $('mBack').href = `/online?exam=${encodeURIComponent(s.exam_id)}`;
        if (Date.now() - state.signalsAt > 5000 || s.total_events !== state.lastTotal) {
            state.lastTotal = s.total_events;
            loadSignals();
        }
    }

    async function loadSignals() {
        state.signalsAt = Date.now();
        try {
            const q = await api(`/api/v1/proctor/review-queue?status=ALL&product=ONE&limit=100&sort=newest&session_id=${enc}`);
            $('mSignals').innerHTML = q.items.length ? q.items.map((item) => {
                const manual = item.source_type === 'MANUAL_BOOKMARK';
                const img = item.snapshot_url || (manual ? `/api/v1/proctor/bookmarks/${item.bookmark_id}/snapshot` : '');
                const ms = item.first_seen_ms ?? item.source_timestamp_ms;
                const when = ms != null ? `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, '0')}` : '';
                const href = `/review?status=ALL&product=ONE&session_id=${enc}&open=${encodeURIComponent(item.queue_id || '')}`;
                return `<a class="om-signal" href="${esc(href)}">
                    ${img ? `<img src="${esc(img)}" alt="" loading="lazy">` : '<span class="om-noimg"></span>'}
                    <span><strong>${esc(manual ? 'Marked by a proctor' : (item.label || item.primary_signal))}</strong><br><small>${esc(item.reason || item.note || '')}</small></span>
                    <time>${esc(when)}</time>
                    <span class="vg-chip" data-tone="${item.review_status === 'PENDING' ? 'warning' : ''}">${esc(item.review_status)}</span></a>`;
            }).join('') : '<div class="vg-empty">No signal so far.</div>';
        } catch (e) {
            $('mSignals').innerHTML = `<div class="vg-empty">${esc(e.message)}</div>`;
        }
    }

    // --- proctor actions ------------------------------------------------------------------
    async function mark() {
        if ($('mMark').disabled) return;
        $('mMark').disabled = true;
        try {
            await api(`/api/v1/one/sessions/${enc}/mark`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: $('mNote').value.trim() }),
            });
            $('mNote').value = '';
            say('Frame marked. It is in the review queue as a proctor mark.', 'good');
            loadSignals();
        } catch (e) {
            say(e.message, 'danger');
        } finally {
            $('mMark').disabled = state.ended;
        }
    }

    async function recalibrate() {
        if (!window.confirm('Restart the gaze calibration? The candidate’s app asks them to look at the centre of the screen for a few seconds.')) return;
        try {
            await api(`/api/v1/one/sessions/${enc}/recalibrate`, { method: 'POST' });
            say('Calibration restarted.', 'good');
            refresh();
        } catch (e) {
            say(e.message, 'danger');
        }
    }

    async function setMaterials() {
        const allowed = $('mMaterials').checked;
        try {
            await api(`/api/v1/one/sessions/${enc}/materials`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ allowed }),
            });
            say(allowed ? 'Books / notes are allowed for this candidate.' : 'Books / notes are signalled again.', 'good');
        } catch (e) {
            $('mMaterials').checked = !allowed;
            say(e.message, 'danger');
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (!sessionId) {
            $('mEmpty').textContent = 'No candidate chosen. Open one from Online exams or Operations.';
            return;
        }
        $('mMark').addEventListener('click', mark);
        $('mRecal').addEventListener('click', recalibrate);
        $('mMaterials').addEventListener('change', setMaterials);
        $('mOverlay').addEventListener('change', (e) => {
            state.overlay = e.target.checked;
            if (state.streamOn) connectStream();
        });
        $('mNote').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); mark(); } });
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') { e.preventDefault(); mark(); }
        });
        // An MJPEG <img> stops silently when the server ends the stream: reconnect on error
        $('mLive').addEventListener('error', () => { if (state.streamOn) setTimeout(connectStream, 1500); });
        refresh();
        setInterval(refresh, 1500);
    });
})();
