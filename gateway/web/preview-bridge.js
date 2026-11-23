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
  let profile = null, view = 'app', panel = 'settings', activation = 0;
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

  const bottomStyle = document.createElement('style');
  bottomStyle.textContent = `
    html[data-preview-view="bottom"] #widget-root { border: 0 !important; border-radius: 0 !important; }
    html[data-preview-view="bottom"] #widget-root > :not(#pageZoomViewport) { display: none !important; }
    html[data-preview-view="bottom"] #pageZoomViewport { inset: 0 !important; border: 0 !important; }
    html[data-preview-view="bottom"] #pageZoomLayer > :not(#appMain) { display: none !important; }
    html[data-preview-view="bottom"] #appMain { height: 100%; flex: 1 1 auto; }
    html[data-preview-view="bottom"] #imageEditWorkspace { display: flex !important; flex-direction: column !important; width: 100% !important; min-width: 0 !important; max-width: 100% !important; flex: 1 1 auto; overflow: visible; }
    html[data-preview-view="bottom"] #imageEditWorkspace > :not(#imageEditForm) { display: none !important; }
    html[data-preview-view="bottom"] #imageEditForm { width: 100% !important; min-width: 0 !important; max-width: 100% !important; flex: 1 1 auto; border: 0; overflow: visible; }
    html[data-preview-view="bottom"] #imageEditForm > :not(.image-generation-footer) { display: none !important; }
    html[data-preview-view="bottom"] .image-generation-footer { width: 100%; margin-top: auto; }
    html[data-preview-view="bottom"] #imageGenerationControlPanel { max-height: calc(100% - 90px); }
  `;
  document.head.append(bottomStyle);

  function activateBottom(token, attempt = 0) {
    if (token !== activation || view !== 'bottom') return;
    if (!window.__pageInitialized || typeof window.toggleImageGenerationControls !== 'function' || !document.getElementById('pageZoomLayer')) {
      if (attempt < 100) setTimeout(() => activateBottom(token, attempt + 1), 40);
      return;
    }
    window.closeImageGenerationControls?.();
    window.closeImageReasoningMenu?.();
    const prompt = document.getElementById('imageEditPrompt');
    if (prompt && !prompt.value) { prompt.value = '预览：柔和光线下的静物构图'; prompt.dispatchEvent(new Event('input', {bubbles:true})); }
    const status = document.getElementById('imageEditStatus');
    if (status) status.textContent = '界面预览，可展开下方设置';
    if (panel === 'reasoning') window.toggleImageReasoningMenu?.();
    else window.toggleImageGenerationControls?.();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (token === activation) window.parent.postMessage({type:'mirra-app-preview-ready', tierId:profile?.tier?.id}, '*');
    }));
  }
  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== window.location.origin || event.data?.type !== 'mirra-app-models') return;
    profile = event.data.profile;
    view = event.data.view === 'bottom' ? 'bottom' : 'app';
    panel = event.data.panel === 'reasoning' ? 'reasoning' : 'settings';
    document.documentElement.dataset.previewView = view;
    if (event.data.theme === 'light' || event.data.theme === 'dark') {
      localStorage.setItem('theme', event.data.theme);
      document.documentElement.classList.toggle('dark', event.data.theme === 'dark');
    }
    if (window.__pageInitialized) window.applyBackendState(snapshot());
    const token = ++activation;
    if (view === 'bottom') requestAnimationFrame(() => activateBottom(token));
  });
  document.addEventListener('submit', event => {
    if (!['keyForm', 'imageEditForm'].includes(event.target.id)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    window.showToast?.('当前使用模拟请求的预览环境', 'info');
  }, true);
})();
