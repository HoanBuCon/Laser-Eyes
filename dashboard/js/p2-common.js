/* Small helpers shared by the sessions, playback and report pages. */
const P2 = {
    $: (id) => document.getElementById(id),

    async api(url, options = {}) {
        const res = await fetch(url, { cache: 'no-store', headers: { 'Content-Type': 'application/json' }, ...options });
        if (res.status === 204) return null;
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `HTTP ${res.status}`);
        return data;
    },

    esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    },

    // Server times without a zone are UTC (utcnow); scheduled times are local wall-clock
    utc(iso) { return iso ? new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`) : null; },
    local(iso) { return iso ? new Date(iso) : null; },
    fmt(date, withDate = true) {
        if (!date || Number.isNaN(date.getTime())) return '—';
        return date.toLocaleString([], withDate
            ? { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
            : { hour: '2-digit', minute: '2-digit' });
    },
    clock(ms) {
        if (typeof ms !== 'number') return '—';
        const s = Math.max(0, Math.floor(ms / 1000));
        return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    },
    pretty(name) { return String(name || '').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()); },
    seat(code) { const parts = String(code || '').split('-'); return code ? `S${parts[parts.length - 1]}` : '—'; },

    STATUS_TONE: {
        DRAFT: '', READY: 'good', RUNNING: 'warning', STOPPING: 'warning', COMPLETED: '', STOPPED: '',
        FAILED: 'danger', INTERRUPTED: 'danger', CLOSED: 'good',
        PENDING: 'warning', CONFIRMED: 'danger', REJECTED: 'good', INCONCLUSIVE: '',
    },
    chip(text, tone) {
        return `<span class="vg-chip" data-tone="${tone ?? P2.STATUS_TONE[text] ?? ''}">${P2.esc(text)}</span>`;
    },

    showError(container, message) {
        const box = container.querySelector('.vg-error') || container;
        box.textContent = message || '';
        box.hidden = !message;
    },

    wireDialogs() {
        document.querySelectorAll('dialog [data-close]').forEach((btn) => {
            btn.addEventListener('click', () => btn.closest('dialog').close());
        });
    },
};
