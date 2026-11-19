'use strict';

(() => {
  const storage = new Map();
  Object.defineProperty(window, 'localStorage', {
    value: {
      getItem: key => storage.get(String(key)) ?? null,
      setItem: (key, value) => storage.set(String(key), String(value)),
      removeItem: key => storage.delete(String(key))
    }
  });
  document.documentElement.dataset.platform = 'android';
  document.documentElement.dataset.preview = 'true';
  let profile = null;
  const snapshot = () => ({
    appVersion: 'Android 预览',
    keys: profile ? [{ id: 'preview', name: '预览密钥', status: 'active', mode: 'usd_quota', serverQuota: true, appModels: profile }] : [],
    update: { status: 'idle' }
  });
  const api = {
    get_state: async () => snapshot(),
    list_image_sets: async () => ({ ok: true, sets: [] }),
    refresh_now: async () => ({ ok: true, state: snapshot() }),
    report_startup: async () => ({ ok: true }),
    native_drag: async () => null,
    set_window_background: async () => ({ ok: true })
  };
  window.pywebview = { api };
  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== window.location.origin || event.data?.type !== 'mirra-app-models') return;
    profile = event.data.profile;
    if (window.__pageInitialized) window.applyBackendState(snapshot());
  });
  document.addEventListener('submit', event => {
    if (!['keyForm', 'imageEditForm'].includes(event.target.id)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    window.showToast?.('当前使用模拟请求的预览环境', 'info');
  }, true);
})();
