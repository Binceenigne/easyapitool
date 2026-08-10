
        const PAGE_ZOOM_STORAGE_KEY = 'api-tools-page-zoom';
        const PAGE_ZOOM_STORAGE_VERSION_KEY = 'api-tools-page-zoom-version';
        const PAGE_ZOOM_STORAGE_VERSION = '2';
        const DEFAULT_PAGE_ZOOM = 1;
        const MIN_PAGE_ZOOM = 0.8;
        const MAX_PAGE_ZOOM = 2;
        const PAGE_ZOOM_STEP = 0.1;

        function normalizePageZoom(value) {
            const numericValue = Number(value);
            const fallback = Number.isFinite(numericValue) ? numericValue : DEFAULT_PAGE_ZOOM;
            return Number(Math.max(
                MIN_PAGE_ZOOM,
                Math.min(MAX_PAGE_ZOOM, Math.round(fallback / PAGE_ZOOM_STEP) * PAGE_ZOOM_STEP)
            ).toFixed(1));
        }

        function getPageZoom() {
            const value = getComputedStyle(document.documentElement).getPropertyValue('--page-zoom');
            return normalizePageZoom(Number.parseFloat(value));
        }

        function pageZoomMetrics() {
            const layer = document.getElementById('pageZoomLayer');
            return {
                layer,
                scale: getPageZoom(),
                rect: layer?.getBoundingClientRect()
            };
        }

        function applyPageZoom(value, persist = false, announce = false) {
            const zoom = normalizePageZoom(value);
            document.documentElement.style.setProperty('--page-zoom', String(zoom));
            if (persist) {
                localStorage.setItem(PAGE_ZOOM_STORAGE_KEY, String(zoom));
                localStorage.setItem(PAGE_ZOOM_STORAGE_VERSION_KEY, PAGE_ZOOM_STORAGE_VERSION);
            }
            requestAnimationFrame(() => {
                handleResponsiveLayout();
                positionKeySwitcherMenu();
                positionImageGenerationPanel();
                positionImageReasoningMenu();
                resizeImagePrompt();
            });
            if (announce) showToast(`页面缩放 ${Math.round(zoom * 100)}%`, 'info');
            return zoom;
        }

        function restorePageZoom() {
            const savedZoom = localStorage.getItem(PAGE_ZOOM_STORAGE_VERSION_KEY) === PAGE_ZOOM_STORAGE_VERSION
                ? localStorage.getItem(PAGE_ZOOM_STORAGE_KEY)
                : DEFAULT_PAGE_ZOOM;
            return applyPageZoom(
                savedZoom || DEFAULT_PAGE_ZOOM,
                true
            );
        }

        function handlePageZoomShortcut(event) {
            if (!event.ctrlKey || event.altKey || event.metaKey) return;
            const zoomIn = event.key === '+' || event.key === '=' || event.code === 'NumpadAdd';
            const zoomOut = event.key === '-' || event.key === '_' || event.code === 'NumpadSubtract';
            const reset = event.key === '0' || event.code === 'Numpad0';
            if (!zoomIn && !zoomOut && !reset) return;
            event.preventDefault();
            const nextZoom = reset
                ? DEFAULT_PAGE_ZOOM
                : getPageZoom() + (zoomIn ? PAGE_ZOOM_STEP : -PAGE_ZOOM_STEP);
            applyPageZoom(nextZoom, true, true);
        }

        function renderLucideIcons() {
            if (!window.lucide || typeof window.lucide.createIcons !== 'function') return;
            try {
                window.lucide.createIcons({
                    attrs: { 'aria-hidden': 'true', focusable: 'false' }
                });
            } catch (error) {
                console.error('Lucide 图标渲染失败:', error);
            }
        }

        function iconMarkup(name, className = '') {
            return `<i data-lucide="${name}" class="${className}" aria-hidden="true"></i>`;
        }

        function setLucideIcon(element, name, className = '') {
            if (!element) return;
            element.setAttribute('data-lucide', name);
            element.setAttribute('class', className);
            renderLucideIcons();
        }

        function setIconLabel(element, name, label, className = 'icon-10') {
            if (!element) return;
            let icon = element.querySelector('[data-lucide]');
            let text = element.querySelector('[data-icon-label]');
            const needsIconRender = !icon || icon.getAttribute('data-lucide') !== name;

            if (!icon || !text) {
                element.replaceChildren();
                icon = document.createElement('i');
                icon.setAttribute('data-lucide', name);
                icon.setAttribute('aria-hidden', 'true');
                text = document.createElement('span');
                text.setAttribute('data-icon-label', '');
                element.append(icon, text);
            } else if (needsIconRender) {
                icon.setAttribute('data-lucide', name);
            }

            icon.setAttribute('class', className);
            text.textContent = label;
            if (needsIconRender || icon.tagName.toLowerCase() !== 'svg') renderLucideIcons();
        }
