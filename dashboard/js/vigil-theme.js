(function () {
  'use strict';

  const STORAGE_KEY = 'vigil-classroom-theme';
  const root = document.documentElement;

  // The DMC5 wordmark font has lowercase letters only: lowercase the
  // "VIGIL AI" wordmark only once the font is really there, so a failed load
  // still shows "VIGIL AI" in the normal font instead of empty boxes.
  function loadWordmarkFont() {
    if (!document.fonts || !document.fonts.load) return;
    document.fonts.load('20px "DMC5"', 'vigil ai').then((faces) => {
      if (faces.length) root.classList.add('font-dmc5');
    }).catch(() => {});
  }
  // The @font-face rule lives in vigil-shell.css: wait until it is parsed
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loadWordmarkFont);
  else loadWordmarkFont();

  function storedTheme() {
    try {
      const value = window.localStorage.getItem(STORAGE_KEY);
      return value === 'light' || value === 'dark' ? value : null;
    } catch (_) {
      return null;
    }
  }

  function preferredTheme() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches
      ? 'light'
      : 'dark';
  }

  function renderThemeControls(theme) {
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      const nextTheme = theme === 'dark' ? 'light' : 'dark';
      button.setAttribute('aria-label', `Switch to ${nextTheme} mode`);
      button.setAttribute('aria-pressed', String(theme === 'light'));
      button.setAttribute('title', `Switch to ${nextTheme} mode`);
      const icon = button.querySelector('[data-theme-icon]');
      const label = button.querySelector('[data-theme-label]');
      if (icon) icon.textContent = theme === 'dark' ? '☀' : '☾';
      if (label) label.textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
    });
  }

  function applyTheme(theme, persist) {
    root.dataset.theme = theme;
    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, theme);
      } catch (_) {
        // Storage can be unavailable in hardened/private browser contexts.
      }
    }
    renderThemeControls(theme);
  }

  applyTheme(storedTheme() || preferredTheme(), false);

  document.addEventListener('DOMContentLoaded', () => {
    renderThemeControls(root.dataset.theme || 'dark');
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      button.addEventListener('click', () => {
        applyTheme(root.dataset.theme === 'light' ? 'dark' : 'light', true);
      });
    });
  });

  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (event) => {
      if (!storedTheme()) applyTheme(event.matches ? 'light' : 'dark', false);
    });
  }
})();
