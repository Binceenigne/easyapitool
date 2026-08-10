        const IMAGE_STREAM_INITIAL_BLUR = 32;
        const IMAGE_STREAM_CLARITY_STEP = 0.25;
        const IMAGE_STREAM_PARTIAL_DURATION = 10000;
        const IMAGE_STREAM_FINAL_DURATION = 3000;
        const IMAGE_STREAM_INITIAL_FADE_DURATION = 5000;
        const IMAGE_STREAM_CROSSFADE_DURATION = 10000;
        const IMAGE_STREAM_DEBUG_PREFIX = '[ImageStreamBlur]';
        const IMAGE_STREAM_DEBUG_LIMIT = 20000;
        const imageStreamDebugHistory = [];
        const imageStreamDebugPending = [];
        const imageStreamDebugSamplers = new WeakMap();
        const imageRevealItemContext = new WeakMap();
        let imageStreamDebugSequence = 0;
        let imageStreamDebugFrameSequence = 0;
        let imageStreamDebugFlushTimer = 0;
        let imageStreamDebugFlushing = false;

        function imageStreamFrameId(frame) {
            if (!frame.debugId) frame.debugId = `frame-${++imageStreamDebugFrameSequence}`;
            return frame.debugId;
        }

        function imageStreamActualStyle(image) {
            if (!image?.isConnected) {
                return { connected: false, filter: '', actualBlurPx: null, opacity: null };
            }
            const style = getComputedStyle(image);
            const filter = style.filter || 'none';
            const blurMatch = filter.match(/blur\((-?[\d.]+)px\)/i);
            const animations = image.getAnimations?.().map(animation => ({
                currentTimeMs: Number.isFinite(Number(animation.currentTime)) ? Number(animation.currentTime) : null,
                playState: animation.playState,
                progress: animation.effect?.getComputedTiming?.().progress ?? null
            })) || [];
            return {
                connected: true,
                filter,
                actualBlurPx: blurMatch ? Number(blurMatch[1]) : filter === 'none' ? 0 : null,
                opacity: Number(style.opacity),
                animationName: style.animationName,
                animationDuration: style.animationDuration,
                animationDelay: style.animationDelay,
                animations
            };
        }

        function imageStreamDebugContext(item, frame = null, frameIndex = null) {
            const itemContext = imageRevealItemContext.get(item) || {};
            return {
                setId: itemContext.setId || '',
                itemIndex: Number.isInteger(itemContext.itemIndex) ? itemContext.itemIndex : Number(item?.itemIndex) || 0,
                frameId: frame ? imageStreamFrameId(frame) : '',
                frameIndex,
                kind: frame?.kind || '',
                configuredFromBlurPx: Number.isFinite(Number(frame?.fromBlur)) ? Number(frame.fromBlur) : null,
                configuredToBlurPx: Number.isFinite(Number(frame?.toBlur)) ? Number(frame.toBlur) : null,
                predictedBlurPx: frame ? imageRevealBlurAt(frame) : null
            };
        }

        function scheduleImageStreamDebugFlush() {
            if (imageStreamDebugFlushTimer || imageStreamDebugPending.length === 0) return;
            imageStreamDebugFlushTimer = window.setTimeout(() => {
                imageStreamDebugFlushTimer = 0;
                void flushImageStreamDebugLog();
            }, 1000);
        }

        async function flushImageStreamDebugLog() {
            if (imageStreamDebugFlushing || imageStreamDebugPending.length === 0) return;
            const api = window.pywebview?.api?.append_image_stream_debug;
            if (!api) {
                imageStreamDebugPending.length = 0;
                return;
            }
            imageStreamDebugFlushing = true;
            const records = imageStreamDebugPending.splice(0, 240);
            try {
                await api(records);
            } catch (error) {
                console.warn(IMAGE_STREAM_DEBUG_PREFIX, 'persist failed', error);
            } finally {
                imageStreamDebugFlushing = false;
                if (imageStreamDebugPending.length) scheduleImageStreamDebugFlush();
            }
        }

        function imageStreamDebugLog(event, details = {}, image = null) {
            const entry = {
                sequence: ++imageStreamDebugSequence,
                timestamp: new Date().toISOString(),
                performanceMs: Math.round(performance.now() * 1000) / 1000,
                event,
                ...details,
                ...(image ? imageStreamActualStyle(image) : {})
            };
            imageStreamDebugHistory.push(entry);
            if (imageStreamDebugHistory.length > IMAGE_STREAM_DEBUG_LIMIT) imageStreamDebugHistory.shift();
            imageStreamDebugPending.push(entry);
            if (event !== 'sample') console.info(IMAGE_STREAM_DEBUG_PREFIX, entry);
            scheduleImageStreamDebugFlush();
            return entry;
        }

        function sampleImageStreamFrames(item) {
            const frames = Array.isArray(item.revealFrames) ? item.revealFrames : [];
            const layers = frames.map((frame, frameIndex) => {
                const image = imageRevealElementCache.get(frame);
                return {
                    ...imageStreamDebugContext(item, frame, frameIndex),
                    ...imageStreamActualStyle(image)
                };
            });
            const visibleLayers = layers.filter(layer => layer.connected && Number(layer.opacity) > 0.01);
            const topVisibleLayer = visibleLayers.at(-1) || null;
            imageStreamDebugLog('sample', {
                ...imageStreamDebugContext(item),
                layerCount: layers.length,
                topVisibleFrameId: topVisibleLayer?.frameId || '',
                topVisibleActualBlurPx: topVisibleLayer?.actualBlurPx ?? null,
                layers
            });
        }

        function ensureImageStreamDebugSampler(item) {
            let state = imageStreamDebugSamplers.get(item);
            if (!state) {
                state = { frameRequest: 0, activeUntil: 0 };
                imageStreamDebugSamplers.set(item, state);
            }
            state.activeUntil = Math.max(state.activeUntil, Date.now() + 12000);
            if (state.frameRequest) return;
            const tick = () => {
                state.frameRequest = 0;
                sampleImageStreamFrames(item);
                const frames = Array.isArray(item.revealFrames) ? item.revealFrames : [];
                const latestEnd = frames.reduce((endAt, frame) => {
                    if (!frame.startedAt) return Math.max(endAt, Date.now() + 1000);
                    return Math.max(endAt, frame.startedAt + Math.max(
                        Number(frame.duration) || 0,
                        frame.kind === 'final' ? IMAGE_STREAM_CROSSFADE_DURATION : 0
                    ));
                }, 0);
                if (Date.now() <= Math.max(state.activeUntil, latestEnd) + 250) {
                    state.frameRequest = requestAnimationFrame(tick);
                } else {
                    imageStreamDebugLog('sampling-stopped', imageStreamDebugContext(item));
                    imageStreamDebugSamplers.delete(item);
                }
            };
            state.frameRequest = requestAnimationFrame(tick);
        }

        window.getImageStreamDebugLog = setId => imageStreamDebugHistory
            .filter(entry => !setId || entry.setId === setId)
            .map(entry => structuredClone(entry));
        window.clearImageStreamDebugLog = () => {
            imageStreamDebugHistory.length = 0;
            imageStreamDebugPending.length = 0;
            imageStreamDebugSequence = 0;
            console.info(IMAGE_STREAM_DEBUG_PREFIX, 'debug history cleared');
        };
        window.flushImageStreamDebugLog = flushImageStreamDebugLog;

        function imageRevealTargetBlur(partialIndex) {
            const index = Math.max(1, Math.floor(Number(partialIndex) || 1));
            const remaining = index <= 3
                ? 1 - index * IMAGE_STREAM_CLARITY_STEP
                : IMAGE_STREAM_CLARITY_STEP / (2 ** (index - 3));
            return IMAGE_STREAM_INITIAL_BLUR * Math.max(0, remaining);
        }

        function imageRevealBlurAt(frame, timestamp = Date.now()) {
            if (Number.isFinite(Number(frame?.frozenBlur))) return Number(frame.frozenBlur);
            if (!frame?.startedAt) return null;
            const duration = Math.max(1, Number(frame.duration) || 1);
            const progress = Math.max(0, Math.min(1, (timestamp - frame.startedAt) / duration));
            const fromBlur = Number(frame.fromBlur) || 0;
            const toBlur = Number(frame.toBlur) || 0;
            return fromBlur + (toBlur - fromBlur) * progress;
        }

        function currentImageRevealBlur(item, beforeFrameIndex, timestamp = Date.now()) {
            const frames = Array.isArray(item.revealFrames) ? item.revealFrames : [];
            for (let index = Math.min(beforeFrameIndex, frames.length) - 1; index >= 0; index -= 1) {
                const actual = imageStreamActualStyle(imageRevealElementCache.get(frames[index]));
                if (actual.connected && Number(actual.opacity) > 0.01 && Number.isFinite(actual.actualBlurPx)) {
                    return actual.actualBlurPx;
                }
                const blur = imageRevealBlurAt(frames[index], timestamp);
                if (blur !== null) return blur;
            }
            return IMAGE_STREAM_INITIAL_BLUR;
        }

        function freezeImageRevealFrame(frame, image, blur, opacity = frame.frozenOpacity) {
            frame.frozenBlur = Math.max(0, Number(blur) || 0);
            frame.frozenOpacity = Math.max(0, Math.min(1, Number.isFinite(Number(opacity)) ? Number(opacity) : 1));
            if (!image) return;
            image.classList.remove('is-reveal-pending');
            image.classList.add('is-image-reveal', `is-${frame.kind}-reveal`);
            image.style.animation = 'none';
            image.style.filter = `blur(${frame.frozenBlur}px)`;
            image.style.opacity = String(frame.frozenOpacity);
            image.style.willChange = 'auto';
            const context = imageRevealElementContext.get(image);
            imageStreamDebugLog('frame-frozen', {
                ...imageStreamDebugContext(context?.item, frame, context?.frameIndex),
                requestedBlurPx: frame.frozenBlur,
                requestedOpacity: frame.frozenOpacity
            }, image);
        }

        function appendImageRevealFrame(item, uri, kind, options = {}) {
            if (!uri) return null;
            if (!Array.isArray(item.revealFrames)) item.revealFrames = [];
            const previousFrame = item.revealFrames.at(-1);
            if (previousFrame?.uri === uri && previousFrame.kind === kind) return previousFrame;
            if (previousFrame?.kind === 'final') return previousFrame;

            const previousTarget = Number.isFinite(Number(previousFrame?.toBlur))
                ? Number(previousFrame.toBlur)
                : IMAGE_STREAM_INITIAL_BLUR;
            const requestedTarget = kind === 'final'
                ? 0
                : imageRevealTargetBlur(options.partialIndex);
            const previousImage = previousFrame ? imageRevealElementCache.get(previousFrame) : null;
            const previousActual = imageStreamActualStyle(previousImage);
            const capturedBlur = previousActual.connected
                && Number(previousActual.opacity) > 0.01
                && Number.isFinite(previousActual.actualBlurPx)
                ? previousActual.actualBlurPx
                : null;
            const frame = {
                uri,
                fallbackUri: options.fallbackUri || '',
                path: options.path || '',
                kind,
                duration: kind === 'final' ? IMAGE_STREAM_FINAL_DURATION : IMAGE_STREAM_PARTIAL_DURATION,
                startedAt: 0,
                fromBlur: capturedBlur,
                toBlur: Math.min(previousTarget, requestedTarget)
            };
            item.revealFrames.push(frame);
            imageStreamDebugLog('frame-inserted', {
                ...imageStreamDebugContext(item, frame, item.revealFrames.length - 1),
                partialIndex: Number(options.partialIndex) || 0,
                partialTotal: Number(options.partialTotal) || 0,
                previousFrameId: previousFrame ? imageStreamFrameId(previousFrame) : '',
                previousActual
            });
            ensureImageStreamDebugSampler(item);
            return frame;
        }

        function imageRevealDuration(frame) {
            return Number.isFinite(frame.resumeDuration)
                ? Math.max(1, frame.resumeDuration)
                : Math.max(1, Number(frame.duration) || 1);
        }

        function imageRevealCrossfadeDuration(frame, frameIndex) {
            if (Number.isFinite(frame.resumeCrossfadeDuration)) {
                return Math.max(1, frame.resumeCrossfadeDuration);
            }
            if (frame.kind === 'final') return frame.duration;
            return frameIndex === 0 ? IMAGE_STREAM_INITIAL_FADE_DURATION : IMAGE_STREAM_CROSSFADE_DURATION;
        }

        function captureImageRevealFramesForRender() {
            const timestamp = Date.now();
            document.querySelectorAll('.image-generation-frame.is-image-reveal').forEach(image => {
                const context = imageRevealElementContext.get(image);
                const frame = context?.frame;
                if (!frame?.startedAt || Number.isFinite(Number(frame.frozenBlur))) return;
                const actual = imageStreamActualStyle(image);
                if (!actual.connected || !Number.isFinite(actual.actualBlurPx) || !Number.isFinite(actual.opacity)) return;
                const elapsed = Math.max(0, timestamp - Number(frame.startedAt));
                const blurRemaining = Math.max(0, imageRevealDuration(frame) - elapsed);
                const fadeRemaining = Math.max(0, imageRevealCrossfadeDuration(frame, context.frameIndex) - elapsed);
                if (blurRemaining <= 0 && fadeRemaining <= 0) {
                    freezeImageRevealFrame(frame, image, frame.toBlur, 1);
                    return;
                }
                frame.fromBlur = Math.max(Number(frame.toBlur) || 0, actual.actualBlurPx);
                frame.fromOpacity = Math.max(0, Math.min(1, actual.opacity));
                frame.resumeDuration = Math.max(1, blurRemaining);
                frame.resumeCrossfadeDuration = Math.max(1, fadeRemaining);
                frame.startedAt = timestamp;
                imageStreamDebugLog('frame-captured-for-render', {
                    ...imageStreamDebugContext(context.item, frame, context.frameIndex),
                    capturedOpacity: frame.fromOpacity,
                    blurRemainingMs: frame.resumeDuration,
                    crossfadeRemainingMs: frame.resumeCrossfadeDuration
                }, image);
            });
        }

        function applyImageRevealAnimation(image, frame, frameIndex) {
            const elapsed = Math.max(0, Date.now() - Number(frame.startedAt || 0));
            const initialPartial = frameIndex === 0 && frame.kind === 'partial';
            const crossfadeDuration = imageRevealCrossfadeDuration(frame, frameIndex);
            image.classList.remove('is-reveal-pending');
            image.classList.add('is-image-reveal', `is-${frame.kind}-reveal`);
            image.style.removeProperty('animation');
            image.style.removeProperty('filter');
            image.style.removeProperty('opacity');
            image.style.removeProperty('will-change');
            const revealDuration = imageRevealDuration(frame);
            image.style.setProperty('--image-reveal-duration', `${revealDuration}ms`);
            image.style.setProperty('--image-reveal-delay', `${-Math.min(elapsed, revealDuration)}ms`);
            image.style.setProperty('--image-reveal-from-blur', `${frame.fromBlur}px`);
            image.style.setProperty('--image-reveal-to-blur', `${frame.toBlur}px`);
            image.style.setProperty(
                '--image-reveal-from-opacity',
                Number.isFinite(frame.fromOpacity)
                    ? String(frame.fromOpacity)
                    : initialPartial || frameIndex > 0 ? '0' : '1'
            );
            image.style.setProperty('--image-reveal-crossfade-duration', `${crossfadeDuration}ms`);
            image.style.setProperty('--image-reveal-crossfade-delay', `${-Math.min(elapsed, crossfadeDuration)}ms`);
            const context = imageRevealElementContext.get(image);
            imageStreamDebugLog('animation-applied', {
                ...imageStreamDebugContext(context?.item, frame, frameIndex),
                elapsedMs: elapsed,
                crossfadeDurationMs: crossfadeDuration
            }, image);
        }

        function scheduleFinalImageFrameCleanup(item, frame) {
            if (frame.kind !== 'final' || frame.cleanupScheduled) return;
            frame.cleanupScheduled = true;
            const elapsed = Math.max(0, Date.now() - Number(frame.startedAt || 0));
            const cleanupDuration = frame.duration;
            window.setTimeout(() => {
                if (!item.revealFrames?.includes(frame) || item.revealFrames.at(-1) !== frame) return;
                freezeImageRevealFrame(frame, imageRevealElementCache.get(frame), 0, 1);
                item.revealFrames = [frame];
                renderImageGenerationSets();
            }, Math.max(0, cleanupDuration - elapsed));
        }

        function startImageRevealFrame(item, frame, image, frameIndex) {
            imageStreamDebugLog('frame-start-requested', {
                ...imageStreamDebugContext(item, frame, frameIndex),
                previousLayers: item.revealFrames.slice(0, frameIndex).map((previousFrame, previousIndex) => ({
                    ...imageStreamDebugContext(item, previousFrame, previousIndex),
                    ...imageStreamActualStyle(imageRevealElementCache.get(previousFrame))
                }))
            }, image);
            if (Number.isFinite(Number(frame.frozenBlur))) {
                freezeImageRevealFrame(frame, image, frame.frozenBlur);
                return;
            }
            if (!frame.startedAt) {
                const timestamp = Date.now();
                const visibleBlur = currentImageRevealBlur(item, frameIndex, timestamp);
                const previousImages = image.parentElement?.querySelectorAll('.image-generation-frame') || [];
                item.revealFrames.slice(0, frameIndex).forEach((previousFrame, previousIndex) => {
                    const previousBlur = imageRevealBlurAt(previousFrame, timestamp);
                    freezeImageRevealFrame(
                        previousFrame,
                        previousImages[previousIndex],
                        previousBlur === null ? visibleBlur : Math.min(previousBlur, visibleBlur),
                        Number(getComputedStyle(previousImages[previousIndex]).opacity)
                    );
                });
                const capturedBlur = Number.isFinite(frame.fromBlur) ? frame.fromBlur : visibleBlur;
                frame.fromBlur = Math.max(Number(frame.toBlur) || 0, Math.min(capturedBlur, visibleBlur));
                frame.startedAt = timestamp;
                imageStreamDebugLog('frame-started', {
                    ...imageStreamDebugContext(item, frame, frameIndex),
                    selectedVisibleBlurPx: visibleBlur
                }, image);
            }
            applyImageRevealAnimation(image, frame, frameIndex);
            scheduleFinalImageFrameCleanup(item, frame);
            ensureImageStreamDebugSampler(item);
        }

        const imageRevealElementCache = new WeakMap();
        const imageRevealElementContext = new WeakMap();

        function createImageRevealElement(item, frame, frameIndex) {
            let image = imageRevealElementCache.get(frame);
            if (!image) {
                image = document.createElement('img');
                image.className = 'image-generation-frame is-reveal-pending';
                image.addEventListener('load', () => {
                    const context = imageRevealElementContext.get(image);
                    if (context) {
                        applyImageGenerationItemAspectRatio(image.parentElement, context.item, image);
                        imageStreamDebugLog('image-loaded', imageStreamDebugContext(
                            context.item,
                            context.frame,
                            context.frameIndex
                        ), image);
                        startImageRevealFrame(context.item, context.frame, image, context.frameIndex);
                    }
                });
                let recoveryAttempted = false;
                image.addEventListener('error', async () => {
                    if (recoveryAttempted) return;
                    recoveryAttempted = true;
                    const context = imageRevealElementContext.get(image);
                    const failedFrame = context?.frame;
                    if (!failedFrame) return;
                    let recoveredUri = '';
                    if (failedFrame.kind === 'final' && failedFrame.path && window.pywebview?.api?.load_generated_image) {
                        try {
                            const loaded = await window.pywebview.api.load_generated_image(failedFrame.path);
                            recoveredUri = loaded.ok ? loaded.dataUrl || '' : '';
                        } catch (error) {
                            console.warn('缩略图原图加载失败:', error);
                        }
                    }
                    recoveredUri ||= failedFrame.fallbackUri;
                    const safeRecoveredUri = browserImageSource(recoveredUri);
                    if (safeRecoveredUri && safeRecoveredUri !== failedFrame.uri) image.src = safeRecoveredUri;
                });
                imageRevealElementCache.set(frame, image);
            }
            imageRevealElementContext.set(image, { item, frame, frameIndex });
            imageRevealItemContext.set(item, imageRevealItemContext.get(item) || {
                setId: '',
                itemIndex: Number(item.itemIndex) || 0
            });
            image.alt = frame.kind === 'final' ? '生成图片' : '过程预览';
            const frameSource = frame.kind === 'final'
                ? browserImageSource(item.fullUri || item.result?.fullUri, frame.uri, frame.fallbackUri)
                : browserImageSource(frame.uri, frame.fallbackUri);
            if (frameSource && image.getAttribute('src') !== frameSource) image.src = frameSource;
            if (Number.isFinite(Number(frame.frozenBlur))) {
                freezeImageRevealFrame(frame, image, frame.frozenBlur, frame.frozenOpacity);
            } else if (frame.startedAt) {
                applyImageRevealAnimation(image, frame, frameIndex);
                scheduleFinalImageFrameCleanup(item, frame);
            }
            return image;
        }

        const pendingReasoningTurnRenders = new Set();
        let reasoningTurnRenderFrame = 0;

        function reasoningTurnDisplayText(turn) {
            if (String(turn?.text || '').trim()) return turn.text;
            if (turn?.status === 'running') return '正在形成这一轮的判断…';
            if (turn?.status === 'failed') return '本轮判断未完成';
            return '本轮判断已完成';
        }

        function queueReasoningTurnRender(setId, turnNumber) {
            pendingReasoningTurnRenders.add(`${setId}\n${turnNumber}`);
            if (reasoningTurnRenderFrame) return;
            reasoningTurnRenderFrame = requestAnimationFrame(() => {
                reasoningTurnRenderFrame = 0;
                pendingReasoningTurnRenders.forEach(renderKey => {
                    const [pendingSetId, pendingTurnNumber] = renderKey.split('\n');
                    const pendingSet = imageGenerationSetById(pendingSetId);
                    const turn = pendingSet
                        ? reasoningTurnsForSet(pendingSet).find(item => item.turn === Number(pendingTurnNumber))
                        : null;
                    const text = document.querySelector(
                        `[data-reasoning-turn-text="${CSS.escape(pendingSetId)}:${pendingTurnNumber}"]`
                    );
                    if (!pendingSet || !turn || !text) return;
                    text.textContent = reasoningTurnDisplayText(turn);
                    text.classList.toggle('is-streaming', turn.status === 'running');
                    const log = text.closest('.image-reasoning-log');
                    if (log && !pendingSet.reasoningContentExpanded) {
                        log.scrollTo({ top: log.scrollHeight, behavior: 'smooth' });
                    }
                });
                pendingReasoningTurnRenders.clear();
            });
        }

        function updateReasoningTimers() {
            document.querySelectorAll('[data-reasoning-elapsed]').forEach(element => {
                const set = imageGenerationSetById(element.dataset.reasoningElapsed);
                if (!set) return;
                element.textContent = `${totalReasoningElapsed(set)} · ${formatReasoningUsage(set)}`;
                element.title = reasoningUsageTitle(set);
            });
        }

        function reasoningToolLabel(tool) {
            const query = String(tool.arguments?.query || '').trim();
            if (tool.name === 'search_web') return query ? `搜索关键词“${query}”` : '搜索网页';
            if (tool.name === 'search_visual_references') return query ? `搜索图片“${query}”` : '搜索图片';
            if (tool.name === 'select_visual_references') return '筛选可用参考图';
            if (tool.name === 'plan_image_continuation') return '规划续作与素材';
            return '调用工具';
        }

        function reasoningPanelTitle(set, activeTurn, activeTool) {
            const mode = normalizeImageReasoningMode(set.reasoningMode);
            const modeLabel = IMAGE_REASONING_LABELS[mode];
            const statusLabel = activeTool
                ? reasoningToolLabel(activeTool)
                : set.reasoningStatus === 'completed'
                    ? '方案已确定'
                    : set.reasoningStatus === 'running'
                        ? '正在确认方案'
                        : activeTurn?.title || '正在分析问题';
            return `${modeLabel}：${statusLabel}`;
        }

        function renderReasoningPanel(set) {
            const panel = document.createElement('div');
            panel.className = `image-reasoning-panel is-${set.reasoningStatus} mode-${set.reasoningMode}${set.reasoningExpanded ? ' is-expanded' : ''}`;
            panel.dataset.reasoningSet = set.setId;
            const activeTurn = activeReasoningTurn(set);
            const activeTool = [...(activeTurn?.tools || [])].reverse().find(tool => tool.status === 'running');

            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'image-reasoning-toggle';
            toggle.setAttribute('aria-expanded', String(set.reasoningExpanded));
            toggle.addEventListener('click', () => toggleReasoningPanel(set.setId));
            const statusIcon = set.reasoningStatus === 'running'
                ? 'clock-3'
                : set.reasoningStatus === 'failed' ? 'triangle-alert' : 'circle-check';
            const title = reasoningPanelTitle(set, activeTurn, activeTool);
            toggle.innerHTML = `${iconMarkup(statusIcon, 'icon-12')}<strong>${escapeHtml(title)}</strong><span class="image-reasoning-usage" data-reasoning-elapsed="${escapeHtml(set.setId)}" title="${escapeHtml(reasoningUsageTitle(set))}">${totalReasoningElapsed(set)} · ${formatReasoningUsage(set)}</span>${iconMarkup(set.reasoningExpanded ? 'chevron-up' : 'chevron-right', 'icon-12')}`;
            panel.append(toggle);

            if (!set.reasoningExpanded) return panel;

            const body = document.createElement('div');
            body.className = 'image-reasoning-body';
            const log = document.createElement('div');
            log.className = `image-reasoning-log${set.reasoningContentExpanded ? ' is-fully-expanded' : ''}`;
            const reasoningTurns = reasoningTurnsForSet(set);
            const turns = reasoningTurns.length ? reasoningTurns : [{
                turn: 1,
                title,
                text: set.reasoningSummary,
                status: set.reasoningStatus,
                startedAt: set.reasoningStartedAt,
                completedAt: set.reasoningCompletedAt,
                tools: []
            }];
            turns.forEach(turn => {
                const turnBlock = document.createElement('section');
                turnBlock.className = `image-reasoning-turn is-${turn.status}`;
                const turnTitle = document.createElement('div');
                turnTitle.className = 'image-reasoning-turn-title';
                turnTitle.innerHTML = `<strong>${escapeHtml(turn.title || '正在完善方案')}</strong><span>${reasoningElapsed(set, turn)}</span>`;
                const turnText = document.createElement('p');
                turnText.dataset.reasoningTurnText = `${set.setId}:${turn.turn}`;
                turnText.textContent = reasoningTurnDisplayText(turn);
                if (turn.status === 'running') turnText.classList.add('is-streaming');
                turnBlock.append(turnTitle, turnText);
                (turn.tools || []).forEach(tool => {
                    const toolRow = document.createElement('div');
                    toolRow.className = `image-reasoning-tool is-${tool.status}`;
                    const resultSuffix = tool.status === 'completed' && Number(tool.resultCount) > 0
                        ? ` · ${Number(tool.resultCount)} 项结果`
                        : tool.status === 'failed' ? ' · 已跳过' : '';
                    toolRow.innerHTML = `${iconMarkup(tool.status === 'running' ? 'loader-circle' : tool.status === 'failed' ? 'triangle-alert' : 'search-check', `icon-11${tool.status === 'running' ? ' is-spinning' : ''}`)}<span>${escapeHtml(reasoningToolLabel(tool) + resultSuffix)}</span>`;
                    turnBlock.append(toolRow);
                });
                log.append(turnBlock);
            });
            const logShell = document.createElement('div');
            logShell.className = 'image-reasoning-log-shell';
            logShell.append(log);

            const expandContent = document.createElement('button');
            expandContent.type = 'button';
            expandContent.className = 'image-reasoning-content-toggle';
            expandContent.title = set.reasoningContentExpanded ? '收起思考内容' : '展开全部思考内容';
            expandContent.setAttribute('aria-label', expandContent.title);
            expandContent.innerHTML = iconMarkup(set.reasoningContentExpanded ? 'minimize-2' : 'maximize-2', 'icon-11');
            expandContent.addEventListener('click', () => toggleReasoningContent(set.setId));
            logShell.append(expandContent);
            body.append(logShell);

            if ((set.webReferenceCount > 0 && set.webReferences.length > 0) || set.effectivePrompt) {
                const supplementTabs = document.createElement('div');
                supplementTabs.className = 'image-reasoning-supplement-tabs';
                if (set.webReferenceCount > 0 && set.webReferences.length > 0) {
                const referenceToggle = document.createElement('button');
                referenceToggle.type = 'button';
                    referenceToggle.className = `image-reasoning-supplement-toggle${set.webReferencesExpanded ? ' is-active' : ''}`;
                referenceToggle.title = set.webReferencesExpanded ? '收起网络参考图' : '查看采用的网络参考图';
                referenceToggle.setAttribute('aria-expanded', String(set.webReferencesExpanded));
                    referenceToggle.innerHTML = `${iconMarkup('images', 'icon-10')}<span>网络参考 ${set.webReferenceCount}</span>`;
                referenceToggle.addEventListener('click', () => void toggleWebReferences(set.setId));
                    supplementTabs.append(referenceToggle);
                }
                if (set.effectivePrompt) {
                    const promptToggle = document.createElement('button');
                    promptToggle.type = 'button';
                    promptToggle.className = `image-reasoning-supplement-toggle${set.finalPromptExpanded ? ' is-active' : ''}`;
                    promptToggle.title = set.finalPromptExpanded ? '收起最终提示词' : '查看最终提示词';
                    promptToggle.setAttribute('aria-expanded', String(set.finalPromptExpanded));
                    promptToggle.innerHTML = `${iconMarkup('file-text', 'icon-10')}<span>最终提示词</span>`;
                    promptToggle.addEventListener('click', () => toggleFinalPrompt(set.setId));
                    supplementTabs.append(promptToggle);
                }
                body.append(supplementTabs);
            }

            if (set.webReferencesExpanded && set.webReferences.length) {
                const references = document.createElement('div');
                references.className = 'image-reasoning-web-references';
                if (set.webReferencesLoading) {
                    references.innerHTML = `${iconMarkup('loader-circle', 'icon-13 is-spinning')}<span>正在加载网络参考图</span>`;
                } else {
                    set.webReferences.forEach((reference, referenceIndex) => {
                        const referenceButton = document.createElement('button');
                        referenceButton.type = 'button';
                        referenceButton.className = 'image-reasoning-web-reference';
                        referenceButton.title = reference.title || `网络参考图 ${referenceIndex + 1}`;
                        const image = document.createElement('img');
                        image.src = browserImageSource(reference.previewUri, reference.uri);
                        image.alt = reference.title || `网络参考图 ${referenceIndex + 1}`;
                        const label = document.createElement('span');
                        label.textContent = reference.title || reference.provider || `参考图 ${referenceIndex + 1}`;
                        referenceButton.append(image, label);
                        referenceButton.addEventListener('click', () => openImageResultModal({
                            ...reference,
                            uri: reference.previewUri || reference.uri || '',
                            actualSize: reference.provider || '网络参考',
                            format: 'jpg',
                            status: 'reference'
                        }));
                        references.append(referenceButton);
                    });
                }
                body.append(references);
            }

            if (set.finalPromptExpanded && set.effectivePrompt) {
                const finalPromptContent = document.createElement('div');
                finalPromptContent.className = 'image-reasoning-final-prompt';
                const finalPromptText = document.createElement('p');
                finalPromptText.textContent = set.effectivePrompt;
                const copyEffectiveButton = document.createElement('button');
                copyEffectiveButton.type = 'button';
                copyEffectiveButton.className = 'image-prompt-copy';
                copyEffectiveButton.title = '复制思维优化提示词';
                copyEffectiveButton.setAttribute('aria-label', copyEffectiveButton.title);
                copyEffectiveButton.innerHTML = iconMarkup('copy', 'icon-11');
                copyEffectiveButton.addEventListener('click', () => void copyImagePrompt(set.effectivePrompt, '思维优化提示词'));
                finalPromptContent.append(finalPromptText, copyEffectiveButton);
                body.append(finalPromptContent);
            }
            panel.append(body);
            return panel;
        }

        function imageGenerationItemAspectRatio(item) {
            for (const source of [item?.result, item]) {
                if (!source) continue;
                let width = Number(source.width) || 0;
                let height = Number(source.height) || 0;
                if ((!width || !height) && source.actualSize) {
                    const match = String(source.actualSize).match(/(\d+)\s*[x×]\s*(\d+)/i);
                    width = Number(match?.[1]) || 0;
                    height = Number(match?.[2]) || 0;
                }
                if (width > 0 && height > 0) return `${width} / ${height}`;
            }
            return '';
        }

        function applyImageGenerationItemAspectRatio(card, item, image = null) {
            if (!card) return;
            if (image?.naturalWidth > 0 && image?.naturalHeight > 0) {
                item.width = image.naturalWidth;
                item.height = image.naturalHeight;
            }
            const aspectRatio = imageGenerationItemAspectRatio(item);
            if (!aspectRatio) return;
            card.style.aspectRatio = aspectRatio;
            card.classList.add('has-image-aspect');
        }

        function renderImageGenerationSets() {
            const container = document.getElementById('imageGenerationSets');
            const sets = window.imageEditState.resultSets;
            const availableSetIds = new Set(sets.map(set => set.setId));
            window.imageEditState.selectedSetIds = new Set(
                [...window.imageEditState.selectedSetIds].filter(setId => availableSetIds.has(setId))
            );
            container.classList.toggle('is-empty', sets.length === 0);
            updateImageSetSelectionActions();
            captureImageRevealFramesForRender();
            container.replaceChildren();
            if (!sets.length) {
                const empty = document.createElement('div');
                empty.id = 'imageEditResultEmpty';
                empty.innerHTML = `${iconMarkup('scan-line', 'icon-20')}<span>每次生成都会在这里创建一个图片集</span>`;
                container.append(empty);
                renderLucideIcons();
                return;
            }
            sets.forEach(set => {
                const expanded = set.status === 'running' || set.expanded === true;
                const section = document.createElement('article');
                section.className = `image-generation-set is-${set.status}${expanded ? ' is-expanded' : ' is-collapsed'}`;
                section.dataset.setId = set.setId;

                const header = document.createElement('div');
                header.className = 'image-generation-set-header';
                const heading = document.createElement('div');
                const titleRow = document.createElement('div');
                titleRow.className = 'image-generation-set-title-row';
                const title = document.createElement('strong');
                title.textContent = set.originalPrompt || set.prompt || '图片生成';
                const copyOriginalButton = document.createElement('button');
                copyOriginalButton.type = 'button';
                copyOriginalButton.className = 'image-prompt-copy';
                copyOriginalButton.title = '复制原始提示词';
                copyOriginalButton.setAttribute('aria-label', copyOriginalButton.title);
                copyOriginalButton.innerHTML = iconMarkup('copy', 'icon-11');
                copyOriginalButton.addEventListener('click', () => void copyImagePrompt(set.originalPrompt || set.prompt, '原始提示词'));
                titleRow.append(title, copyOriginalButton);
                const completedCount = set.items.filter(item => item.status === 'completed').length;
                const meta = document.createElement('small');
                meta.textContent = set.status === 'running'
                    ? `第 ${set.roundNumber || 1} 轮 · 生成中 · ${completedCount}/${set.requestedCount}`
                    : set.status === 'interrupted'
                        ? `第 ${set.roundNumber || 1} 轮 · 已中断 · 保留 ${completedCount} 张`
                        : `第 ${set.roundNumber || 1} 轮 · ${completedCount}/${set.requestedCount} 张 · 已保存`;
                heading.append(titleRow, meta);
                const actions = document.createElement('div');
                actions.className = 'image-generation-set-actions';
                const selectCheckbox = document.createElement('input');
                selectCheckbox.type = 'checkbox';
                selectCheckbox.className = 'image-generation-set-checkbox';
                selectCheckbox.checked = window.imageEditState.selectedSetIds.has(set.setId);
                selectCheckbox.disabled = set.status === 'running';
                selectCheckbox.title = selectCheckbox.disabled ? '生成完成后可选择' : '选择图片集';
                selectCheckbox.setAttribute('aria-label', `选择图片集：${title.textContent}`);
                selectCheckbox.addEventListener('click', event => event.stopPropagation());
                selectCheckbox.addEventListener('change', () => toggleImageSetSelection(set.setId, selectCheckbox.checked));
                const continueButton = document.createElement('button');
                continueButton.type = 'button';
                continueButton.title = '基于此轮继续创作';
                continueButton.setAttribute('aria-label', continueButton.title);
                continueButton.disabled = completedCount === 0;
                continueButton.innerHTML = `${iconMarkup('corner-down-left', 'icon-12')}<span>继续编辑</span>`;
                continueButton.addEventListener('click', () => enterImageEditSession(set.setId));
                const deleteButton = document.createElement('button');
                deleteButton.type = 'button';
                deleteButton.className = 'is-danger';
                deleteButton.title = set.status === 'running' ? '生成完成后可删除' : '删除图片集';
                deleteButton.setAttribute('aria-label', deleteButton.title);
                deleteButton.disabled = set.status === 'running';
                deleteButton.innerHTML = iconMarkup('trash-2', 'icon-12');
                deleteButton.addEventListener('click', () => deleteImageGenerationSet(set.setId));
                actions.append(selectCheckbox, continueButton, deleteButton);
                if (set.status !== 'running') {
                    const toggleButton = document.createElement('button');
                    toggleButton.type = 'button';
                    toggleButton.className = 'is-icon-only image-generation-set-toggle';
                    toggleButton.title = expanded ? '折叠图片集' : '展开图片集';
                    toggleButton.setAttribute('aria-label', toggleButton.title);
                    toggleButton.setAttribute('aria-expanded', String(expanded));
                    toggleButton.innerHTML = iconMarkup(expanded ? 'chevron-up' : 'chevron-down', 'icon-12');
                    toggleButton.addEventListener('click', () => toggleImageGenerationSet(set.setId));
                    actions.append(toggleButton);
                }
                header.append(heading, actions);

                section.append(header);
                if (!expanded) {
                    container.append(section);
                    return;
                }

                const reasoningPanel = set.reasoningStatus !== 'idle' || set.reasoningSummary
                    ? renderReasoningPanel(set)
                    : null;

                const grid = document.createElement('div');
                grid.className = `image-generation-set-grid${set.history ? ' is-history' : ''}`;
                grid.dataset.count = String(set.requestedCount);
                grid.hidden = set.reasoningStatus === 'running';
                if (set.loadingPreviews) {
                    const loading = document.createElement('div');
                    loading.className = 'image-generation-set-loading';
                    loading.innerHTML = `${iconMarkup('loader-circle', 'icon-16 is-spinning')}<span>正在加载缩略图</span>`;
                    grid.append(loading);
                }
                if (!set.loadingPreviews) set.items.forEach(item => {
                    imageRevealItemContext.set(item, {
                        setId: set.setId,
                        itemIndex: Number(item.itemIndex) || 0
                    });
                    const completed = item.status === 'completed';
                    const card = document.createElement(completed ? 'button' : 'div');
                    if (completed) card.type = 'button';
                    const reasoningMode = normalizeImageReasoningMode(set.reasoningMode);
                    card.className = `image-generation-item is-${item.status} mode-${reasoningMode}${set.history ? ' is-history' : ''}`;
                    card.dataset.itemIndex = String(item.itemIndex);
                    card.dataset.reasoningMode = reasoningMode;
                    applyImageGenerationItemAspectRatio(card, item);
                    const displayUri = completed
                        ? browserImageSource(
                            set.history ? item.result?.previewUri : item.fullUri || item.result?.fullUri,
                            set.history ? item.previewUri : item.uri,
                            item.result?.previewUri,
                            item.previewUri,
                            item.result?.uri,
                            item.uri
                        )
                        : browserImageSource(item.previewUri, item.uri);
                    const revealFrames = Array.isArray(item.revealFrames) ? item.revealFrames : [];
                    if (revealFrames.length) {
                        revealFrames.forEach((frame, frameIndex) => {
                            const image = createImageRevealElement(item, frame, frameIndex);
                            card.append(image);
                            applyImageGenerationItemAspectRatio(card, item, image);
                        });
                    } else if (displayUri) {
                        const image = document.createElement('img');
                        image.alt = item.status === 'completed' ? '生成图片' : '过程预览';
                        image.addEventListener('load', () => applyImageGenerationItemAspectRatio(card, item, image));
                        image.src = displayUri;
                        const fallbackUri = completed
                            ? set.history
                                ? item.result?.previewUri || item.previewUri
                                : item.result?.previewUri || item.previewUri || item.result?.uri || item.uri
                            : item.uri;
                        if (fallbackUri && fallbackUri !== displayUri) {
                            image.addEventListener('error', async () => {
                                if (completed && item.result?.path && window.pywebview?.api?.load_generated_image) {
                                    try {
                                        const loaded = await window.pywebview.api.load_generated_image(item.result.path);
                                        if (loaded.ok && loaded.dataUrl) {
                                            image.src = loaded.dataUrl;
                                            return;
                                        }
                                    } catch (error) {
                                        console.warn('缩略图原图加载失败:', error);
                                    }
                                }
                                const safeFallbackUri = browserImageSource(fallbackUri);
                                if (safeFallbackUri && image.src !== safeFallbackUri) image.src = safeFallbackUri;
                            }, { once: true });
                        }
                        card.append(image);
                        applyImageGenerationItemAspectRatio(card, item, image);
                    }
                    const overlay = document.createElement('span');
                    if (item.status === 'completed') {
                        overlay.textContent = '查看图片';
                        card.addEventListener('click', () => openImageResultModal(item.result, set.setId, item.itemIndex));
                        card.addEventListener('contextmenu', event => {
                            event.preventDefault();
                            void copyGeneratedImage(item.result);
                        });
                    } else if (item.status === 'partial') {
                        overlay.innerHTML = `${iconMarkup('loader-circle', 'icon-16 is-spinning')}<em>生成中</em>`;
                    } else if (item.status === 'failed') {
                        overlay.textContent = item.error || '生成失败';
                    } else {
                        overlay.innerHTML = `${iconMarkup('loader-circle', 'icon-16 is-spinning')}<em>等待生成</em>`;
                    }
                    card.append(overlay);
                    grid.append(card);
                });
                if (reasoningPanel) section.append(reasoningPanel);
                section.append(grid);
                container.append(section);
                const reasoningLog = section.querySelector('.image-reasoning-log:not(.is-fully-expanded)');
                if (reasoningLog) reasoningLog.scrollTop = reasoningLog.scrollHeight;
            });
            const latestSet = sets[0];
            if (latestSet && !latestSet.originalsLoaded && !latestSet.loadingOriginals) {
                void loadImageGenerationSetOriginals(latestSet);
            }
            renderLucideIcons();
        }
