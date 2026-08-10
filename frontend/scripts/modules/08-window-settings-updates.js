        function handleResponsiveLayout() {
            const root = document.getElementById('widget-root');
            const zoomViewport = document.getElementById('pageZoomViewport');
            const zoomLayer = document.getElementById('pageZoomLayer');
            if (!root || !zoomViewport || !zoomLayer) return;

            const pageZoom = typeof getPageZoom === 'function' ? getPageZoom() : 1;
            const width = Math.max(0, Math.round(zoomViewport.clientWidth / pageZoom));
            const height = Math.max(0, Math.round(zoomViewport.clientHeight / pageZoom));

            root.classList.remove(
                'height-summary-2', 'height-summary-4', 'height-summary-countdown',
                'height-details', 'height-details-metrics', 'height-intervals',
                'height-trend', 'height-full',
                'width-micro', 'width-narrow', 'width-wide'
            );

            const trendHeight = width >= 768 ? 640 : 690;
            const fullHeight = width >= 768 ? 760 : 820;
            const heightClass = height < 170
                ? 'height-summary-2'
                : height < 200
                    ? 'height-summary-4'
                    : height < 360
                        ? 'height-summary-countdown'
                        : height < 460
                            ? 'height-details'
                            : height < 520
                                ? 'height-details-metrics'
                                : height < trendHeight
                                    ? 'height-intervals'
                                    : height < fullHeight
                                        ? 'height-trend'
                                        : 'height-full';
            root.classList.add(heightClass);

            if (width < 340) {
                root.classList.add('width-micro', 'width-narrow');
            } else if (width < 768) {
                root.classList.add('width-narrow');
            } else {
                root.classList.add('width-wide');
            }

            const fullLayoutWidth = width >= 768 ? 768 : 340;
            const fullLayoutHeight = trendHeight;
            const widthGrowth = width >= 768
                ? Math.max(0, (width - fullLayoutWidth) / fullLayoutWidth)
                : 0;
            const heightGrowth = Math.max(0, (height - fullLayoutHeight) / fullLayoutHeight);
            const contentScale = Math.min(1.6, 1 + Math.max(widthGrowth * 0.45, heightGrowth * 0.35));
            root.style.setProperty('--content-scale', contentScale.toFixed(4));
            cancelAnimationFrame(window.__progressResizeFrame || 0);
            window.__progressResizeFrame = requestAnimationFrame(rerenderProgressBars);
        }

        async function requestWindowAction(action) {
            if (!window.pywebview || !window.pywebview.api) return;
            const result = await window.pywebview.api.window_action(action);
            if (action === 'maximize' && result) {
                window.applyWindowState(result.maximized);
            }
        }

        async function toggleAlwaysOnTop() {
            if (!window.pywebview?.api?.set_always_on_top) return;
            const desired = !window.appState.alwaysOnTop;
            const buttons = [...document.querySelectorAll('.always-on-top-button')];
            buttons.forEach(button => { button.disabled = true; });
            try {
                const result = await window.pywebview.api.set_always_on_top(desired);
                if (!result?.ok) throw new Error(result?.error || '无法修改窗口置顶状态');
                window.applyAlwaysOnTopState(result.alwaysOnTop === true);
            } catch (error) {
                showToast(error.message || String(error), 'error');
            } finally {
                buttons.forEach(button => { button.disabled = false; });
            }
        }

        function getActiveKey() {
            if (!window.appState || !window.appState.keys) return null;
            return window.appState.keys.find(k => k.id === window.appState.activeKeyId);
        }

        function selectKey(id) {
            window.appState.activeKeyId = id;
            closeKeySwitcher();
            updateUI();
        }

        function closeKeySwitcher() {
            const button = document.getElementById('keySwitcherButton');
            const menu = document.getElementById('keySwitcherMenu');
            if (!button || !menu) return;
            menu.hidden = true;
            button.setAttribute('aria-expanded', 'false');
            button.classList.remove('is-open');
            const icon = window.appState.activeTitleBarMode === 'minimal' ? 'key-round' : 'chevron-down';
            setLucideIcon(document.getElementById('keySwitcherChevron'), icon, 'icon-10');
        }

        function positionKeySwitcherMenu() {
            const button = document.getElementById('keySwitcherButton');
            const menu = document.getElementById('keySwitcherMenu');
            if (!button || !menu || menu.hidden) return;
            const { layer, scale, rect: layerRect } = pageZoomMetrics();
            if (!layer || !layerRect) return;
            const rect = button.getBoundingClientRect();
            const minimal = window.appState.activeTitleBarMode === 'minimal';
            const width = minimal
                ? Math.min(220, Math.max(150, layer.clientWidth - 12))
                : Math.min(320, Math.max(rect.width / scale, 180));
            const buttonLeft = (rect.left - layerRect.left) / scale;
            const left = Math.min(Math.max(6, buttonLeft), Math.max(6, layer.clientWidth - width - 6));
            menu.style.left = `${left}px`;
            menu.style.width = `${width}px`;
            const menuHeight = menu.offsetHeight;
            const below = (rect.bottom - layerRect.top) / scale + 4;
            const buttonTop = (rect.top - layerRect.top) / scale;
            const top = below + menuHeight <= layer.clientHeight - 6
                ? below
                : Math.max(6, buttonTop - menuHeight - 4);
            menu.style.top = `${top}px`;
        }

        function focusKeySwitcherOption(direction = 0) {
            const options = [...document.querySelectorAll('#keySwitcherMenu [role="option"]')];
            if (!options.length) return;
            const current = options.indexOf(document.activeElement);
            const selected = options.findIndex(option => option.getAttribute('aria-selected') === 'true');
            const base = current >= 0 ? current : Math.max(0, selected);
            options[(base + direction + options.length) % options.length].focus();
        }

        function openKeySwitcher(focusDirection = 0) {
            const button = document.getElementById('keySwitcherButton');
            const menu = document.getElementById('keySwitcherMenu');
            if (!button || !menu || button.disabled) return;
            renderKeySwitcher();
            menu.hidden = false;
            button.setAttribute('aria-expanded', 'true');
            button.classList.add('is-open');
            const icon = window.appState.activeTitleBarMode === 'minimal' ? 'key-round' : 'chevron-up';
            setLucideIcon(document.getElementById('keySwitcherChevron'), icon, 'icon-10');
            positionKeySwitcherMenu();
            if (focusDirection) requestAnimationFrame(() => focusKeySwitcherOption(focusDirection));
        }

        function toggleKeySwitcher() {
            const menu = document.getElementById('keySwitcherMenu');
            if (menu?.hidden) openKeySwitcher();
            else closeKeySwitcher();
        }

        function handleKeySwitcherButtonKeydown(event) {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                openKeySwitcher(event.key === 'ArrowDown' ? 1 : -1);
            } else if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                toggleKeySwitcher();
            }
        }

        function handleKeySwitcherOptionKeydown(event) {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                focusKeySwitcherOption(event.key === 'ArrowDown' ? 1 : -1);
            } else if (event.key === 'Home' || event.key === 'End') {
                event.preventDefault();
                const options = [...document.querySelectorAll('#keySwitcherMenu [role="option"]')];
                options[event.key === 'Home' ? 0 : options.length - 1]?.focus();
            } else if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                const keyId = event.currentTarget.dataset.keyId;
                const key = window.appState.keys.find(item => String(item.id) === keyId);
                if (key) selectKey(key.id);
                document.getElementById('keySwitcherButton')?.focus();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                closeKeySwitcher();
                document.getElementById('keySwitcherButton')?.focus();
            }
        }

        function renderKeySwitcher() {
            const menu = document.getElementById('keySwitcherMenu');
            const button = document.getElementById('keySwitcherButton');
            const label = document.getElementById('keySwitcherLabel');
            if (!menu || !button || !label) return;
            const activeKey = getActiveKey();
            label.textContent = activeKey?.name || '暂无密钥';
            button.disabled = !window.appState.keys.length;
            button.title = activeKey ? `当前密钥：${activeKey.name}` : '暂无密钥';
            const buttonStatus = button.querySelector('.key-switcher-status');
            if (buttonStatus) buttonStatus.className = `key-switcher-status ${keySwitcherStatusClass(activeKey)}`;
            menu.replaceChildren();
            window.appState.keys.forEach(key => {
                const option = document.createElement('button');
                const selected = key.id === window.appState.activeKeyId;
                option.type = 'button';
                option.className = 'key-switcher-option';
                option.setAttribute('role', 'option');
                option.setAttribute('aria-selected', String(selected));
                option.dataset.keyId = String(key.id);
                option.onclick = () => selectKey(key.id);
                option.onkeydown = handleKeySwitcherOptionKeydown;
                const status = document.createElement('span');
                status.className = `key-switcher-status ${keySwitcherStatusClass(key)}`;
                status.setAttribute('aria-hidden', 'true');
                const text = document.createElement('span');
                text.className = 'key-switcher-option-name';
                text.textContent = key.name;
                const check = document.createElement('i');
                check.setAttribute('data-lucide', selected ? 'check' : 'circle');
                check.setAttribute('aria-hidden', 'true');
                option.append(status, text, check);
                menu.append(option);
            });
            renderLucideIcons();
        }

        function keySwitcherStatusClass(key) {
            const status = String(key?.status || '').toLowerCase();
            if (status === 'active') return 'is-active';
            if (['error', 'disabled', 'invalid'].includes(status)) return 'is-error';
            return 'is-unknown';
        }

        const DEVTOOLS_SEQUENCE = 'ddjjyyxx';
        let devtoolsSequenceBuffer = '';

        function isSettingsPanelOpen() {
            return document.getElementById('settingsPanel')?.classList.contains('is-open') === true;
        }

        async function requestDevTools() {
            if (!window.pywebview?.api?.open_devtools) {
                showToast('开发者工具仅在桌面应用中可用', 'info');
                return;
            }
            try {
                const result = await window.pywebview.api.open_devtools();
                if (!result?.ok) throw new Error(result?.error || '无法打开开发者工具');
                showToast('开发者工具已打开', 'info');
            } catch (error) {
                showToast(error.message || String(error), 'error');
            }
        }

            window.benchmark = {
                async open() {
                    if (!window.pywebview?.api?.open_benchmark) {
                        throw new Error('Benchmark 尚未就绪');
                    }
                    return window.pywebview.api.open_benchmark();
                }
            };

        function handleDevToolsSequence(event) {
            if (!isSettingsPanelOpen()) {
                devtoolsSequenceBuffer = '';
                return;
            }
            if (event.ctrlKey || event.altKey || event.metaKey || event.key.length !== 1) return;
            if (event.target instanceof HTMLElement && event.target.closest('input, textarea, select, [contenteditable="true"]')) {
                devtoolsSequenceBuffer = '';
                return;
            }

            devtoolsSequenceBuffer = (devtoolsSequenceBuffer + event.key.toLowerCase())
                .slice(-DEVTOOLS_SEQUENCE.length);
            if (devtoolsSequenceBuffer === DEVTOOLS_SEQUENCE) {
                devtoolsSequenceBuffer = '';
                requestDevTools();
            }
        }

        // 打开/关闭设置浮层
        function toggleSettingsPanel() {
            const panel = document.getElementById('settingsPanel');
            panel.classList.toggle('is-open');
            devtoolsSequenceBuffer = '';
        }

        // 修改并应用主题模式
        function changeThemeMode(mode) {
            const html = document.documentElement;
            const btnLight = document.getElementById('themeBtnLight');
            const btnDark = document.getElementById('themeBtnDark');
            const normalizedMode = mode === 'light' ? 'light' : 'dark';

            if (normalizedMode === 'light') {
                html.classList.remove('dark');
                localStorage.setItem('theme', 'light');
                btnLight.classList.add('is-active');
                btnDark.classList.remove('is-active');
            } else {
                html.classList.add('dark');
                localStorage.setItem('theme', 'dark');
                btnDark.classList.add('is-active');
                btnLight.classList.remove('is-active');
            }
            window.__pendingNativeTheme = normalizedMode;
            window.pywebview?.api?.set_window_background(normalizedMode);
        }

        function updateRateLimitModeButtons() {
            const usedMode = window.appState.rateLimitProgressMode === 'used';
            document.getElementById('rateModeRemaining')?.classList.toggle('is-active', !usedMode);
            document.getElementById('rateModeUsed')?.classList.toggle('is-active', usedMode);
        }

        function applyTitleBarMode(mode) {
            const root = document.getElementById('widget-root');
            const titleBar = document.getElementById('windowTitleBar');
            const toolbar = document.getElementById('keyToolbar');
            const controls = titleBar?.querySelector('.window-controls');
            root.classList.toggle('titlebar-minimal', mode === 'minimal');
            root.classList.toggle('titlebar-original', mode === 'original');
            if (mode === 'minimal' && titleBar && toolbar && controls) {
                titleBar.insertBefore(toolbar, controls);
                const activeKey = getActiveKey();
                toolbar.title = activeKey ? `当前密钥：${activeKey.name}` : '选择密钥';
                setLucideIcon(document.getElementById('keySwitcherChevron'), 'key-round', 'icon-10');
            } else if (toolbar) {
                document.getElementById('pageZoomLayer')?.prepend(toolbar);
                toolbar.removeAttribute('title');
                setLucideIcon(document.getElementById('keySwitcherChevron'), 'chevron-down', 'icon-10');
            }
            closeKeySwitcher();
            requestAnimationFrame(() => {
                handleResponsiveLayout();
                resizeImagePrompt();
            });
        }

        function updateTitleBarModeControls() {
            const selected = window.appState.titleBarMode;
            const active = window.appState.activeTitleBarMode;
            document.getElementById('titleBarDefault')?.classList.toggle('is-active', selected === 'default');
            document.getElementById('titleBarMinimal')?.classList.toggle('is-active', selected === 'minimal');
            document.getElementById('titleBarOriginal')?.classList.toggle('is-active', selected === 'original');
            document.getElementById('titleBarRestartRow').hidden = selected === active;
        }

        async function changeTitleBarMode(mode) {
            const previous = window.appState.titleBarMode;
            window.appState.titleBarMode = ['minimal', 'original'].includes(mode) ? mode : 'default';
            updateTitleBarModeControls();
            try {
                await updateAppPreferences(false);
            } catch (error) {
                window.appState.titleBarMode = previous;
                updateTitleBarModeControls();
            }
        }

        async function changeRateLimitProgressMode(mode) {
            window.appState.rateLimitProgressMode = mode === 'used' ? 'used' : 'remaining';
            updateRateLimitModeButtons();
            updateUI();
            if (!window.pywebview?.api?.update_rate_limit_progress_mode) return;
            try {
                const result = await window.pywebview.api.update_rate_limit_progress_mode(
                    window.appState.rateLimitProgressMode
                );
                window.appState.rateLimitProgressMode = result?.rateLimitProgressMode === 'used'
                    ? 'used'
                    : 'remaining';
                updateRateLimitModeButtons();
                updateUI();
            } catch (error) {
                console.error('保存频率窗口进度逻辑失败:', error);
            }
        }

        // 更新用户设置的警报阈值
        async function updateThresholds() {
            const previous = { ...window.appState.thresholds };
            const warnVal = parseFloat(document.getElementById('thWarn').value) || 50;
            const dangerVal = parseFloat(document.getElementById('thDanger').value) || 25;
            const criticalVal = parseFloat(document.getElementById('thCritical').value) || 10;

            window.appState.thresholds = {
                warn: warnVal,
                danger: dangerVal,
                critical: criticalVal
            };
            updateUI();
            if (!window.pywebview?.api?.update_thresholds) {
                localStorage.setItem('api-tools-thresholds', JSON.stringify(window.appState.thresholds));
                return;
            }
            try {
                const result = await window.pywebview.api.update_thresholds(window.appState.thresholds);
                if (!result?.ok || !result.thresholds) {
                    throw new Error(result?.error || '保存提醒阈值失败');
                }
                window.appState.thresholds = result.thresholds;
                document.getElementById('thWarn').value = result.thresholds.warn;
                document.getElementById('thDanger').value = result.thresholds.danger;
                document.getElementById('thCritical').value = result.thresholds.critical;
                localStorage.setItem('api-tools-thresholds', JSON.stringify(result.thresholds));
                updateUI();
                showToast('提醒阈值已保存');
            } catch (error) {
                window.appState.thresholds = previous;
                document.getElementById('thWarn').value = previous.warn;
                document.getElementById('thDanger').value = previous.danger;
                document.getElementById('thCritical').value = previous.critical;
                updateUI();
                showToast(error.message || String(error), 'error');
            }
        }

        async function updateRefreshIntervals() {
            const foregroundInput = document.getElementById('foregroundRefreshMinutes');
            const backgroundInput = document.getElementById('backgroundRefreshMinutes');
            const foregroundMinutes = Math.max(1, Math.round(Number(foregroundInput.value) || 1));
            const backgroundMinutes = Math.max(5, Math.round(Number(backgroundInput.value) || 5));
            foregroundInput.value = foregroundMinutes;
            backgroundInput.value = backgroundMinutes;
            if (!window.pywebview?.api) return;
            try {
                const result = await window.pywebview.api.update_refresh_intervals(
                    foregroundMinutes * 60,
                    backgroundMinutes * 60
                );
                if (result?.state) window.applyBackendState(result.state);
            } catch (error) {
                console.error('保存刷新周期失败:', error);
            }
        }

        async function updateAppPreferences(showSavedToast = true) {
            const closeAction = document.getElementById('closeAction').value;
            const backgroundUiMode = document.getElementById('backgroundUiMode').value;
            const startupEnabled = document.getElementById('startupEnabled').checked;
            if (!window.pywebview?.api?.update_app_preferences) return;
            try {
                const result = await window.pywebview.api.update_app_preferences(
                    'startup', closeAction, startupEnabled, window.appState.titleBarMode, backgroundUiMode
                );
                if (!result?.ok) throw new Error(result?.error || '保存应用设置失败');
                if (result.state) window.applyBackendState(result.state);
                if (showSavedToast) showToast('应用设置已保存');
            } catch (error) {
                showToast(error.message || String(error), 'error');
                throw error;
            }
        }

        async function restartApp() {
            const button = document.getElementById('restartAppButton');
            if (!window.pywebview?.api?.restart_app || button.disabled) return;
            button.disabled = true;
            try {
                const result = await window.pywebview.api.restart_app();
                if (result && !result.ok) throw new Error(result.error || '无法重启应用');
            } catch (error) {
                button.disabled = false;
                showToast(error.message || String(error), 'error');
            }
        }

        window.applyUpdateState = function(update) {
            if (!update) return;
            const previousLatestVersion = window.appState.update.latestVersion;
            window.appState.update = { ...window.appState.update, ...update };
            const current = window.appState.update;
            if (current.status === 'checking' || current.latestVersion !== previousLatestVersion) {
                window.appState.showFullChangelog = false;
            }
            const busy = current.status === 'checking' || current.status === 'downloading';
            const ready = current.status === 'ready';
            document.getElementById('updateStatusText').textContent = current.message || '尚未检查更新';
            document.getElementById('checkUpdateButton').disabled = busy;
            document.getElementById('downloadUpdateButton').disabled = !current.available || busy;
            document.getElementById('declineUpdateButton').disabled = busy;
            document.getElementById('ignoreUpdateButton').disabled = !current.available || busy;
            document.getElementById('downloadUpdateButton').hidden = ready;
            document.getElementById('declineUpdateButton').hidden = ready;
            document.getElementById('restartLaterButton').hidden = !ready;
            document.getElementById('restartNowButton').hidden = !ready;
            document.getElementById('ignoreUpdateButton').hidden = ready;
            document.getElementById('updateModalTitle').textContent = current.status === 'downloading'
                ? '正在下载更新'
                : current.status === 'ready'
                    ? '下载完成'
                : current.status === 'checking'
                    ? '正在检查更新'
                    : current.status === 'current'
                    ? '已是最新版本'
                    : current.status === 'failed'
                        ? '检查更新失败'
                        : current.available
                            ? '检测到更新'
                            : '更新';
            document.getElementById('updateModalVersion').textContent = current.available
                ? `当前 v${window.appState.appVersion} · 最新 v${current.latestVersion}`
                : `当前版本 v${window.appState.appVersion}`;
            const displayedReleaseNotes = current.status === 'checking'
                ? ''
                : window.appState.showFullChangelog
                    ? current.fullReleaseNotes
                    : current.releaseNotes;
            renderSimpleMarkdown(
                document.getElementById('updateChangelogContent'),
                stripChecksumNotes(displayedReleaseNotes)
                    || (current.status === 'checking' ? '正在获取更新日志。' : '暂无更新日志。')
            );
            const changelogToggle = document.getElementById('toggleFullChangelogButton');
            changelogToggle.hidden = current.status === 'checking'
                || !String(current.fullReleaseNotes || '').trim();
            changelogToggle.textContent = window.appState.showFullChangelog
                ? '收起完整更新日志'
                : '查看完整更新日志';
            const progress = document.getElementById('updateProgress');
            renderProgressBar(progress, busy || ready ? Number(current.percent) || 0 : 0, 100, {
                update: true,
                active: busy
            });
            progress.classList.toggle('is-complete', ready);
            progress.classList.remove('bar-good', 'bar-warn', 'bar-danger', 'bar-critical');
            progress.classList.add(current.status === 'failed' ? 'bar-critical' : 'bar-good');
            if (current.showPrompt) openUpdateModal();
        };

        function toggleFullChangelog() {
            window.appState.showFullChangelog = !window.appState.showFullChangelog;
            window.applyUpdateState({});
        }

        async function checkForUpdates() {
            if (!window.pywebview?.api?.check_for_updates) return;
            window.applyUpdateState({
                status: 'checking', percent: 8, message: '正在检查 GitHub Release', showPrompt: true
            });
            try {
                const result = await window.pywebview.api.check_for_updates();
                if (!result?.ok) throw new Error(result?.error || '无法检查更新');
                if (result.update) window.applyUpdateState(result.update);
            } catch (error) {
                showToast(error.message || String(error), 'error');
            }
        }

        async function downloadUpdate() {
            window.applyUpdateState({
                status: 'downloading', percent: 0, message: '正在连接下载源', showPrompt: true
            });
            try {
                const result = await window.pywebview.api.download_update();
                if (!result?.ok) throw new Error(result?.error || '无法下载更新');
                    while (true) {
                        await new Promise(resolve => setTimeout(resolve, 200));
                        const state = await window.pywebview.api.get_state();
                        if (state?.update) window.applyUpdateState(state.update);
                        if (state?.update?.status !== 'downloading') break;
                    }
            } catch (error) {
                window.applyUpdateState({
                    status: 'failed', percent: 0, message: `更新失败: ${error.message || String(error)}`
                });
                showToast(error.message || String(error), 'error');
            }
        }

        async function restartUpdate() {
            try {
                const result = await window.pywebview.api.restart_update();
                if (!result?.ok) throw new Error(result?.error || '无法重启更新');
            } catch (error) {
                showToast(error.message || String(error), 'error');
            }
        }

        async function deferUpdateRestart() {
            try {
                const result = await window.pywebview.api.defer_update_restart();
                if (!result?.ok) throw new Error(result?.error || '无法延后更新');
                if (result.update) window.appState.update = { ...window.appState.update, ...result.update };
                await closeAnimatedModal(document.getElementById('updateModal'));
                showToast('更新将在下次退出应用时安装', 'info');
            } catch (error) {
                showToast(error.message || String(error), 'error');
            }
        }
