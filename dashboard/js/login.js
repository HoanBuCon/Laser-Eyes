/* Sign in, or create the first administrator when no account exists. */
(function () {
    const $ = (id) => document.getElementById(id);
    const params = new URLSearchParams(location.search);
    const next = params.get('next') && params.get('next').startsWith('/') && !params.get('next').startsWith('//')
        ? params.get('next') : '/';
    let setup = false;

    async function init() {
        const info = await fetch('/api/v1/auth/me', { cache: 'no-store' }).then((r) => r.json()).catch(() => ({}));
        if (info.user) { location.replace(next); return; }
        setup = Boolean(info.needs_setup);
        $('modeChip').textContent = info.mode === 'required' ? 'SIGN-IN REQUIRED' : 'OPTIONAL';
        $('skipLink').hidden = info.mode === 'required';
        if (setup) {
            $('loginTitle').textContent = 'Create the administrator';
            $('setupNote').hidden = false;
            $('displayField').hidden = false;
            $('password').autocomplete = 'new-password';
            $('loginBtn').textContent = 'Create and sign in';
        }
        $('username').focus();
    }

    $('loginForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        $('loginError').hidden = true;
        $('loginBtn').disabled = true;
        const body = { username: $('username').value.trim(), password: $('password').value };
        if (setup) body.display_name = $('displayName').value.trim();
        try {
            const res = await fetch(setup ? '/api/v1/auth/setup' : '/api/v1/auth/login', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            location.replace(next);
        } catch (err) {
            $('loginError').textContent = err.message;
            $('loginError').hidden = false;
            $('loginBtn').disabled = false;
        }
    });

    init();
})();
