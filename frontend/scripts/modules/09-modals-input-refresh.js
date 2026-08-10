        const MODAL_ANIMATION_MS = 260;

        function openAnimatedModal(modal) {
            if (!modal) return;
            clearTimeout(modal.__hideTimer);
            modal.hidden = false;
            modal.classList.remove('is-closing');
            modal.classList.add('is-opening');
            requestAnimationFrame(() => requestAnimationFrame(() => {
                modal.classList.remove('is-opening');
                modal.classList.add('is-open');
            }));
        }

        function closeAnimatedModal(modal) {
            if (!modal || modal.hidden) return Promise.resolve();
            clearTimeout(modal.__hideTimer);
            modal.classList.remove('is-opening', 'is-open');
            modal.classList.add('is-closing');
            return new Promise(resolve => {
                modal.__hideTimer = setTimeout(() => {
                    modal.classList.remove('is-closing');
                    modal.hidden = true;
                    resolve();
                }, MODAL_ANIMATION_MS);
            });
        }

        function appendInlineMarkdown(container, text) {
            const parts = String(text).split(/(`[^`]+`)/g);
            parts.forEach(part => {
                if (part.startsWith('`') && part.endsWith('`')) {
                    const code = document.createElement('code');
                    code.textContent = part.slice(1, -1);
                    container.append(code);
                } else {
                    container.append(document.createTextNode(part));
                }
            });
        }

        function renderSimpleMarkdown(container, markdown) {
            container.replaceChildren();
            let list = null;
            String(markdown || '暂无更新日志。').split(/\r?\n/).forEach(line => {
                const heading = line.match(/^(#{1,3})\s+(.+)$/);
                const item = line.match(/^\s*[-*]\s+(.+)$/);
                if (heading) {
                    list = null;
                    const element = document.createElement(`h${heading[1].length}`);
                    appendInlineMarkdown(element, heading[2]);
                    container.append(element);
                } else if (item) {
                    if (!list) {
                        list = document.createElement('ul');
                        container.append(list);
                    }
                    const element = document.createElement('li');
                    appendInlineMarkdown(element, item[1]);
                    list.append(element);
                } else if (line.trim()) {
                    list = null;
                    const element = document.createElement('p');
                    appendInlineMarkdown(element, line.trim());
                    container.append(element);
                } else {
                    list = null;
                }
            });
        }

        function stripChecksumNotes(markdown) {
            const lines = String(markdown || '').split(/\r?\n/);
            const visible = [];
            let skipping = false;
            for (const line of lines) {
                if (/^#{1,3}\s+(下载校验|校验信息)/i.test(line.trim())) {
                    skipping = true;
                    continue;
                }
                if (skipping && /^#{1,3}\s+/.test(line.trim())) skipping = false;
                if (!skipping && !/sha-?256/i.test(line)) visible.push(line);
            }
            return visible.join('\n').trim();
        }

        function openUpdateModal() {
            openAnimatedModal(document.getElementById('updateModal'));
            renderLucideIcons();
        }

        async function closeUpdateModal() {
            if (['downloading', 'ready'].includes(window.appState.update?.status)) return;
            window.appState.update.showPrompt = false;
            await closeAnimatedModal(document.getElementById('updateModal'));
            try {
                const result = await window.pywebview?.api?.dismiss_update_prompt?.();
                if (result?.update) window.appState.update = { ...window.appState.update, ...result.update };
            } catch (error) {
                console.error('关闭更新提示失败:', error);
            }
        }

        async function ignoreCurrentUpdate() {
            const version = window.appState.update?.latestVersion;
            if (!version || !window.pywebview?.api?.ignore_update_version) return;
            try {
                const result = await window.pywebview.api.ignore_update_version(version);
                if (!result?.ok) throw new Error(result?.error || '忽略版本失败');
                if (result.update) window.applyUpdateState(result.update);
                await closeUpdateModal();
            } catch (error) {
                showToast(error.message || String(error), 'error');
            }
        }

        window.openCloseActionModal = function() {
            openAnimatedModal(document.getElementById('closeActionModal'));
            renderLucideIcons();
        };

        async function resolveCloseAction(action) {
            await closeAnimatedModal(document.getElementById('closeActionModal'));
            await window.pywebview?.api?.resolve_close_action(action);
        }

        function handleAppModalBackdrop(event) {
            if (event.target !== event.currentTarget) return;
            if (event.currentTarget.id === 'updateModal') closeUpdateModal();
        }

        const RELIABLE_BUTTON_PRESS_TIMEOUT_MS = 10000;
        const RELIABLE_BUTTON_CLICK_GRACE_MS = 40;
        const RELIABLE_BUTTON_LATE_CLICK_MS = 1500;
        const reliableButtonPressesByPointer = new Map();
        const reliableButtonPressesByButton = new WeakMap();
        const reliableButtonFallbackAt = new WeakMap();
        const reliableButtonDebugHistory = [];

        function reliableButtonLog(event, button, details = {}) {
            const entry = {
                timestamp: new Date().toISOString(),
                performanceMs: Math.round(performance.now()),
                event,
                buttonId: button?.id || '',
                title: button?.title || button?.getAttribute('aria-label') || button?.textContent?.trim() || '',
                ...details
            };
            reliableButtonDebugHistory.push(entry);
            if (reliableButtonDebugHistory.length > 200) reliableButtonDebugHistory.shift();
            console.info('[ReliableButton]', entry);
        }

        window.getReliableButtonDebugLog = () => JSON.parse(JSON.stringify(reliableButtonDebugHistory));
        window.clearReliableButtonDebugLog = () => {
            reliableButtonDebugHistory.length = 0;
        };

        function clearReliableButtonPress(press) {
            if (!press) return;
            clearTimeout(press.timer);
            reliableButtonPressesByPointer.delete(press.pointerId);
            if (reliableButtonPressesByButton.get(press.button) === press) {
                reliableButtonPressesByButton.delete(press.button);
            }
        }

        function dispatchReliableButtonFallback(press, reason) {
            if (!press || reliableButtonPressesByButton.get(press.button) !== press) return;
            clearReliableButtonPress(press);
            if (!press.button.isConnected || press.button.disabled) return;
            reliableButtonFallbackAt.set(press.button, performance.now());
            reliableButtonLog('fallback-click', press.button, { reason, pointerId: press.pointerId });
            press.button.click();
        }

        function finishReliableButtonPress(press, targetButton) {
            if (!press) return;
            clearTimeout(press.timer);
            if (targetButton !== press.button) {
                clearReliableButtonPress(press);
                return;
            }
            press.timer = window.setTimeout(
                () => dispatchReliableButtonFallback(press, 'click-missing'),
                RELIABLE_BUTTON_CLICK_GRACE_MS
            );
        }

        document.addEventListener('pointerdown', event => {
            const button = event.target.closest?.('button');
            if (!button || event.button !== 0 || button.disabled) return;
            clearReliableButtonPress(reliableButtonPressesByButton.get(button));
            const press = {
                button,
                pointerId: event.pointerId,
                timer: 0
            };
            press.timer = window.setTimeout(
                () => clearReliableButtonPress(press),
                RELIABLE_BUTTON_PRESS_TIMEOUT_MS
            );
            reliableButtonPressesByPointer.set(event.pointerId, press);
            reliableButtonPressesByButton.set(button, press);
        }, true);

        document.addEventListener('pointerup', event => {
            const press = reliableButtonPressesByPointer.get(event.pointerId);
            finishReliableButtonPress(press, event.target.closest?.('button'));
        }, true);

        document.addEventListener('mouseup', event => {
            if (event.button !== 0) return;
            const button = event.target.closest?.('button');
            finishReliableButtonPress(reliableButtonPressesByButton.get(button), button);
        }, true);

        document.addEventListener('pointercancel', event => {
            clearReliableButtonPress(reliableButtonPressesByPointer.get(event.pointerId));
        }, true);

        document.addEventListener('click', event => {
            const button = event.target.closest?.('button');
            if (!button) return;
            const activePress = reliableButtonPressesByButton.get(button);
            const fallbackAt = reliableButtonFallbackAt.get(button);
            if (event.detail > 0 && !activePress && fallbackAt
                && performance.now() - fallbackAt < RELIABLE_BUTTON_LATE_CLICK_MS) {
                reliableButtonFallbackAt.delete(button);
                reliableButtonLog('late-click-suppressed', button, { detail: event.detail });
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
            if (event.detail > 0 && activePress) reliableButtonFallbackAt.delete(button);
            clearReliableButtonPress(activePress);
        }, true);

        const MANUAL_REFRESH_FEEDBACK_MS = 700;
        const MANUAL_REFRESH_LOG_PREFIX = '[ManualRefresh]';
        let manualRefreshCooldownTimer = 0;
        let manualRefreshCooldownDeadline = 0;
        let manualRefreshFeedbackTimer = 0;
        let manualRefreshTraceSequence = 0;
        let manualRefreshPendingTraceId = '';
        let manualRefreshActiveTraceId = '';
        let manualRefreshCooldownTraceId = '';
        let manualRefreshLastStateSignature = '';
        const manualRefreshDebugHistory = [];

        function manualRefreshSnapshot() {
            const button = document.getElementById('manualRefreshButton');
            const icon = document.getElementById('manualRefreshIcon');
            const counter = document.getElementById('manualRefreshCountdown');
            return {
                visibility: document.visibilityState,
                disabled: button?.disabled ?? null,
                ariaDisabled: button?.getAttribute('aria-disabled') ?? null,
                classes: button ? [...button.classList] : [],
                title: button?.title || '',
                iconHidden: icon?.hidden ?? null,
                iconDisplay: icon ? getComputedStyle(icon).display : '',
                countdownHidden: counter?.hidden ?? null,
                countdown: counter?.textContent || '',
                cooldownDeadline: manualRefreshCooldownDeadline || 0,
                cooldownRemainingMs: manualRefreshCooldownDeadline
                    ? Math.max(0, manualRefreshCooldownDeadline - Date.now())
                    : 0,
                cooldownTimerActive: Boolean(manualRefreshCooldownTimer),
                feedbackTimerActive: Boolean(manualRefreshFeedbackTimer),
                activeTraceId: manualRefreshActiveTraceId,
                cooldownTraceId: manualRefreshCooldownTraceId
            };
        }

        function manualRefreshLog(traceId, event, details = {}) {
            const entry = {
                timestamp: new Date().toISOString(),
                performanceMs: Math.round(performance.now()),
                traceId: traceId || 'unassigned',
                event,
                ...details,
                snapshot: manualRefreshSnapshot()
            };
            manualRefreshDebugHistory.push(JSON.parse(JSON.stringify(entry)));
            if (manualRefreshDebugHistory.length > 200) manualRefreshDebugHistory.shift();
            console.info(MANUAL_REFRESH_LOG_PREFIX, entry);
        }

        window.getManualRefreshDebugLog = () => JSON.parse(JSON.stringify(manualRefreshDebugHistory));
        window.clearManualRefreshDebugLog = () => {
            manualRefreshDebugHistory.length = 0;
            console.info(MANUAL_REFRESH_LOG_PREFIX, 'debug history cleared');
        };

        function manualRefreshButtonEvent(event, type) {
            const button = event.target.closest?.('#manualRefreshButton');
            if (!button) return;
            if (type === 'pointerdown') {
                if (event.button !== 0) return;
                manualRefreshPendingTraceId = `refresh-${Date.now()}-${++manualRefreshTraceSequence}`;
                manualRefreshLog(
                    manualRefreshPendingTraceId,
                    'button-pointerdown',
                    { detail: event.detail, button: event.button, targetTag: event.target.tagName }
                );
                return;
            }
            if (type === 'click') {
                manualRefreshPendingTraceId = manualRefreshPendingTraceId
                    || `refresh-${Date.now()}-${++manualRefreshTraceSequence}`;
                manualRefreshLog(
                    manualRefreshPendingTraceId,
                    'button-click',
                    {
                        detail: event.detail,
                        button: event.button,
                        targetTag: event.target.tagName,
                        fallback: event.detail === 0
                    }
                );
                void manualRefresh();
            }
        }

        document.addEventListener('pointerdown', event => manualRefreshButtonEvent(event, 'pointerdown'), true);
        document.addEventListener('click', event => manualRefreshButtonEvent(event, 'click'), true);

        function setManualRefreshButtonState(state, countdown = 0, feedback = '', traceId = '') {
            const button = document.getElementById('manualRefreshButton');
            const icon = document.getElementById('manualRefreshIcon');
            const counter = document.getElementById('manualRefreshCountdown');
            if (!button || !icon || !counter) {
                manualRefreshLog(traceId, 'state-elements-missing', {
                    state,
                    buttonFound: Boolean(button),
                    iconFound: Boolean(icon),
                    counterFound: Boolean(counter)
                });
                return;
            }
            const stateClasses = {
                refreshing: 'is-refreshing',
                success: 'is-refresh-success',
                error: 'is-refresh-error',
                cooldown: 'is-refresh-cooldown'
            };
            button.classList.remove('is-refreshing', 'is-refresh-success', 'is-refresh-error', 'is-refresh-cooldown');
            if (stateClasses[state]) button.classList.add(stateClasses[state]);
            if (feedback === 'success') button.classList.add('is-refresh-success');
            if (feedback === 'error') button.classList.add('is-refresh-error');
            button.disabled = state === 'refreshing';
            button.setAttribute('aria-disabled', state !== 'idle' ? 'true' : 'false');
            const cooling = state === 'cooldown';
            icon.hidden = cooling;
            counter.hidden = !cooling;
            counter.textContent = cooling ? String(countdown) : '';
            button.title = state === 'refreshing'
                ? '正在刷新'
                : cooling
                    ? `${countdown} 秒后可再次刷新`
                    : state === 'success'
                        ? '刷新成功'
                        : state === 'error'
                            ? '刷新失败'
                    : '立即刷新';
            button.setAttribute('aria-label', button.title);
            const stateSignature = `${state}|${countdown}|${feedback}`;
            if (stateSignature !== manualRefreshLastStateSignature) {
                manualRefreshLastStateSignature = stateSignature;
                manualRefreshLog(traceId, 'state-changed', { state, countdown, feedback });
            }
        }

        function startManualRefreshCooldown(seconds = 5, feedback = '', traceId = '') {
            clearInterval(manualRefreshCooldownTimer);
            clearTimeout(manualRefreshFeedbackTimer);
            const durationMs = Math.max(0, Number(seconds) || 0) * 1000;
            manualRefreshCooldownDeadline = Date.now() + durationMs;
            manualRefreshCooldownTraceId = traceId;
            manualRefreshLog(traceId, 'cooldown-started', { seconds, durationMs });
            if (durationMs === 0) {
                manualRefreshCooldownDeadline = 0;
                setManualRefreshButtonState('idle', 0, feedback, traceId);
                if (feedback) {
                    manualRefreshFeedbackTimer = window.setTimeout(() => {
                        document.getElementById('manualRefreshButton')?.classList.remove(
                            feedback === 'success' ? 'is-refresh-success' : 'is-refresh-error'
                        );
                        manualRefreshFeedbackTimer = 0;
                        manualRefreshLog(traceId, 'feedback-finished', { feedback });
                        manualRefreshCooldownTraceId = '';
                    }, MANUAL_REFRESH_FEEDBACK_MS);
                } else {
                    manualRefreshCooldownTraceId = '';
                }
                return;
            }
            const updateCooldown = () => {
                const remainingMs = manualRefreshCooldownDeadline - Date.now();
                if (remainingMs <= 0) {
                    clearInterval(manualRefreshCooldownTimer);
                    manualRefreshCooldownTimer = 0;
                    manualRefreshCooldownDeadline = 0;
                    setManualRefreshButtonState('idle', 0, '', traceId);
                    manualRefreshLog(traceId, 'cooldown-finished');
                    manualRefreshCooldownTraceId = '';
                    return;
                }
                setManualRefreshButtonState('cooldown', Math.ceil(remainingMs / 1000), '', traceId);
            };
            setManualRefreshButtonState('cooldown', Math.ceil(durationMs / 1000), feedback, traceId);
            if (feedback) {
                manualRefreshFeedbackTimer = window.setTimeout(() => {
                    document.getElementById('manualRefreshButton')?.classList.remove(
                        feedback === 'success' ? 'is-refresh-success' : 'is-refresh-error'
                    );
                    manualRefreshFeedbackTimer = 0;
                    manualRefreshLog(traceId, 'feedback-finished', { feedback });
                }, MANUAL_REFRESH_FEEDBACK_MS);
            }
            manualRefreshCooldownTimer = window.setInterval(updateCooldown, 200);
        }

        async function manualRefresh() {
            const button = document.getElementById('manualRefreshButton');
            const traceId = manualRefreshPendingTraceId
                || `refresh-${Date.now()}-${++manualRefreshTraceSequence}`;
            manualRefreshPendingTraceId = '';
            manualRefreshLog(traceId, 'handler-entered', {
                bridgeAvailable: Boolean(window.pywebview?.api)
            });
            if (!window.pywebview?.api) {
                manualRefreshLog(traceId, 'blocked-no-bridge');
                return;
            }
            if (button?.disabled) {
                manualRefreshLog(traceId, 'blocked-disabled');
                return;
            }
            manualRefreshActiveTraceId = traceId;
            setManualRefreshButtonState('refreshing', 0, '', traceId);
            const startedAt = performance.now();
            let cooldownSeconds = null;
            let feedback = 'error';
            try {
                manualRefreshLog(traceId, 'bridge-call-started');
                const result = await window.pywebview.api.refresh_now(traceId);
                manualRefreshLog(traceId, 'bridge-call-resolved', {
                    elapsedMs: Math.round(performance.now() - startedAt),
                    result: {
                        ok: result?.ok,
                        valid: result?.valid,
                        busy: result?.busy,
                        cooldownSeconds: result?.cooldownSeconds,
                        refreshedCount: Array.isArray(result?.refreshed) ? result.refreshed.length : 0,
                        failedCount: Array.isArray(result?.failed) ? result.failed.length : 0,
                        error: result?.error || '',
                        backendDebug: result?.debug || null
                    }
                });
                const backendCooldown = Number(result?.cooldownSeconds);
                if (Number.isFinite(backendCooldown)) {
                    cooldownSeconds = Math.max(0, backendCooldown);
                }
                if (result?.state) window.applyBackendState(result.state);
                manualRefreshLog(traceId, 'backend-state-applied', {
                    stateIncluded: Boolean(result?.state)
                });
                const refreshedCount = Array.isArray(result?.refreshed) ? result.refreshed.length : 0;
                const failedCount = Array.isArray(result?.failed) ? result.failed.length : 0;
                const valid = result?.valid === true;
                feedback = valid ? 'success' : 'error';
                showToast(
                    valid
                        ? `已刷新全部 ${refreshedCount} 个密钥`
                        : result?.error || (failedCount
                        ? `已刷新 ${refreshedCount} 个密钥，${failedCount} 个失败`
                        : '未获取到有效回复'),
                    valid ? 'success' : 'error'
                );
            } catch (error) {
                console.error(MANUAL_REFRESH_LOG_PREFIX, {
                    timestamp: new Date().toISOString(),
                    traceId,
                    event: 'bridge-call-rejected',
                    elapsedMs: Math.round(performance.now() - startedAt),
                    errorName: error?.name || '',
                    errorMessage: error?.message || String(error),
                    errorStack: error?.stack || '',
                    snapshot: manualRefreshSnapshot()
                });
                showToast(error.message || String(error), 'error');
            }
            if (cooldownSeconds === null) {
                cooldownSeconds = 5;
                manualRefreshLog(traceId, 'cooldown-defaulted', { cooldownSeconds });
            }
            manualRefreshActiveTraceId = '';
            manualRefreshLog(traceId, 'request-settled', { cooldownSeconds, feedback });
            startManualRefreshCooldown(cooldownSeconds, feedback, traceId);
        }
