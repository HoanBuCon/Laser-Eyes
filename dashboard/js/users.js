/* Accounts (administrators only). */
(function () {
    const $ = (id) => document.getElementById(id);
    const ROLE_LABEL = { PROCTOR: 'Proctor', CHIEF: 'Chief proctor', ADMIN: 'Administrator' };

    async function api(url, options = {}) {
        const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
    }

    function cell(text, className = '') {
        const td = document.createElement('td');
        td.textContent = text;
        if (className) td.className = className;
        return td;
    }

    async function patch(user, body) {
        try {
            await api(`/api/v1/users/${user.id}`, { method: 'PATCH', body: JSON.stringify(body) });
            load();
        } catch (err) {
            alert(err.message);
        }
    }

    function row(user) {
        const tr = document.createElement('tr');
        const role = document.createElement('select');
        role.className = 'vg-select';
        Object.entries(ROLE_LABEL).forEach(([value, label]) => role.append(new Option(label, value, false, value === user.role)));
        role.addEventListener('change', () => patch(user, { role: role.value }));
        const roleCell = document.createElement('td');
        roleCell.append(role);
        const status = document.createElement('td');
        status.innerHTML = `<span class="vg-chip" data-tone="${user.is_active ? 'good' : ''}">${user.is_active ? 'ACTIVE' : 'DISABLED'}</span>`;
        const actions = document.createElement('td');
        actions.className = 'num';
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'vigil-btn vigil-btn--sm';
        toggle.textContent = user.is_active ? 'Disable' : 'Enable';
        toggle.addEventListener('click', () => patch(user, { is_active: !user.is_active }));
        const reset = document.createElement('button');
        reset.type = 'button';
        reset.className = 'vigil-btn vigil-btn--sm vigil-btn--ghost';
        reset.textContent = 'Set password';
        reset.addEventListener('click', () => {
            const password = prompt(`New password for ${user.display_name} (min 8 characters). They will be signed out.`);
            if (password) patch(user, { password });
        });
        actions.append(reset, toggle);
        tr.append(cell(user.display_name, 'strong'), cell(user.username), roleCell, status,
            cell(user.last_login_at ? new Date(`${user.last_login_at}Z`).toLocaleString() : '—'), actions);
        return tr;
    }

    async function load() {
        try {
            const users = await api('/api/v1/users');
            $('usersBody').hidden = false;
            $('denied').hidden = true;
            $('userCount').textContent = `${users.length}`;
            $('userRows').replaceChildren(...users.map(row));
        } catch (err) {
            $('usersBody').hidden = true;
            $('denied').hidden = false;
        }
    }

    $('userForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        $('userError').hidden = true;
        try {
            await api('/api/v1/users', {
                method: 'POST',
                body: JSON.stringify({ display_name: $('uName').value, username: $('uUser').value, role: $('uRole').value, password: $('uPass').value }),
            });
            $('userForm').reset();
            load();
        } catch (err) {
            $('userError').textContent = err.message;
            $('userError').hidden = false;
        }
    });

    load();
})();
