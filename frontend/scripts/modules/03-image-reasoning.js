        const IMAGE_REASONING_MODES = ['instant', 'flash', 'medium', 'high', 'extra', 'max'];
        const IMAGE_REASONING_LABELS = {
            instant: 'Instant', flash: 'Flash', medium: 'Medium', high: 'High', extra: 'Extra', max: 'Max'
        };
        const IMAGE_REASONING_DESCRIPTIONS = {
            instant: '直接快速获得结果',
            flash: '快速思考，优化生成质量',
            medium: '增强推理，丰富画面细节',
            high: '深入编排方案，强化视觉表现',
            extra: '延长思维链，持续推演与优化',
            max: '使用顶级模型持续反思，获得最优结果'
        };
        const IMAGE_REASONING_TRACK_GRADIENTS = [
            'linear-gradient(90deg, #64748b 0%, #94a3b8 100%)',
            'linear-gradient(90deg, #a9dcff 0%, #86ceff 42%, #a8c9ff 100%)',
            'linear-gradient(90deg, #9fcaff 0%, #8fddff 48%, #b5e8ff 100%)',
            'linear-gradient(90deg, #6b95f4 0%, #5d6ff2 48%, #8170f5 100%)',
            'linear-gradient(90deg, #aeaaff 0%, #c4b7ff 50%, #dfd3ff 100%)',
            'linear-gradient(90deg, #aebcff 0%, #c2b0ff 32%, #dfcdff 64%, #b7ddff 100%)'
        ];
        const IMAGE_REASONING_PULSE_PALETTES = {
            flash: ['#8fd3ff', '#b9e6ff', '#79c7ff', '#9fbfff', '#d8f3ff'],
            medium: ['#4f7df3', '#5b8def', '#5cc8ff', '#9bdcff'],
            high: ['#5865f2', '#6478f5', '#7185f7', '#8170f5'],
            extra: ['#6d5dfc', '#8b6cff', '#b99cff', '#d9c7ff'],
            max: ['#536dfe', '#5b8def', '#8b6cff', '#e9ddff', '#3b82f6']
        };
        const IMAGE_REASONING_STATUS_COLORS = {
            instant: '#27303d', flash: '#0284c7', medium: '#2563eb',
            high: '#5865f2', extra: '#6d5dfc', max: '#536dfe'
        };
        const IMAGE_REASONING_HOVER_PLAYBACK_RATE = 2.5;
        let imageReasoningGradientTimer = 0;
        let imageReasoningPulseTimer = 0;
        let imageReasoningMaxTransitionTimer = 0;
        let activeImageReasoningGradient = IMAGE_REASONING_TRACK_GRADIENTS[0];

        function normalizeImageReasoningMode(mode) {
            const cleanMode = String(mode || 'instant').toLowerCase();
            return IMAGE_REASONING_MODES.includes(cleanMode) ? cleanMode : 'instant';
        }

        function imageReasoningHexToRgba(hex, alpha) {
            const value = hex.replace('#', '');
            const normalized = value.length === 3
                ? value.split('').map(character => character + character).join('')
                : value;
            const number = Number.parseInt(normalized, 16);
            return `rgba(${(number >> 16) & 255}, ${(number >> 8) & 255}, ${number & 255}, ${alpha})`;
        }

        function setImageReasoningAnimationPlaybackRate(playbackRate) {
            const activeLayer = document.querySelector('#imageReasoningControl .reasoning-bg-layer.is-visible');
            activeLayer?.getAnimations({ subtree: true }).forEach(animation => {
                if (typeof animation.updatePlaybackRate === 'function') {
                    animation.updatePlaybackRate(playbackRate);
                } else {
                    animation.playbackRate = playbackRate;
                }
            });
        }

        function initializeImageReasoningHoverPlayback() {
            const generateButton = document.getElementById('generateEditedImageButton');
            if (!generateButton || generateButton.dataset.hoverPlaybackInitialized === 'true') return;
            generateButton.dataset.hoverPlaybackInitialized = 'true';
            generateButton.addEventListener('pointerenter', () => {
                setImageReasoningAnimationPlaybackRate(IMAGE_REASONING_HOVER_PLAYBACK_RATE);
            });
            const restorePlaybackRate = () => setImageReasoningAnimationPlaybackRate(1);
            generateButton.addEventListener('pointerleave', restorePlaybackRate);
            generateButton.addEventListener('pointercancel', restorePlaybackRate);
        }

        function createReasoningColorPulse(target, mode) {
            if (mode === 'instant' || document.hidden || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
            const palette = IMAGE_REASONING_PULSE_PALETTES[mode];
            const layer = target?.querySelector(`.reasoning-bg-layer[data-layer-mode="${mode}"]`);
            if (!target || !palette || !layer) return;
            const color = palette[Math.floor(Math.random() * palette.length)];
            const pulse = document.createElement('span');
            pulse.className = 'reasoning-color-pulse';
            const controlWidth = Math.max(target.clientWidth, 130);
            const controlHeight = Math.max(target.clientHeight, 34);
            const width = 68 + Math.random() * 54;
            const height = 44 + Math.random() * 34;
            const top = -16 + Math.random() * Math.max(18, controlHeight - 3);
            const startX = -width * (1.05 + Math.random() * 0.22);
            const endX = controlWidth + width * (0.95 + Math.random() * 0.28);
            const verticalDrift = -5 + Math.random() * 10;
            const peakAlpha = 0.19 + Math.random() * 0.11;
            const duration = 9800 + Math.random() * 4600;
            pulse.style.width = `${width}px`;
            pulse.style.height = `${height}px`;
            pulse.style.top = `${top}px`;
            pulse.style.left = '0';
            pulse.style.setProperty('--pulse-blur', `${8 + Math.random() * 6}px`);
            pulse.style.background = `radial-gradient(ellipse at center, ${imageReasoningHexToRgba(color, peakAlpha)} 0%, ${imageReasoningHexToRgba(color, peakAlpha * 0.72)} 28%, ${imageReasoningHexToRgba(color, peakAlpha * 0.28)} 52%, ${imageReasoningHexToRgba(color, 0)} 76%)`;
            layer.append(pulse);
            const animation = pulse.animate([
                { transform: `translate3d(${startX}px, 0, 0) scale(0.68)`, opacity: 0 },
                { transform: `translate3d(${startX + (endX - startX) * 0.18}px, ${verticalDrift * 0.15}px, 0) scale(0.88)`, opacity: 0.46, offset: 0.18 },
                { transform: `translate3d(${startX + (endX - startX) * 0.5}px, ${verticalDrift * 0.55}px, 0) scale(1.08)`, opacity: 0.82, offset: 0.5 },
                { transform: `translate3d(${startX + (endX - startX) * 0.8}px, ${verticalDrift * 0.88}px, 0) scale(0.94)`, opacity: 0.38, offset: 0.8 },
                { transform: `translate3d(${endX}px, ${verticalDrift}px, 0) scale(0.72)`, opacity: 0 }
            ], { duration, easing: 'cubic-bezier(0.22, 0.58, 0.34, 1)', fill: 'forwards' });
            if (document.getElementById('generateEditedImageButton')?.matches(':hover')) {
                animation.updatePlaybackRate(IMAGE_REASONING_HOVER_PLAYBACK_RATE);
            }
            animation.addEventListener('finish', () => pulse.remove(), { once: true });
            animation.addEventListener('cancel', () => pulse.remove(), { once: true });
            return { duration };
        }

        function pulseImageGenerationStatusText(mode, duration) {
            const cleanMode = normalizeImageReasoningMode(mode);
            const color = IMAGE_REASONING_STATUS_COLORS[cleanMode];
            const root = document.getElementById('imageGenerationSets');
            if (!color || !root || document.hidden || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
            const rootRect = root.getBoundingClientRect();
            root.querySelectorAll('.image-generation-item:is(.is-queued, .is-running) > span > em').forEach(label => {
                const cardRect = label.parentElement.parentElement.getBoundingClientRect();
                if (cardRect.bottom < rootRect.top - 96 || cardRect.top > rootRect.bottom + 96) return;
                label.imageReasoningPulseAnimation?.cancel();
                label.style.willChange = 'color, text-shadow';
                const animation = label.animate([
                    { color: '#64748b', textShadow: '0 0 0 rgba(0, 0, 0, 0)' },
                    { color, textShadow: `0 0 4px ${imageReasoningHexToRgba(color, 0.24)}`, offset: 0.18 },
                    { color, textShadow: `0 0 10px ${imageReasoningHexToRgba(color, 0.62)}`, offset: 0.5 },
                    { color, textShadow: `0 0 5px ${imageReasoningHexToRgba(color, 0.3)}`, offset: 0.8 },
                    { color: '#64748b', textShadow: '0 0 0 rgba(0, 0, 0, 0)' }
                ], { duration, easing: 'cubic-bezier(0.22, 0.58, 0.34, 1)' });
                label.imageReasoningPulseAnimation = animation;
                const clearWillChange = () => {
                    if (label.imageReasoningPulseAnimation !== animation) return;
                    label.imageReasoningPulseAnimation = null;
                    label.style.willChange = 'auto';
                };
                animation.addEventListener('finish', clearWillChange, { once: true });
                animation.addEventListener('cancel', clearWillChange, { once: true });
            });
        }

        function createImageReasoningColorPulse(mode) {
            const cleanMode = normalizeImageReasoningMode(mode);
            const pulse = createReasoningColorPulse(document.getElementById('imageReasoningControl'), cleanMode);
            if (pulse) pulseImageGenerationStatusText(cleanMode, pulse.duration);
        }

        function scheduleNextImageReasoningPulse(immediate = false) {
            window.clearTimeout(imageReasoningPulseTimer);
            const delay = immediate ? 900 + Math.random() * 900 : 5200 + Math.random() * 3600;
            imageReasoningPulseTimer = window.setTimeout(() => {
                createImageReasoningColorPulse(window.imageEditState.reasoningMode);
                scheduleNextImageReasoningPulse(false);
            }, delay);
        }

        function positionImageReasoningMenu() {
            const menu = document.getElementById('imageReasoningMenu');
            const control = document.getElementById('imageReasoningControl');
            if (!menu || menu.hidden || !control) return;
            const { layer, scale, rect: layerRect } = pageZoomMetrics();
            if (!layer || !layerRect) return;
            const controlRect = control.getBoundingClientRect();
            const menuWidth = Math.min(220, Math.max(0, layer.clientWidth - 16));
            const controlRight = (controlRect.right - layerRect.left) / scale;
            const menuLeft = Math.min(
                layer.clientWidth - menuWidth - 8,
                Math.max(8, controlRight - menuWidth)
            );
            menu.style.setProperty('--reasoning-menu-left', `${menuLeft}px`);
            menu.style.setProperty('--reasoning-menu-width', `${menuWidth}px`);
            const anchorTop = (controlRect.top - layerRect.top) / scale;
            menu.style.setProperty('--reasoning-menu-bottom', `${Math.max(12, layer.clientHeight - anchorTop + 8)}px`);
        }

        function closeImageReasoningMenu() {
            const menu = document.getElementById('imageReasoningMenu');
            const button = document.getElementById('imageReasoningMenuButton');
            if (!menu) return;
            menu.hidden = true;
            menu.classList.remove('is-open');
            button?.setAttribute('aria-expanded', 'false');
        }

        function toggleImageReasoningMenu() {
            const menu = document.getElementById('imageReasoningMenu');
            const button = document.getElementById('imageReasoningMenuButton');
            if (!menu || window.imageEditState.busy) return;
            const opening = menu.hidden;
            if (opening) {
                closeImageGenerationControls();
                const appMain = document.getElementById('appMain');
                if (appMain && menu.parentElement !== appMain) appMain.append(menu);
                menu.hidden = false;
                positionImageReasoningMenu();
                requestAnimationFrame(() => menu.classList.add('is-open'));
            } else {
                closeImageReasoningMenu();
            }
            button?.setAttribute('aria-expanded', String(opening));
        }

        function setImageReasoningMode(mode, persist = true) {
            const cleanMode = normalizeImageReasoningMode(mode);
            const modeIndex = IMAGE_REASONING_MODES.indexOf(cleanMode);
            const previousMode = window.imageEditState.reasoningMode;
            window.imageEditState.reasoningMode = cleanMode;
            const control = document.getElementById('imageReasoningControl');
            const generateButton = document.getElementById('generateEditedImageButton');
            control.dataset.mode = cleanMode;
            generateButton.dataset.reasoningMode = cleanMode;
            control.querySelectorAll('.reasoning-bg-layer').forEach(layer => {
                layer.classList.toggle('is-visible', layer.dataset.layerMode === cleanMode);
            });
            const percentage = modeIndex / (IMAGE_REASONING_MODES.length - 1) * 100;
            const sliderTrack = document.getElementById('reasoningSliderTrack');
            const sliderFill = document.getElementById('sliderTrackFill');
            const sliderThumb = document.getElementById('sliderThumb');
            const sliderCurrent = document.getElementById('sliderGradientCurrent');
            const sliderNext = document.getElementById('sliderGradientNext');
            sliderFill.style.transform = `translate3d(0, 0, 0) scaleX(${percentage / 100})`;
            sliderThumb.style.left = `${percentage}%`;
            sliderTrack.setAttribute('aria-valuenow', String(modeIndex));
            sliderTrack.setAttribute('aria-valuetext', IMAGE_REASONING_LABELS[cleanMode]);
            sliderTrack.querySelectorAll('.slider-dot').forEach((dot, index) => {
                dot.classList.toggle('inactive', index > modeIndex);
            });
            if (cleanMode !== 'max') {
                window.clearTimeout(imageReasoningMaxTransitionTimer);
                sliderTrack.classList.remove('is-max-entering', 'is-max-settled');
            } else if (previousMode !== 'max') {
                window.clearTimeout(imageReasoningMaxTransitionTimer);
                sliderTrack.classList.remove('is-max-entering', 'is-max-settled');
                void sliderTrack.offsetWidth;
                sliderTrack.classList.add('is-max-entering');
                imageReasoningMaxTransitionTimer = window.setTimeout(() => {
                    sliderTrack.classList.remove('is-max-entering');
                    sliderTrack.classList.add('is-max-settled');
                }, 540);
            } else if (!sliderTrack.classList.contains('is-max-entering')) {
                sliderTrack.classList.add('is-max-settled');
            }
            const nextGradient = IMAGE_REASONING_TRACK_GRADIENTS[modeIndex];
            window.clearTimeout(imageReasoningGradientTimer);
            if (nextGradient !== activeImageReasoningGradient) {
                sliderNext.style.background = nextGradient;
                sliderNext.classList.remove('is-blending');
                void sliderNext.offsetWidth;
                sliderNext.classList.add('is-blending');
                imageReasoningGradientTimer = window.setTimeout(() => {
                    sliderCurrent.style.background = nextGradient;
                    sliderNext.classList.remove('is-blending');
                    activeImageReasoningGradient = nextGradient;
                }, 540);
            } else {
                sliderNext.classList.remove('is-blending');
            }
            const badge = document.getElementById('reasoningBadge');
            badge.textContent = IMAGE_REASONING_LABELS[cleanMode];
            badge.dataset.mode = cleanMode;
            document.getElementById('reasoningSliderDesc').textContent = IMAGE_REASONING_DESCRIPTIONS[cleanMode];
            document.getElementById('imageReasoningMenuButton').title = `思维深度：${IMAGE_REASONING_LABELS[cleanMode]}`;
            const searchToggle = document.getElementById('imageWebSearchEnabled');
            searchToggle.disabled = cleanMode === 'instant';
            scheduleNextImageReasoningPulse(true);
            if (persist) persistImageGenerationPreferences();
        }

        function setImageWebSearchEnabled(enabled, persist = true) {
            window.imageEditState.webSearchEnabled = enabled === true;
            const toggle = document.getElementById('imageWebSearchEnabled');
            if (toggle) toggle.checked = window.imageEditState.webSearchEnabled;
            if (persist) persistImageGenerationPreferences();
        }

        function setImageReasoningModeFromPointer(event) {
            if (window.imageEditState.busy) return;
            const sliderTrack = document.getElementById('reasoningSliderTrack');
            const rect = sliderTrack.getBoundingClientRect();
            const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
            setImageReasoningMode(IMAGE_REASONING_MODES[Math.round(fraction * (IMAGE_REASONING_MODES.length - 1))]);
        }

        function initializeImageReasoningSlider() {
            const sliderTrack = document.getElementById('reasoningSliderTrack');
            if (!sliderTrack || sliderTrack.dataset.initialized === 'true') return;
            sliderTrack.dataset.initialized = 'true';
            let dragging = false;
            sliderTrack.addEventListener('pointerdown', event => {
                if (window.imageEditState.busy) return;
                dragging = true;
                sliderTrack.setPointerCapture(event.pointerId);
                setImageReasoningModeFromPointer(event);
            });
            sliderTrack.addEventListener('pointermove', event => {
                if (dragging) setImageReasoningModeFromPointer(event);
            });
            const endDrag = event => {
                dragging = false;
                if (sliderTrack.hasPointerCapture(event.pointerId)) sliderTrack.releasePointerCapture(event.pointerId);
            };
            sliderTrack.addEventListener('pointerup', endDrag);
            sliderTrack.addEventListener('pointercancel', endDrag);
            sliderTrack.addEventListener('keydown', event => {
                if (window.imageEditState.busy) return;
                const currentIndex = IMAGE_REASONING_MODES.indexOf(window.imageEditState.reasoningMode);
                let nextIndex = currentIndex;
                if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') nextIndex -= 1;
                else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') nextIndex += 1;
                else if (event.key === 'Home') nextIndex = 0;
                else if (event.key === 'End') nextIndex = IMAGE_REASONING_MODES.length - 1;
                else return;
                event.preventDefault();
                setImageReasoningMode(IMAGE_REASONING_MODES[Math.max(0, Math.min(IMAGE_REASONING_MODES.length - 1, nextIndex))]);
            });
        }

        async function polishImagePrompt() {
            if (window.imageEditState.polishing || window.imageEditState.busy) return;
            const activeKey = getActiveKey();
            const prompt = document.getElementById('imageEditPrompt');
            const button = document.getElementById('polishImagePromptButton');
            const originalPrompt = prompt.value.trim();
            if (!activeKey) return showToast('请先添加并选择一个有效的 API Key', 'error');
            if (!originalPrompt) return showToast('请输入需要润色的提示词', 'error');
            window.imageEditState.polishing = true;
            button.disabled = true;
            button.classList.add('is-active');
            setLucideIcon(button.querySelector('[data-lucide], svg'), 'loader-circle');
            button.querySelector('[data-lucide], svg')?.classList.add('is-spinning');
            prompt.value = '';
            try {
                const result = await window.pywebview.api.polish_prompt(activeKey.id, originalPrompt);
                if (!result.ok) throw new Error(result.error || '提示词润色失败');
                prompt.value = result.prompt;
                document.getElementById('imagePromptModalTextarea').value = result.prompt;
                resizeImagePrompt();
                showToast('提示词润色完成');
            } catch (error) {
                prompt.value = originalPrompt;
                resizeImagePrompt();
                showToast(error.message || String(error), 'error');
            } finally {
                window.imageEditState.polishing = false;
                button.disabled = false;
                button.classList.remove('is-active');
                setLucideIcon(button.querySelector('[data-lucide], svg'), 'sparkles');
            }
        }

        function createImageGenerationSet(requestId, requestedCount, prompt, metadata = {}) {
            let set = window.imageEditState.resultSets.find(item => item.setId === requestId);
            if (set) return set;
            window.imageEditState.resultSets.forEach(existingSet => {
                if (existingSet.status === 'running') return;
                existingSet.expanded = false;
                existingSet.history = true;
                existingSet.previewsLoaded = false;
                existingSet.loadingPreviews = false;
                existingSet.items.forEach(item => {
                    item.revealFrames = [];
                    item.previewUri = '';
                    item.fullUri = '';
                    if (item.result) item.result.previewUri = '';
                    if (item.result) item.result.fullUri = '';
                });
            });
            set = {
                setId: requestId,
                requestId,
                sessionId: metadata.sessionId || requestId,
                parentSetId: metadata.parentSetId || '',
                roundNumber: Number(metadata.roundNumber) || 1,
                requestedCount,
                prompt,
                originalPrompt: metadata.originalPrompt || prompt,
                operation: metadata.operation || 'generate',
                continuation: metadata.continuation === true,
                continuationRationale: metadata.continuationRationale || '',
                selectedAssetIds: Array.isArray(metadata.selectedAssetIds) ? [...metadata.selectedAssetIds] : [],
                createdAt: metadata.createdAt || new Date().toISOString(),
                status: 'running',
                expanded: metadata.expanded !== false,
                history: metadata.history === true,
                previewsLoaded: metadata.previewsLoaded !== false,
                loadingPreviews: false,
                originalsLoaded: false,
                loadingOriginals: false,
                reasoningStatus: metadata.reasoningStatus || 'idle',
                reasoningMode: normalizeImageReasoningMode(metadata.reasoningMode || window.imageEditState.reasoningMode),
                reasoningModel: metadata.reasoningModel || '',
                reasoningEffort: metadata.reasoningEffort || '',
                reasoningSummary: metadata.reasoningSummary || '',
                reasoningTurns: Array.isArray(metadata.reasoningTurns) ? metadata.reasoningTurns.map(turn => ({ ...turn })) : [],
                reasoningExpanded: metadata.reasoningExpanded === true,
                reasoningContentExpanded: metadata.reasoningContentExpanded === true,
                reasoningStartedAt: Number(metadata.reasoningStartedAt) || 0,
                reasoningCompletedAt: Number(metadata.reasoningCompletedAt) || 0,
                reasoningDurationMs: Math.max(0, Number(metadata.reasoningDurationMs) || 0),
                reasoningUsage: normalizeReasoningUsage(metadata.reasoningUsage),
                effectivePrompt: metadata.effectivePrompt || '',
                webSearchEnabled: metadata.webSearchEnabled === true,
                webSearchStatus: metadata.webSearchFailed
                    ? 'failed'
                    : metadata.webSearchUsed
                        ? 'completed'
                        : metadata.webSearchEnabled && metadata.reasoningStatus === 'completed'
                            ? 'skipped'
                            : 'idle',
                webSearchResultCount: Number(metadata.webSearchResultCount) || 0,
                webReferenceCount: Number(metadata.webReferenceCount) || 0,
                webReferences: Array.isArray(metadata.webReferences) ? metadata.webReferences.map(reference => ({ ...reference })) : [],
                webReferencesExpanded: false,
                finalPromptExpanded: false,
                webReferencesLoading: false,
                items: Array.from({ length: requestedCount }, (_, itemIndex) => ({
                    itemIndex,
                    status: 'queued',
                    uri: '',
                    previewUri: '',
                    fullUri: '',
                    path: '',
                    result: null,
                    error: '',
                    revealFrames: [],
                    originalLoadAttempted: false,
                    partialIndex: 0,
                    partialTotal: 3
                }))
            };
            window.imageEditState.resultSets.unshift(set);
            renderImageGenerationSets();
            return set;
        }

        function imageGenerationSetById(setId) {
            return window.imageEditState.resultSets.find(item => item.setId === setId);
        }

        function reasoningTurnsForSet(set) {
            if (!Array.isArray(set.reasoningTurns)) set.reasoningTurns = [];
            return set.reasoningTurns;
        }

        function reasoningTurnByNumber(set, turnNumber) {
            const cleanTurn = Math.max(1, Number(turnNumber) || 1);
            const turns = reasoningTurnsForSet(set);
            let turn = turns.find(item => item.turn === cleanTurn);
            if (!turn) {
                turn = {
                    turn: cleanTurn,
                    title: cleanTurn === 1 ? '正在分析问题' : '正在完善方案',
                    text: '',
                    status: 'running',
                    startedAt: Date.now(),
                    completedAt: 0,
                    tools: []
                };
                turns.push(turn);
            }
            return turn;
        }

        function activeReasoningTurn(set) {
            const turns = reasoningTurnsForSet(set);
            return [...turns].reverse().find(turn => turn.status === 'running')
                || turns.at(-1)
                || null;
        }

        function formatReasoningDuration(milliseconds) {
            const totalSeconds = Math.max(0, Math.floor(Number(milliseconds) / 1000));
            const minutes = Math.floor(totalSeconds / 60);
            const seconds = totalSeconds % 60;
            return minutes ? `${minutes}m:${String(seconds).padStart(2, '0')}s` : `${totalSeconds}s`;
        }

        function normalizeReasoningUsage(value) {
            const source = value && typeof value === 'object' ? value : {};
            const callCount = Math.max(0, Number(source.callCount) || 0);
            const costedCallCount = Math.max(0, Number(source.costedCallCount) || 0);
            const estimatedCallCount = Math.min(costedCallCount, Math.max(0, Number(source.estimatedCallCount) || 0));
            const costSource = String(source.costSource || '');
            const hasCost = source.hasCost === true
                && ['response', 'estimate', 'mixed'].includes(costSource)
                && callCount > 0
                && costedCallCount === callCount;
            return {
                inputTokens: Math.max(0, Number(source.inputTokens) || 0),
                outputTokens: Math.max(0, Number(source.outputTokens) || 0),
                totalTokens: Math.max(0, Number(source.totalTokens) || 0),
                callCount,
                costUsd: hasCost ? Math.max(0, Number(source.costUsd) || 0) : 0,
                costedCallCount,
                estimatedCallCount,
                costSource: hasCost ? costSource : '',
                hasTokenUsage: source.hasTokenUsage === true,
                hasCost
            };
        }

        function formatReasoningUsage(set) {
            const usage = normalizeReasoningUsage(set.reasoningUsage);
            const tokens = usage.hasTokenUsage ? Math.round(usage.totalTokens).toLocaleString() : '--';
            const calls = usage.callCount ? Math.round(usage.callCount) : '--';
            const cost = usage.hasCost
                ? `${usage.costSource === 'response' ? '' : '≈'}$${usage.costUsd.toFixed(6)}`
                : '$--';
            return `${tokens} token · ${calls} 次调用 · ${cost}`;
        }

        function reasoningUsageTitle(set) {
            const usage = normalizeReasoningUsage(set.reasoningUsage);
            const input = usage.hasTokenUsage ? Math.round(usage.inputTokens).toLocaleString() : '--';
            const output = usage.hasTokenUsage ? Math.round(usage.outputTokens).toLocaleString() : '--';
            const cost = usage.hasCost
                ? usage.costSource === 'response'
                    ? `$${usage.costUsd.toFixed(6)}（接口返回的逐请求费用）`
                    : `≈$${usage.costUsd.toFixed(6)}（OpenAI 官方价目估算，${usage.estimatedCallCount} 次调用采用估算）`
                : '$--（缺少可计价用量，未使用账户总额差值）';
            return `输入 ${input} token · 输出 ${output} token · API 调用 ${usage.callCount || '--'} 次 · 本轮费用 ${cost}`;
        }

        function reasoningElapsed(set, turn = activeReasoningTurn(set)) {
            const startedAt = Number(turn?.startedAt) || Number(set.reasoningStartedAt) || 0;
            const completedAt = Number(turn?.completedAt)
                || Number(set.reasoningCompletedAt)
                || (set.reasoningStatus === 'running' ? Date.now() : 0);
            if (!startedAt || (!completedAt && set.reasoningStatus !== 'running')) {
                return formatReasoningDuration(Number(turn?.durationMs) || Number(set.reasoningDurationMs) || 0);
            }
            return formatReasoningDuration(completedAt - startedAt);
        }

        function totalReasoningElapsed(set) {
            const persistedDuration = Math.max(0, Number(set.reasoningDurationMs) || 0);
            if (persistedDuration && set.reasoningStatus !== 'running') {
                return formatReasoningDuration(persistedDuration);
            }
            const startedAt = Number(set.reasoningStartedAt) || Number(reasoningTurnsForSet(set)[0]?.startedAt) || Date.now();
            const completedAt = Number(set.reasoningCompletedAt) || Date.now();
            return formatReasoningDuration(completedAt - startedAt);
        }

        function escapeHtml(value) {
            const span = document.createElement('span');
            span.textContent = String(value ?? '');
            return span.innerHTML;
        }

        function toggleReasoningPanel(setId) {
            const set = imageGenerationSetById(setId);
            if (!set) return;
            set.reasoningExpanded = !set.reasoningExpanded;
            renderImageGenerationSets();
        }

        function toggleReasoningContent(setId) {
            const set = imageGenerationSetById(setId);
            if (!set) return;
            set.reasoningContentExpanded = !set.reasoningContentExpanded;
            renderImageGenerationSets();
        }

        function toggleFinalPrompt(setId) {
            const set = imageGenerationSetById(setId);
            if (!set?.effectivePrompt) return;
            set.finalPromptExpanded = !set.finalPromptExpanded;
            if (set.finalPromptExpanded) set.webReferencesExpanded = false;
            renderImageGenerationSets();
        }

        async function copyImagePrompt(text, label) {
            const value = String(text || '').trim();
            if (!value) {
                showToast(`${label}为空`, 'error');
                return false;
            }
            try {
                let copied = false;
                if (navigator.clipboard?.writeText) {
                    try {
                        await navigator.clipboard.writeText(value);
                        copied = true;
                    } catch (error) {
                        console.warn('Clipboard API 不可用，尝试兼容复制:', error);
                    }
                }
                if (!copied) {
                    const input = document.createElement('textarea');
                    input.value = value;
                    input.style.position = 'fixed';
                    input.style.opacity = '0';
                    document.body.append(input);
                    input.select();
                    copied = document.execCommand('copy');
                    input.remove();
                    if (!copied) throw new Error('当前环境无法访问文本剪贴板');
                }
                showToast(`${label}已复制`);
                return true;
            } catch (error) {
                showToast(error.message || String(error), 'error');
                return false;
            }
        }

        async function loadImageGenerationSetPreviews(set) {
            if (!set?.history || isLatestImageGenerationSet(set) || set.previewsLoaded || set.loadingPreviews) return;
            set.loadingPreviews = true;
            renderImageGenerationSets();
            await Promise.all(set.items.map(async item => {
                const previewPath = item.previewPath || item.result?.previewPath;
                if (!previewPath || item.previewUri || !window.pywebview?.api?.load_generated_image) return;
                try {
                    const loaded = await window.pywebview.api.load_generated_image(previewPath);
                    if (!loaded.ok || !loaded.dataUrl) return;
                    item.previewUri = loaded.dataUrl;
                    if (item.result) item.result.previewUri = loaded.dataUrl;
                } catch (error) {
                    console.warn('历史缩略图加载失败:', error);
                }
            }));
            set.previewsLoaded = true;
            set.loadingPreviews = false;
            renderImageGenerationSets();
        }

        function isLatestImageGenerationSet(set) {
            return window.imageEditState.resultSets[0]?.setId === set?.setId;
        }

        async function loadImageGenerationSetOriginals(set) {
            if (!set || !isLatestImageGenerationSet(set) || set.originalsLoaded || set.loadingOriginals) return;
            const completedItems = set.items.filter(item => item.status === 'completed' && !item.fullUri && !item.originalLoadAttempted);
            if (!completedItems.length) {
                const completed = set.items.filter(item => item.status === 'completed');
                set.originalsLoaded = set.status !== 'running' && completed.length > 0 && completed.every(item => Boolean(item.fullUri));
                return;
            }
            const loadImage = window.pywebview?.api?.load_generated_image;
            if (!loadImage) return;
            completedItems.forEach(item => { item.originalLoadAttempted = true; });
            set.loadingOriginals = true;
            renderImageGenerationSets();
            const loadedItems = await Promise.all(completedItems.map(async item => {
                const sourcePath = item.path || item.result?.path;
                if (!sourcePath) return false;
                try {
                    const loaded = await loadImage(sourcePath);
                    if (!loaded.ok || !loaded.dataUrl) return false;
                    const currentItem = set.items.find(candidate => Number(candidate.itemIndex) === Number(item.itemIndex)) || item;
                    currentItem.fullUri = loaded.dataUrl;
                    currentItem.originalLoadAttempted = true;
                    if (currentItem.result) currentItem.result.fullUri = loaded.dataUrl;
                    currentItem.revealFrames = [];
                    return true;
                } catch (error) {
                    console.warn('最近一轮原图加载失败，继续使用缩略图:', error);
                    return false;
                }
            }));
            const completed = set.items.filter(item => item.status === 'completed');
            set.originalsLoaded = set.status !== 'running' && completed.length > 0 && completed.every(item => Boolean(item.fullUri));
            set.loadingOriginals = false;
            renderImageGenerationSets();
        }

        function selectedImageGenerationSets() {
            const selectedIds = window.imageEditState.selectedSetIds;
            return window.imageEditState.resultSets.filter(set => selectedIds.has(set.setId));
        }

        function updateImageSetSelectionActions() {
            const selected = selectedImageGenerationSets();
            const exportButton = document.getElementById('exportSelectedImageSetsButton');
            const deleteButton = document.getElementById('deleteSelectedImageSetsButton');
            if (exportButton) exportButton.disabled = !selected.some(set => set.items.some(item => item.status === 'completed'));
            if (deleteButton) deleteButton.disabled = !selected.some(set => set.status !== 'running');
        }

        function toggleImageSetSelection(setId, checked) {
            if (checked) window.imageEditState.selectedSetIds.add(setId);
            else window.imageEditState.selectedSetIds.delete(setId);
            updateImageSetSelectionActions();
        }

        function deleteImageGenerationSet(setId) {
            const set = imageGenerationSetById(setId);
            if (!set || set.status === 'running') return;
            window.imageEditState.pendingDeleteSetId = setId;
            window.imageEditState.pendingDeleteSetIds = [setId];
            const modal = document.getElementById('imageSetDeleteModal');
            const appMain = document.getElementById('appMain');
            document.getElementById('imageSetDeleteModalTitle').textContent = '删除图片集';
            document.getElementById('imageSetDeleteModalMessage').textContent = '将同时删除该轮提示词、原图和压缩预览。此操作无法撤销。';
            if (modal.parentElement !== appMain) appMain.append(modal);
            openAnimatedModal(modal);
            renderLucideIcons();
            requestAnimationFrame(() => document.getElementById('cancelImageSetDeleteButton').focus());
        }

        function deleteSelectedImageSets() {
            const selected = selectedImageGenerationSets().filter(set => set.status !== 'running');
            if (!selected.length) return showToast('请先选择已完成的图片集', 'error');
            window.imageEditState.pendingDeleteSetId = selected[0].setId;
            window.imageEditState.pendingDeleteSetIds = selected.map(set => set.setId);
            const modal = document.getElementById('imageSetDeleteModal');
            const appMain = document.getElementById('appMain');
            document.getElementById('imageSetDeleteModalTitle').textContent = `删除 ${selected.length} 个图片集`;
            document.getElementById('imageSetDeleteModalMessage').textContent = `将同时删除选中的 ${selected.length} 个图片集、原图和压缩预览。此操作无法撤销。`;
            if (modal.parentElement !== appMain) appMain.append(modal);
            openAnimatedModal(modal);
            renderLucideIcons();
            requestAnimationFrame(() => document.getElementById('cancelImageSetDeleteButton').focus());
        }

        async function exportSelectedImageSets() {
            const selected = selectedImageGenerationSets()
                .filter(set => set.items.some(item => item.status === 'completed'))
                .map(set => ({ setId: set.setId, sessionId: set.sessionId, prompt: set.originalPrompt || set.prompt || '图片生成' }));
            if (!selected.length) return showToast('请先选择包含完成图片的图片集', 'error');
            const button = document.getElementById('exportSelectedImageSetsButton');
            button.disabled = true;
            try {
                const result = await window.pywebview.api.export_image_sets(selected);
                if (!result.ok) throw new Error(result.error || '导出图片失败');
                if (!result.cancelled) {
                    const skippedCount = Array.isArray(result.skippedSets) ? result.skippedSets.length : 0;
                    showToast(skippedCount
                        ? `已导出 ${result.exported || 0} 张原图，${skippedCount} 个图片集无可用原图`
                        : result.exported ? `已导出 ${result.exported} 张原图` : '没有可导出的原图',
                        skippedCount ? 'error' : 'success');
                }
            } catch (error) {
                showToast(error.message || String(error), 'error');
            } finally {
                updateImageSetSelectionActions();
            }
        }

        function browserImageSource(...candidates) {
            return candidates
                .map(candidate => String(candidate || '').trim())
                .find(candidate => candidate && !candidate.toLowerCase().startsWith('file:')) || '';
        }

        async function toggleWebReferences(setId) {
            const set = imageGenerationSetById(setId);
            if (!set?.webReferences?.length) return;
            set.webReferencesExpanded = !set.webReferencesExpanded;
            if (set.webReferencesExpanded) set.finalPromptExpanded = false;
            if (!set.webReferencesExpanded) {
                renderImageGenerationSets();
                return;
            }
            const missing = set.webReferences.filter(reference => !reference.previewUri && reference.previewPath);
            if (!missing.length || !window.pywebview?.api?.load_generated_image) {
                renderImageGenerationSets();
                return;
            }
            set.webReferencesLoading = true;
            renderImageGenerationSets();
            await Promise.all(missing.map(async reference => {
                try {
                    const loaded = await window.pywebview.api.load_generated_image(reference.previewPath);
                    if (loaded.ok && loaded.dataUrl) reference.previewUri = loaded.dataUrl;
                } catch (error) {
                    console.warn('网络参考图预览加载失败:', error);
                }
            }));
            set.webReferencesLoading = false;
            renderImageGenerationSets();
        }

        function toggleImageGenerationSet(setId) {
            const set = imageGenerationSetById(setId);
            if (!set || set.status === 'running') return;
            set.expanded = !set.expanded;
            if (set.expanded && set.history && !set.previewsLoaded) {
                void loadImageGenerationSetPreviews(set);
                return;
            }
            if (!set.expanded && set.history) {
                set.previewsLoaded = false;
                set.items.forEach(item => {
                    item.previewUri = '';
                    if (item.result) item.result.previewUri = '';
                });
            }
            renderImageGenerationSets();
        }
