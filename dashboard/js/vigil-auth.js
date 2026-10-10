/*
 * Signed-in user in the sidebar.  When someone is signed in, their name is
 * the reviewer on every page (the server records it the same way).
 */
(function () {
    const SLOT = '[data-user-slot]';

    function escapeText(value) {
        const span = document.createElement('span');
        span.textContent = value == null ? '' : String(value);
        return span.innerHTML;
    }

    function useAsReviewer(user) {
        try { localStorage.setItem('vigil.reviewer', user.display_name); } catch (e) { /* storage blocked */ }
        ['reviewerName', 'actorInput'].forEach((id) => {
            const input = document.getElementById(id);
            if (!input) return;
            input.value = user.display_name;
            input.readOnly = true;
            input.title = 'Signed in as this user';
        });
    }

    function render(slot, info) {
        const next = encodeURIComponent(location.pathname + location.search);
        if (info.user) {
            const u = info.user;
            slot.innerHTML = `
                <div class="vigil-user-copy"><strong>${escapeText(u.display_name)}</strong><small>${escapeText(u.role)}</small></div>
                <div class="vigil-user-actions">
                    ${u.role === 'ADMIN' ? '<a href="/users" title="Accounts">Accounts</a>' : ''}
                    <button type="button" data-sign-out>Sign out</button>
                </div>`;
            slot.querySelector('[data-sign-out]').addEventListener('click', async () => {
                await fetch('/api/v1/auth/logout', { method: 'POST' }).catch(() => {});
                location.href = info.mode === 'required' ? '/login' : location.pathname;
            });
            useAsReviewer(u);
        } else {
            slot.innerHTML = `<a class="vigil-user-signin" href="/login?next=${next}">${info.needs_setup ? 'Set up accounts' : 'Sign in'}</a>`;
        }
    }

    window.VIGIL_AUTH = fetch('/api/v1/auth/me', { cache: 'no-store' })
        .then((res) => (res.ok ? res.json() : { user: null, mode: 'optional' }))
        .catch(() => ({ user: null, mode: 'optional' }))
        .then((info) => {
            window.VIGIL_USER = info.user;
            document.querySelectorAll(SLOT).forEach((slot) => render(slot, info));
            document.dispatchEvent(new CustomEvent('vigil:user', { detail: info }));
            return info;
        });
})();
