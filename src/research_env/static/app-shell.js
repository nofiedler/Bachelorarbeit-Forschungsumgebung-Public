/* UI-only navigation state. No application records or requests are changed. */
(() => {
  'use strict';
  if (window.researchAppShellInitialized) return;
  window.researchAppShellInitialized = true;

  const preferenceKey = 'research-env.sidebar.collapsed';
  const mobile = window.matchMedia('(max-width: 800px)');
  let collapsed = false;
  let returnFocus = null;
  let focusFrame = null;
  try { collapsed = localStorage.getItem(preferenceKey) === 'true'; } catch (_) { /* Storage is optional. */ }

  const shell = () => document.querySelector('body.app-shell');
  const sidebar = () => document.getElementById('app-sidebar');
  const isOpen = () => shell()?.dataset.mobileOpen === 'true';

  function setCollapsed(value) {
    const body = shell();
    if (!body) return;
    collapsed = value;
    body.dataset.sidebarCollapsed = String(value);
    const toggle = body.querySelector('[data-sidebar-toggle]');
    const label = value ? 'Seitenleiste ausklappen' : 'Seitenleiste einklappen';
    if (toggle) {
      toggle.setAttribute('aria-expanded', String(!value));
      toggle.setAttribute('aria-label', label);
      toggle.title = label;
      const text = toggle.querySelector('.app-toggle-label');
      if (text) text.textContent = label;
    }
  }

  function closeMobile(restoreFocus = true) {
    if (focusFrame !== null) cancelAnimationFrame(focusFrame);
    focusFrame = null;
    const body = shell();
    const panel = sidebar();
    if (!body || !panel) return;
    const wasOpen = isOpen();
    body.dataset.mobileOpen = 'false';
    body.querySelector('[data-sidebar-open]')?.setAttribute('aria-expanded', 'false');
    const backdrop = body.querySelector('[data-sidebar-backdrop]');
    if (backdrop) backdrop.hidden = true;
    panel.removeAttribute('role');
    panel.removeAttribute('aria-modal');
    panel.removeAttribute('aria-label');
    panel.inert = mobile.matches;
    const main = document.getElementById('main-content');
    const bar = body.querySelector('.app-mobile-bar');
    if (main) main.inert = false;
    if (bar) bar.inert = false;
    if (wasOpen && restoreFocus && returnFocus?.isConnected) returnFocus.focus();
    returnFocus = null;
  }

  function openMobile() {
    const body = shell();
    const panel = sidebar();
    if (!body || !panel || !mobile.matches) return;
    returnFocus = document.activeElement;
    body.dataset.mobileOpen = 'true';
    body.querySelector('[data-sidebar-open]')?.setAttribute('aria-expanded', 'true');
    const backdrop = body.querySelector('[data-sidebar-backdrop]');
    if (backdrop) backdrop.hidden = false;
    panel.inert = false;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-label', 'Navigation');
    const main = document.getElementById('main-content');
    const bar = body.querySelector('.app-mobile-bar');
    if (main) main.inert = true;
    if (bar) bar.inert = true;
    // Let visibility and inert changes reach layout before moving focus.
    // Closing, resizing or replacing the page cancels this pending move.
    if (focusFrame !== null) cancelAnimationFrame(focusFrame);
    focusFrame = requestAnimationFrame(() => {
      focusFrame = requestAnimationFrame(() => {
        focusFrame = null;
        if (!mobile.matches || !isOpen() || !panel.isConnected || panel !== sidebar() || panel.inert) return;
        if (panel.contains(document.activeElement)) return;
        const closeButton = panel.querySelector('[data-sidebar-close]');
        if (closeButton?.isConnected && closeButton.getClientRects().length && getComputedStyle(closeButton).visibility === 'visible') {
          closeButton.focus({ preventScroll: true });
        }
      });
    });
  }

  function initialize() {
    const body = shell();
    if (!body || !sidebar()) return;
    body.dataset.shellReady = 'true';
    body.querySelectorAll('[data-sidebar-toggle], [data-sidebar-open], [data-sidebar-close], .app-mobile-bar').forEach(el => { el.hidden = false; });
    setCollapsed(collapsed);
    closeMobile(false);
  }

  document.addEventListener('click', event => {
    if (!(event.target instanceof Element)) return;
    if (event.target.closest('[data-sidebar-toggle]')) {
      setCollapsed(!collapsed);
      try { localStorage.setItem(preferenceKey, String(collapsed)); } catch (_) { /* Storage is optional. */ }
    } else if (event.target.closest('[data-sidebar-open]')) {
      openMobile();
    } else if (event.target.closest('[data-sidebar-close], [data-sidebar-backdrop]')) {
      closeMobile();
    } else if (event.target.closest('.app-navigation a') && isOpen()) {
      closeMobile(false);
    }
  });

  document.addEventListener('keydown', event => {
    if (!isOpen() || !mobile.matches) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMobile();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(sidebar()?.querySelectorAll('button:not([disabled]), a[href]') || [])
      .filter(el => !el.hidden && el.getClientRects().length > 0);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !sidebar().contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !sidebar().contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  });

  mobile.addEventListener('change', () => {
    const focusedInSidebar = sidebar()?.contains(document.activeElement);
    closeMobile(false);
    if (mobile.matches && focusedInSidebar) shell()?.querySelector('[data-sidebar-open]')?.focus();
  });
  document.addEventListener('htmx:afterSwap', event => {
    if (event.detail?.target === document.body || !shell()?.dataset.shellReady) initialize();
  });
  initialize();
})();
