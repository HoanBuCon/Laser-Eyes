/*
 * One Hub, two products: VIGIL Classroom (room cameras) and VIGIL One (a candidate's
 * own computer, analysed on this Hub too).  The switcher at the top of the sidebar
 * picks what the pages show — both, or one product — and every page remembers it.
 *
 *   VIGIL_PRODUCT.get()      'ALL' | 'CLASSROOM' | 'ONE'
 *   VIGIL_PRODUCT.set(value) saves it and fires `vigil:product` on document
 *
 * A link may carry ?product=ONE (e.g. "Review signals" on the online exams page);
 * it becomes the current choice.  Loaded in <head> so pages read it before they fetch.
 */
(function () {
    const KEY = 'vigil.product';
    const VALUES = ['ALL', 'CLASSROOM', 'ONE'];
    const LABELS = { ALL: 'All', CLASSROOM: 'Classroom', ONE: 'One' };
    const TITLES = {
        ALL: 'Both products',
        CLASSROOM: 'VIGIL Classroom: exam rooms watched by room cameras',
        ONE: 'VIGIL One: online exams, each candidate on their own computer',
    };
    // Pages that belong to one product; the rest work for both
    const PAGE_PRODUCT = { '/demo': 'CLASSROOM', '/calibration': 'CLASSROOM', '/online': 'ONE', '/one-video': 'ONE' };

    function normalise(value) {
        const v = String(value || '').toUpperCase();
        return VALUES.includes(v) ? v : null;
    }

    function stored() {
        try { return normalise(localStorage.getItem(KEY)); } catch (e) { return null; }
    }

    let current = normalise(new URLSearchParams(location.search).get('product')) || stored() || 'ALL';
    try { localStorage.setItem(KEY, current); } catch (e) { /* storage blocked */ }
    document.documentElement.dataset.product = current;

    function paint() {
        document.documentElement.dataset.product = current;
        document.querySelectorAll('[data-product-choice]').forEach((button) => {
            button.setAttribute('aria-pressed', String(button.dataset.productChoice === current));
        });
    }

    function set(value) {
        const next = normalise(value);
        if (!next || next === current) return;
        current = next;
        try { localStorage.setItem(KEY, current); } catch (e) { /* storage blocked */ }
        const url = new URL(location.href);
        if (url.searchParams.has('product')) {
            url.searchParams.set('product', current);
            history.replaceState(null, '', url);
        }
        paint();
        document.dispatchEvent(new CustomEvent('vigil:product', { detail: { product: current } }));
    }

    function mount() {
        const sidebar = document.querySelector('.vigil-sidebar');
        if (!sidebar || sidebar.querySelector('.vigil-product')) return;
        sidebar.querySelectorAll('.vigil-nav-item[href]').forEach((link) => {
            const product = PAGE_PRODUCT[link.getAttribute('href')];
            if (product) link.dataset.navProduct = product;
        });
        const group = document.createElement('div');
        group.className = 'vigil-product';
        group.setAttribute('role', 'group');
        group.setAttribute('aria-label', 'Product');
        group.innerHTML = VALUES.map((v) => `<button type="button" data-product-choice="${v}" title="${TITLES[v]}">${LABELS[v]}</button>`).join('');
        group.addEventListener('click', (event) => {
            const button = event.target.closest('[data-product-choice]');
            if (button) set(button.dataset.productChoice);
        });
        const brand = sidebar.querySelector('.vigil-brand');
        if (brand) brand.after(group); else sidebar.prepend(group);
        paint();
    }

    window.VIGIL_PRODUCT = {
        get: () => current,
        set,
        // For APIs that take product=ALL|CLASSROOM|ONE
        param: () => current,
        // Whether a session / review item of `product` ('ONE', 'VIGIL_ONE', 'CLASSROOM', …) is shown
        includes: (product) => current === 'ALL' || (String(product || '').toUpperCase().endsWith('ONE') ? 'ONE' : 'CLASSROOM') === current,
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
    // Another tab changed it
    window.addEventListener('storage', (event) => {
        if (event.key === KEY && normalise(event.newValue)) set(event.newValue);
    });
})();
