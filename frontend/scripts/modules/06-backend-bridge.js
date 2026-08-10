        try {
            const savedThresholds = JSON.parse(localStorage.getItem('api-tools-thresholds') || 'null');
            if (savedThresholds) {
                window.appState.thresholds = {
                    warn: Number(savedThresholds.warn) || 50,
                    danger: Number(savedThresholds.danger) || 25,
                    critical: Number(savedThresholds.critical) || 10
                };
            }
        } catch (error) {
            console.warn('读取本地设置失败:', error);
        }

        async function runAuthAndInitialize() {
            const loadState = async () => {
                if (!window.pywebview || !window.pywebview.api) return false;
                try {
                    const state = await window.pywebview.api.get_state();
                    window.applyBackendState(state);
                    return true;
                } catch (error) {
                    console.error("本地数据加载失败:", error);
                    return false;
                }
            };

            if (await loadState()) return;
            window.addEventListener('pywebviewready', loadState, { once: true });
        }

        window.applyBackendState = function(state) {
            const previousActiveId = window.appState.activeKeyId;
            window.appState.keys = Array.isArray(state.keys) ? state.keys : [];
            window.appState.thresholds = state.thresholds || window.appState.thresholds;
            window.appState.rateLimitProgressMode = state.rateLimitProgressMode === 'used' ? 'used' : 'remaining';
            window.appState.appVersion = state.appVersion || window.appState.appVersion;
            window.appState.closeAction = state.closeAction || 'ask';
            window.applyAlwaysOnTopState(state.alwaysOnTop === true);
            window.appState.backgroundUiMode = ['delayed', 'active', 'low_power'].includes(state.backgroundUiMode)
                ? state.backgroundUiMode
                : 'delayed';
            window.appState.titleBarMode = ['default', 'minimal', 'original'].includes(state.titleBarMode)
                ? state.titleBarMode
                : 'default';
            window.appState.activeTitleBarMode = ['default', 'minimal', 'original'].includes(state.activeTitleBarMode)
                ? state.activeTitleBarMode
                : window.appState.activeTitleBarMode;
            window.appState.startupEnabled = state.startupEnabled === true;
            window.appState.update = state.update || window.appState.update;
            window.appState.refreshIntervals = state.refreshIntervals || window.appState.refreshIntervals;
            window.appState.refreshCounter = Number(state.nextRefreshSeconds) || (
                state.isForeground
                    ? window.appState.refreshIntervals.foreground
                    : window.appState.refreshIntervals.background
            );
            window.appState.isTabActive = state.isForeground !== false;

            if (previousActiveId && window.appState.keys.some(key => key.id === previousActiveId)) {
                window.appState.activeKeyId = previousActiveId;
            } else {
                window.appState.activeKeyId = window.appState.keys[0]?.id || null;
            }

            document.getElementById('thWarn').value = window.appState.thresholds.warn;
            document.getElementById('thDanger').value = window.appState.thresholds.danger;
            document.getElementById('thCritical').value = window.appState.thresholds.critical;
            updateRateLimitModeButtons();
            document.getElementById('foregroundRefreshMinutes').value = Math.max(
                1, Math.round(window.appState.refreshIntervals.foreground / 60)
            );
            document.getElementById('backgroundRefreshMinutes').value = Math.max(
                5, Math.round(window.appState.refreshIntervals.background / 60)
            );
            document.getElementById('closeAction').value = window.appState.closeAction;
            document.getElementById('backgroundUiMode').value = window.appState.backgroundUiMode;
            document.getElementById('startupEnabled').checked = window.appState.startupEnabled;
            applyTitleBarMode(window.appState.activeTitleBarMode);
            updateTitleBarModeControls();
            document.getElementById('settingsVersionValue').textContent = `v${window.appState.appVersion}`;
            window.applyUpdateState(window.appState.update);
            updateRefreshBadge();
            updateUI();
        };

        window.applyWindowState = function(maximized) {
            const isMaximized = Boolean(maximized);
            const root = document.getElementById('widget-root');
            const button = document.getElementById('maximizeButton');
            const icon = document.getElementById('maximizeIcon');
            root.classList.toggle('window-maximized', isMaximized);
            setLucideIcon(icon, isMaximized ? 'copy' : 'square', 'titlebar-icon');
            button.title = isMaximized ? '还原' : '最大化';
            button.setAttribute('aria-label', isMaximized ? '还原' : '最大化');
            handleResponsiveLayout();
        };

        window.applyAlwaysOnTopState = function(enabled) {
            const active = enabled === true;
            window.appState.alwaysOnTop = active;
            document.querySelectorAll('.always-on-top-button').forEach(button => {
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', String(active));
                button.title = active ? '取消置顶' : '置顶窗口';
                button.setAttribute('aria-label', button.title);
            });
        };

        function updateRefreshBadge() {
            const badge = document.getElementById('tabStatusBadge');
            if (!badge) return;
            if (window.appState.isTabActive) {
                badge.textContent = "前台监控中";
                badge.className = "refresh-state is-foreground";
            } else {
                badge.textContent = "后台闲置";
                badge.className = "refresh-state is-background";
            }
        }

        window.dbSaveKey = async function(keyObject) {
            try {
                const result = await window.pywebview.api.add_key(keyObject.name, keyObject.value);
                if (!result.ok) throw new Error(result.error || "密钥验证失败");
                window.appState.activeKeyId = result.activeKeyId;
                window.applyBackendState(result.state);
            } catch (error) {
                console.error('密钥验证失败:', error);
                throw error;
            }
        }

        window.dbDeleteKey = async function(id) {
            try {
                const result = await window.pywebview.api.delete_key(id);
                window.applyBackendState(result.state);
            } catch (error) {
                console.error('删除密钥失败:', error);
            }
        }

        function reportFrontendStartup(stage) {
            const report = () => window.pywebview?.api?.report_startup(
                stage,
                Math.round(performance.now())
            );
            if (window.pywebview?.api) report();
            else window.addEventListener('pywebviewready', report, { once: true });
        }

        async function syncVisibleBackendState() {
            if (document.visibilityState !== 'visible' || !window.pywebview?.api?.get_state) return;
            try {
                const state = await window.pywebview.api.get_state();
                window.applyBackendState(state);
            } catch (error) {
                console.warn('后台状态同步失败:', error);
            }
        }
