        window.applyImageGenerationEvent = function(event) {
            if (event?.type?.startsWith('prompt_polish_')) {
                if (event.type === 'prompt_polish_delta') {
                    const prompt = document.getElementById('imageEditPrompt');
                    prompt.value += event.delta || '';
                    document.getElementById('imagePromptModalTextarea').value = prompt.value;
                    resizeImagePrompt();
                }
                if (event.type === 'prompt_polish_completed' && event.prompt) {
                    document.getElementById('imageEditPrompt').value = event.prompt;
                    document.getElementById('imagePromptModalTextarea').value = event.prompt;
                    resizeImagePrompt();
                }
                return;
            }
            if (!event?.setId) return;
            const eventRequestId = String(event.requestId || '');
            const set = imageGenerationSetById(event.setId)
                || imageGenerationSetById(eventRequestId)
                || createImageGenerationSet(
                    event.setId,
                    Number(event.requestedCount) || 1,
                    event.prompt || '',
                    event
                );
            if (eventRequestId) set.requestId = eventRequestId;
            if (event.type === 'set_started') {
                set.requestedCount = Number(event.requestedCount) || set.requestedCount;
                set.prompt = event.prompt || set.prompt;
                set.originalPrompt = event.originalPrompt || set.originalPrompt || set.prompt;
                set.sessionId = event.sessionId || set.sessionId;
                set.parentSetId = event.parentSetId || set.parentSetId;
                set.roundNumber = Number(event.roundNumber) || set.roundNumber;
                set.operation = event.operation || set.operation;
                set.continuation = event.continuation === true || set.continuation;
                set.continuationRationale = event.continuationRationale || set.continuationRationale;
                if (Array.isArray(event.selectedAssetIds)) set.selectedAssetIds = [...event.selectedAssetIds];
                set.reasoningMode = normalizeImageReasoningMode(event.reasoningMode || set.reasoningMode);
                set.reasoningModel = event.reasoningModel || set.reasoningModel;
                set.reasoningEffort = event.reasoningEffort || set.reasoningEffort;
                set.reasoningDepth = event.reasoningDepth || set.reasoningDepth || set.reasoningEffort;
                set.reasoningSummary = event.reasoningSummary || set.reasoningSummary;
                set.reasoningDurationMs = Math.max(0, Number(event.reasoningDurationMs) || set.reasoningDurationMs);
                set.reasoningUsage = normalizeReasoningUsage(event.reasoningUsage || set.reasoningUsage);
                set.effectivePrompt = event.prompt || set.effectivePrompt;
                set.webSearchEnabled = event.webSearchEnabled === true || set.webSearchEnabled;
                if (event.webSearchFailed) set.webSearchStatus = 'failed';
                else if (event.webSearchUsed) set.webSearchStatus = 'completed';
                else if (set.webSearchEnabled) set.webSearchStatus = 'skipped';
                set.webSearchResultCount = Number(event.webSearchResultCount) || set.webSearchResultCount;
                set.webReferenceCount = Number(event.webReferenceCount) || set.webReferenceCount;
                if (Array.isArray(event.webReferences)) {
                    set.webReferences = event.webReferences.map(reference => ({ ...reference }));
                    set.webReferenceCount = set.webReferences.length;
                }
                if (set.reasoningStatus === 'running') set.reasoningStatus = 'completed';
                while (set.items.length < set.requestedCount) {
                    set.items.push({ itemIndex: set.items.length, status: 'queued', uri: '', previewUri: '', path: '', result: null, error: '', revealFrames: [], partialIndex: 0, partialTotal: 3 });
                }
            } else if (event.type === 'react_started') {
                set.reasoningStatus = 'running';
                set.reasoningMode = normalizeImageReasoningMode(event.mode || set.reasoningMode);
                set.reasoningModel = event.model || '';
                set.reasoningEffort = event.reasoningEffort || '';
                set.reasoningDepth = event.reasoningDepth || set.reasoningDepth || set.reasoningEffort;
                set.webSearchEnabled = event.webSearchEnabled === true;
                set.reasoningStartedAt ||= Date.now();
                set.reasoningCompletedAt = 0;
            } else if (event.type === 'react_turn_started') {
                const turn = reasoningTurnByNumber(set, event.turn);
                turn.title = event.title || turn.title;
                turn.status = 'running';
                turn.startedAt ||= Date.now();
                turn.completedAt = 0;
                set.reasoningStatus = 'running';
            } else if (event.type === 'react_turn_delta') {
                const turn = reasoningTurnByNumber(set, event.turn);
                turn.text += event.delta || '';
                set.reasoningSummary += event.delta || '';
                set.reasoningStatus = 'running';
                queueReasoningTurnRender(set.setId, turn.turn);
                return;
            } else if (event.type === 'react_turn_completed') {
                const turn = reasoningTurnByNumber(set, event.turn);
                turn.title = event.title || turn.title;
                turn.text = event.text || turn.text;
                turn.status = 'completed';
                turn.completedAt ||= Date.now();
                set.reasoningUsage = normalizeReasoningUsage(event.reasoningUsage || set.reasoningUsage);
            } else if (event.type === 'react_tool_started') {
                const turn = reasoningTurnByNumber(set, event.turn);
                turn.tools.push({
                    callId: event.callId || `${event.tool}-${Date.now()}`,
                    name: event.tool || '',
                    arguments: event.arguments || {},
                    status: 'running',
                    resultCount: 0
                });
                if (['search_web', 'search_visual_references'].includes(event.tool)) {
                    set.webSearchStatus = 'running';
                }
            } else if (event.type === 'react_tool_completed' || event.type === 'react_tool_failed') {
                const turn = reasoningTurnByNumber(set, event.turn);
                const tool = turn.tools.find(item => item.callId === event.callId)
                    || [...turn.tools].reverse().find(item => item.name === event.tool && item.status === 'running');
                if (tool) {
                    tool.status = event.type === 'react_tool_failed' ? 'failed' : 'completed';
                    tool.resultCount = Number(event.resultCount) || 0;
                    tool.error = event.error || '';
                }
                if (['search_web', 'search_visual_references'].includes(event.tool)) {
                    set.webSearchStatus = event.type === 'react_tool_failed' ? 'failed' : 'completed';
                }
            } else if (event.type === 'react_visual_results') {
                set.webSearchStatus = 'running';
                set.webSearchResultCount = Number(event.webSearchResultCount) || set.webSearchResultCount;
            } else if (event.type === 'react_visual_selected') {
                set.webSearchStatus = 'completed';
                set.webReferenceCount = Number(event.selectedCount) || 0;
            } else if (event.type === 'react_continuation_planned') {
                set.operation = event.operation || set.operation;
                set.continuation = true;
                set.continuationRationale = event.rationale || set.continuationRationale;
                if (Array.isArray(event.selectedAssetIds)) set.selectedAssetIds = [...event.selectedAssetIds];
            } else if (event.type === 'react_summary_delta') {
                if (!reasoningTurnsForSet(set).length) set.reasoningSummary += event.delta || '';
                return;
            } else if (event.type === 'react_completed') {
                set.reasoningStatus = 'completed';
                set.reasoningSummary = event.summary || set.reasoningSummary;
                set.reasoningDurationMs = Math.max(0, Number(event.reasoningDurationMs) || set.reasoningDurationMs);
                set.reasoningUsage = normalizeReasoningUsage(event.reasoningUsage || set.reasoningUsage);
                set.reasoningCompletedAt ||= Date.now();
                reasoningTurnsForSet(set).forEach(turn => {
                    if (turn.status === 'running') {
                        turn.status = 'completed';
                        turn.completedAt ||= set.reasoningCompletedAt;
                    }
                });
                set.effectivePrompt = event.prompt || '';
                set.reasoningModel = event.model || set.reasoningModel;
                set.reasoningEffort = event.reasoningEffort || set.reasoningEffort;
                set.reasoningDepth = event.reasoningDepth || set.reasoningDepth || set.reasoningEffort;
                set.webSearchEnabled = event.webSearchEnabled === true || set.webSearchEnabled;
                if (event.webSearchFailed) set.webSearchStatus = 'failed';
                else if (event.webSearchUsed) set.webSearchStatus = 'completed';
                else if (set.webSearchEnabled) set.webSearchStatus = 'skipped';
                set.webSearchResultCount = Number(event.webSearchResultCount) || set.webSearchResultCount;
                set.webReferenceCount = Number(event.webReferenceCount) || set.webReferenceCount;
                requestAnimationFrame(() => {
                    const panel = document.querySelector(`[data-reasoning-set="${CSS.escape(set.setId)}"]`);
                    if (!panel) return;
                    panel.classList.add('is-completion-pulsing');
                    const finishPulse = animationEvent => {
                        if (animationEvent.animationName !== 'reasoningPanelComplete') return;
                        panel.classList.remove('is-completion-pulsing');
                        panel.removeEventListener('animationend', finishPulse);
                    };
                    panel.addEventListener('animationend', finishPulse);
                });
            } else if (event.type === 'react_failed') {
                set.reasoningStatus = 'failed';
                set.reasoningSummary = event.error || '思维处理失败';
                set.reasoningCompletedAt ||= Date.now();
                const turn = activeReasoningTurn(set) || reasoningTurnByNumber(set, 1);
                turn.status = 'failed';
                turn.completedAt ||= Date.now();
            } else if (Number.isInteger(event.itemIndex) && set.items[event.itemIndex]) {
                const item = set.items[event.itemIndex];
                imageRevealItemContext.set(item, {
                    setId: set.setId,
                    itemIndex: event.itemIndex
                });
                if (event.type === 'item_started') item.status = 'running';
                if (event.type === 'item_partial' && item.status !== 'completed') {
                    const previousPartialIndex = Number(item.partialIndex) || 0;
                    const incomingPartialIndex = Number(event.partialIndex) || previousPartialIndex + 1;
                    const incomingPartialTotal = Math.max(1, Number(event.partialTotal) || 3);
                    if (incomingPartialIndex <= previousPartialIndex) {
                        imageStreamDebugLog('partial-ignored', {
                            ...imageStreamDebugContext(item),
                            incomingPartialIndex,
                            incomingPartialTotal,
                            previousPartialIndex
                        });
                    } else {
                        item.status = 'partial';
                        item.uri = event.uri || item.uri;
                        item.previewUri = event.previewUri || item.previewUri;
                        item.path = event.path || item.path;
                        item.width = Number(event.width) || item.width || 0;
                        item.height = Number(event.height) || item.height || 0;
                        item.partialIndex = incomingPartialIndex;
                        item.partialTotal = Math.max(item.partialIndex, item.partialTotal, incomingPartialTotal);
                        appendImageRevealFrame(item, item.previewUri || item.uri, 'partial', {
                            fallbackUri: item.uri,
                            path: item.path,
                            partialIndex: item.partialIndex,
                            partialTotal: item.partialTotal
                        });
                    }
                }
                if (event.type === 'item_completed') {
                    item.status = 'completed';
                    item.result = event.result;
                    item.uri = event.result?.uri || item.uri;
                    item.previewUri = event.result?.previewUri || item.previewUri;
                    item.path = event.result?.path || item.path;
                        appendImageRevealFrame(item, item.previewUri || item.uri, 'final', {
                        fallbackUri: item.previewUri,
                        path: item.path
                    });
                }
                if (event.type === 'item_failed') {
                    item.status = 'failed';
                    item.error = event.error || '生成失败';
                }
            }
            if (event.type === 'set_completed') {
                const wasRunning = set.status === 'running';
                set.reasoningUsage = normalizeReasoningUsage(event.reasoningUsage || set.reasoningUsage);
                if (Array.isArray(event.webReferences)) {
                    set.webReferences = event.webReferences.map(reference => ({ ...reference }));
                    set.webReferenceCount = set.webReferences.length;
                }
                if (Array.isArray(event.items)) {
                    event.items.forEach((result, itemIndex) => {
                        if (!set.items[itemIndex]) return;
                        if (result.ok) {
                            const wasCompleted = set.items[itemIndex].status === 'completed';
                            set.items[itemIndex].status = 'completed';
                            set.items[itemIndex].result = result;
                            set.items[itemIndex].uri = result.uri || set.items[itemIndex].uri;
                            set.items[itemIndex].previewUri = result.previewUri || set.items[itemIndex].previewUri;
                            set.items[itemIndex].path = result.path || set.items[itemIndex].path;
                            if (!wasCompleted) {
                                appendImageRevealFrame(
                                    set.items[itemIndex],
                                    set.items[itemIndex].previewUri || set.items[itemIndex].uri,
                                    'final',
                                    {
                                        fallbackUri: set.items[itemIndex].previewUri,
                                        path: set.items[itemIndex].path
                                    }
                                );
                            }
                        } else {
                            set.items[itemIndex].status = 'failed';
                            set.items[itemIndex].error = result.error || '生成失败';
                        }
                    });
                }
                set.status = set.items.some(item => item.status === 'completed') ? 'completed' : 'failed';
                set.justCompleted = wasRunning && set.status === 'completed' && set.expanded !== true;
            }
            if (event.type === 'set_cancelled') {
                set.status = 'cancelled';
                set.reasoningStatus = set.reasoningStatus === 'running' ? 'cancelled' : set.reasoningStatus;
                set.reasoningCompletedAt ||= Date.now();
                set.items.forEach(item => {
                    if (item.status === 'completed') return;
                    item.status = 'cancelled';
                    item.error = event.error || '生成已停止';
                });
            }
            renderImageGenerationSets();
            if (event.type === 'set_completed') notifyImageGenerationCompleted(set);
            refreshOpenImageViewer(set, event.itemIndex);
        };

        function notifyImageGenerationCompleted(set) {
            if (!set || set.completionNotified) return;
            const completedCount = set.items.filter(item => item.status === 'completed').length;
            if (!completedCount) return;
            set.completionNotified = true;
            const prompt = String(set.originalPrompt || set.prompt || '图片生成任务').replace(/\s+/g, ' ').trim();
            const summary = prompt.length > 24 ? `${prompt.slice(0, 24)}…` : prompt;
            showToast(`您的任务已完成：${summary}`);
        }

        function syncImageGenerationResult(result, requestId = '') {
            const resultSetId = String(result?.setId || result?.requestId || requestId || '');
            if (!resultSetId) return;
            const set = imageGenerationSetById(requestId)
                || imageGenerationSetById(result?.requestId)
                || imageGenerationSetById(resultSetId)
                || createImageGenerationSet(resultSetId, result.requestedCount || 1, result.prompt || '', result);
            const wasRunning = set.status === 'running';
            set.requestId ||= String(result?.requestId || requestId || resultSetId);
            set.status = result.cancelled ? 'cancelled' : result.ok ? 'completed' : 'failed';
            set.sessionId = result.sessionId || set.sessionId;
            set.parentSetId = result.parentSetId || set.parentSetId;
            set.roundNumber = Number(result.roundNumber) || set.roundNumber;
            set.originalPrompt = result.originalPrompt || set.originalPrompt || result.prompt || '';
            set.reasoningDurationMs = Math.max(0, Number(result.reasoningDurationMs) || set.reasoningDurationMs);
            set.reasoningUsage = normalizeReasoningUsage(result.reasoningUsage || set.reasoningUsage);
            set.operation = result.operation || set.operation;
            set.continuation = result.continuation === true || set.continuation;
            set.continuationRationale = result.continuationRationale || set.continuationRationale;
            if (Array.isArray(result.selectedAssetIds)) set.selectedAssetIds = [...result.selectedAssetIds];
            set.effectivePrompt = result.reasoningMode && result.reasoningMode !== 'instant'
                ? result.prompt || set.effectivePrompt
                : set.effectivePrompt;
            set.webSearchEnabled = result.webSearchEnabled === true || set.webSearchEnabled;
            if (result.webSearchFailed) set.webSearchStatus = 'failed';
            else if (result.webSearchUsed) set.webSearchStatus = 'completed';
            else if (set.webSearchEnabled) set.webSearchStatus = 'skipped';
            set.webSearchResultCount = Number(result.webSearchResultCount) || set.webSearchResultCount;
            if (Array.isArray(result.webReferences)) {
                set.webReferences = result.webReferences.map(reference => ({ ...reference }));
                set.webReferenceCount = set.webReferences.length;
            }
            (result.items || []).forEach((resultItem, itemIndex) => {
                const item = set.items[itemIndex] || {
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
                };
                if (!set.items[itemIndex]) set.items[itemIndex] = item;
                item.status = resultItem.ok ? 'completed' : result.cancelled ? 'cancelled' : 'failed';
                item.uri = resultItem.uri || item.uri;
                item.previewUri = resultItem.previewUri || item.previewUri;
                item.fullUri = item.fullUri || resultItem.fullUri || '';
                item.path = resultItem.path || item.path;
                item.result = resultItem.ok
                    ? { ...(item.result || {}), ...resultItem, fullUri: item.fullUri }
                    : null;
                item.error = resultItem.error || '';
            });
            set.items.forEach(item => {
                if (result.cancelled && item.status !== 'completed') {
                    item.status = 'cancelled';
                    item.error = result.error || '生成已停止';
                    return;
                }
                if (item.status !== 'completed') return;
                const existingFinalFrame = Array.isArray(item.revealFrames)
                    ? item.revealFrames.find(frame => frame.kind === 'final')
                    : null;
                if (existingFinalFrame) return;
                appendImageRevealFrame(item, item.uri || item.previewUri, 'final', {
                    fallbackUri: item.previewUri,
                    path: item.path
                });
            });
            set.justCompleted = set.justCompleted === true
                || wasRunning && set.status === 'completed' && set.expanded !== true;
            renderImageGenerationSets();
            notifyImageGenerationCompleted(set);
        }

        function setImageEditSessionUi(active) {
            const placeholder = active
                ? '描述本轮要修改或继续创作的内容；可添加必须参考的图片'
                : '描述要生成的图片；也可以粘贴或拖入参考图片';
            document.getElementById('imageEditPrompt').placeholder = placeholder;
            document.getElementById('imagePromptModalTextarea').placeholder = placeholder;
        }

        function enterImageEditSession(setId) {
            const set = imageGenerationSetById(setId);
            if (!set) return;
            const results = set.items.filter(item => item.result).map(item => item.result);
            if (!results.length) return showToast('该图片集还没有可编辑的完成图片', 'error');
            if (!window.imageEditState.draftBeforeSession) {
                window.imageEditState.draftBeforeSession = {
                    files: [...window.imageEditState.files],
                    prompt: document.getElementById('imageEditPrompt').value,
                    reasoningMode: window.imageEditState.reasoningMode,
                    reasoningAdvancedModel: window.imageEditState.reasoningAdvancedModel,
                    reasoningAdvancedEffort: window.imageEditState.reasoningAdvancedEffort,
                    reasoningPreviousMode: window.imageEditState.reasoningPreviousMode,
                    webSearchEnabled: window.imageEditState.webSearchEnabled
                };
            }
            window.imageEditState.editSession = {
                sessionId: set.sessionId || set.setId,
                setId: set.setId,
                roundNumber: Number(set.roundNumber) || 1,
                previousPrompt: set.originalPrompt || set.prompt || ''
            };
            window.imageEditState.reasoningPreviousMode = normalizeImageReasoningPreset(
                set.reasoningPreviousMode || window.imageEditState.reasoningPreviousMode
            );
            setImageReasoningSelection(
                set.reasoningMode,
                set.reasoningModel,
                set.reasoningDepth || set.reasoningEffort,
                false
            );
            setImageWebSearchEnabled(set.webSearchEnabled === true, false);
            setImageEditSessionUi(true);
            window.imageEditState.files = [];
            renderEditImageList();
            const prompt = document.getElementById('imageEditPrompt');
            prompt.value = '';
            resizeImagePrompt();
            document.getElementById('exitImageEditSessionButton').hidden = false;
            document.getElementById('imageEditHeading').textContent = `会话续作 · 第 ${window.imageEditState.editSession.roundNumber + 1} 轮`;
            document.getElementById('imageEditStatus').textContent = `已切换到第 ${window.imageEditState.editSession.roundNumber} 轮结果，请追加新的修改需求`;
            prompt.focus();
        }

        function exitImageEditSession() {
            const draft = window.imageEditState.draftBeforeSession;
            window.imageEditState.editSession = null;
            window.imageEditState.draftBeforeSession = null;
            if (draft) {
                window.imageEditState.files = [...draft.files];
                document.getElementById('imageEditPrompt').value = draft.prompt || '';
                window.imageEditState.reasoningPreviousMode = normalizeImageReasoningPreset(draft.reasoningPreviousMode);
                setImageReasoningSelection(
                    draft.reasoningMode,
                    draft.reasoningAdvancedModel,
                    draft.reasoningAdvancedEffort,
                    false
                );
                setImageWebSearchEnabled(draft.webSearchEnabled === true, false);
                persistImageGenerationPreferences();
            }
            setImageEditSessionUi(false);
            renderEditImageList();
            resizeImagePrompt();
            document.getElementById('exitImageEditSessionButton').hidden = true;
            document.getElementById('imageEditHeading').textContent = 'GPT Image 2 生图';
            document.getElementById('imageEditStatus').textContent = window.imageEditState.files.length
                ? `已选择 ${window.imageEditState.files.length} 张参考图片`
                : '填写提示词即可生成，参考图可选';
        }

        function advanceImageEditSession(result) {
            if (!window.imageEditState.editSession) return;
            const successful = (result.items || []).filter(item => item.ok);
            if (!successful.length) return;
            window.imageEditState.editSession = {
                sessionId: result.sessionId,
                setId: result.setId,
                roundNumber: Number(result.roundNumber) || window.imageEditState.editSession.roundNumber + 1,
                previousPrompt: result.originalPrompt || result.prompt || ''
            };
            setImageEditSessionUi(true);
            window.imageEditState.files = [];
            renderEditImageList();
            document.getElementById('imageEditPrompt').value = '';
            resizeImagePrompt();
            document.getElementById('imageEditHeading').textContent = `会话续作 · 第 ${window.imageEditState.editSession.roundNumber + 1} 轮`;
            document.getElementById('imageEditStatus').textContent = `第 ${window.imageEditState.editSession.roundNumber} 轮已完成，可继续追加需求`;
        }

        async function closeImageSetDeleteModal() {
            window.imageEditState.pendingDeleteSetId = '';
            window.imageEditState.pendingDeleteSetIds = [];
            await closeAnimatedModal(document.getElementById('imageSetDeleteModal'));
        }

        function requestStopImageGeneration(setId) {
            const set = imageGenerationSetById(setId);
            if (!set || set.status !== 'running' || set.stopRequested) return;
            window.imageEditState.pendingStopSetId = set.setId;
            const modal = document.getElementById('imageGenerationStopModal');
            const appMain = document.getElementById('appMain');
            const message = document.getElementById('imageGenerationStopModalMessage');
            message.textContent = set.items.some(item => item.status === 'completed')
                ? '停止后会保留已经生成完成的图片，其余思维过程和图片生成会立即结束。'
                : '当前思维过程和图片生成会立即结束。此任务尚未完成的内容不会保存。';
            if (modal.parentElement !== appMain) appMain.append(modal);
            openAnimatedModal(modal);
            renderLucideIcons();
            requestAnimationFrame(() => document.getElementById('cancelImageGenerationStopButton').focus());
        }

        async function closeImageGenerationStopModal() {
            window.imageEditState.pendingStopSetId = '';
            await closeAnimatedModal(document.getElementById('imageGenerationStopModal'));
        }

        function handleImageGenerationStopModalBackdrop(event) {
            if (event.target === event.currentTarget) void closeImageGenerationStopModal();
        }

        async function confirmStopImageGeneration() {
            const setId = window.imageEditState.pendingStopSetId;
            const set = imageGenerationSetById(setId);
            if (!set || set.status !== 'running') return void closeImageGenerationStopModal();
            const confirmButton = document.getElementById('confirmImageGenerationStopButton');
            confirmButton.disabled = true;
            setIconLabel(confirmButton, 'loader-circle', '正在停止');
            confirmButton.querySelector('[data-lucide], svg')?.classList.add('is-spinning');
            set.stopRequested = true;
            renderImageGenerationSets();
            try {
                const result = await window.pywebview.api.cancel_image_generation(set.requestId || set.setId);
                if (!result.ok) throw new Error(result.error || '无法停止图片任务');
                await closeImageGenerationStopModal();
            } catch (error) {
                set.stopRequested = false;
                renderImageGenerationSets();
                showToast(error.message || String(error), 'error');
            } finally {
                confirmButton.disabled = false;
                setIconLabel(confirmButton, 'square', '确认停止');
            }
        }

        function handleImageSetDeleteModalBackdrop(event) {
            if (event.target === event.currentTarget) void closeImageSetDeleteModal();
        }

        async function confirmDeleteImageGenerationSet() {
            const pendingIds = window.imageEditState.pendingDeleteSetIds.length
                ? [...window.imageEditState.pendingDeleteSetIds]
                : [window.imageEditState.pendingDeleteSetId];
            const pendingSets = pendingIds
                .map(setId => imageGenerationSetById(setId))
                .filter(set => set && set.status !== 'running')
                .sort((left, right) => Number(right.roundNumber || 0) - Number(left.roundNumber || 0));
            if (!pendingSets.length) return void closeImageSetDeleteModal();
            const confirmButton = document.getElementById('confirmImageSetDeleteButton');
            confirmButton.disabled = true;
            setIconLabel(confirmButton, 'loader-circle', '删除中');
            confirmButton.querySelector('[data-lucide], svg')?.classList.add('is-spinning');
            const deletedSetIds = [];
            const failures = [];
            try {
                for (const set of pendingSets) {
                    try {
                        const result = await window.pywebview.api.delete_image_set(set.sessionId || set.setId, set.setId);
                        if (!result.ok) throw new Error(result.error || '删除图片集失败');
                        deletedSetIds.push(set.setId);
                        if (window.imageEditState.editSession?.setId === set.setId) exitImageEditSession();
                        if (window.imageEditState.viewer.setId === set.setId) await closeImageResultModal();
                    } catch (error) {
                        failures.push(`${set.originalPrompt || set.prompt || set.setId}：${error.message || String(error)}`);
                    }
                }
                window.imageEditState.resultSets = window.imageEditState.resultSets.filter(item => !deletedSetIds.includes(item.setId));
                deletedSetIds.forEach(setId => window.imageEditState.selectedSetIds.delete(setId));
                renderImageGenerationSets();
                await closeImageSetDeleteModal();
                if (failures.length) showToast(`已删除 ${deletedSetIds.length} 个，${failures.length} 个失败`, 'error');
                else showToast(deletedSetIds.length > 1 ? `已删除 ${deletedSetIds.length} 个图片集` : '图片集已删除');
            } catch (error) {
                showToast(error.message || String(error), 'error');
            } finally {
                confirmButton.disabled = false;
                setIconLabel(confirmButton, 'trash-2', '确认删除');
            }
        }

        async function loadImageGenerationHistory() {
            if (!window.pywebview?.api?.list_image_sets) return;
            try {
                const result = await window.pywebview.api.list_image_sets();
                if (!result.ok) throw new Error(result.error || '无法读取图片集历史');
                const existing = new Map(window.imageEditState.resultSets.map(set => [set.setId, set]));
                (result.sets || []).forEach(set => {
                    const current = existing.get(set.setId);
                    if (!current) {
                        existing.set(set.setId, {
                            ...set,
                            reasoningTurns: Array.isArray(set.reasoningTurns) ? set.reasoningTurns : [],
                            reasoningExpanded: set.reasoningExpanded === true,
                            reasoningContentExpanded: set.reasoningContentExpanded === true,
                            webReferences: Array.isArray(set.webReferences) ? set.webReferences : [],
                            webReferencesExpanded: false,
                            finalPromptExpanded: false,
                            webReferencesLoading: false,
                            expanded: false,
                            history: true,
                            previewsLoaded: false,
                            loadingPreviews: false,
                            originalsLoaded: false,
                            loadingOriginals: false
                        });
                    } else if (Array.isArray(set.webReferences) && set.webReferences.length) {
                        current.webReferences = set.webReferences.map(reference => ({ ...reference }));
                        current.webReferenceCount = current.webReferences.length;
                    }
                });
                window.imageEditState.resultSets = [...existing.values()].sort((left, right) =>
                    String(right.createdAt || '').localeCompare(String(left.createdAt || ''))
                );
                window.imageEditState.resultSets.forEach((set, setIndex) => {
                    set.history = setIndex !== 0;
                    set.originalsLoaded = set.history || set.originalsLoaded === true;
                    if (set.history) set.loadingOriginals = false;
                });
                renderImageGenerationSets();
            } catch (error) {
                console.warn('图片集历史加载失败:', error);
            }
        }

        function applyImageViewerTransform() {
            const viewer = window.imageEditState.viewer;
            document.getElementById('imageResultModalImage').style.transform =
                `translate3d(${viewer.x}px, ${viewer.y}px, 0) scale(${viewer.scale})`;
            document.getElementById('imageViewerZoomLabel').textContent = `${Math.round(viewer.scale * 100)}%`;
        }

        function resetImageViewer() {
            Object.assign(window.imageEditState.viewer, {
                scale: 1,
                x: 0,
                y: 0,
                dragging: false,
                pinchDistance: 0,
                touchPointers: new Map()
            });
            document.getElementById('imageResultModalCanvas')?.classList.remove('is-dragging');
            applyImageViewerTransform();
        }

        function setImageViewerScale(scale, clientX = null, clientY = null) {
            const viewer = window.imageEditState.viewer;
            const nextScale = Math.max(0.2, Math.min(8, Number(scale) || 1));
            const canvas = document.getElementById('imageResultModalCanvas');
            if (clientX !== null && clientY !== null && canvas) {
                const rect = canvas.getBoundingClientRect();
                const pointX = clientX - (rect.left + rect.width / 2);
                const pointY = clientY - (rect.top + rect.height / 2);
                const ratio = nextScale / viewer.scale;
                viewer.x = pointX - (pointX - viewer.x) * ratio;
                viewer.y = pointY - (pointY - viewer.y) * ratio;
            }
            viewer.scale = nextScale;
            applyImageViewerTransform();
        }

        function zoomImageViewer(direction) {
            setImageViewerScale(window.imageEditState.viewer.scale * (direction > 0 ? 1.25 : 0.8));
        }

        async function setImageViewerSource(result, preferFullImage = false) {
            if (!result) return;
            window.imageEditState.viewerResult = result;
            const image = document.getElementById('imageResultModalImage');
            image.src = browserImageSource(result.previewUri, result.uri);
            document.getElementById('imageResultModalMeta').textContent = result.status === 'partial'
                ? '过程预览 · 生成仍在继续'
                : result.status === 'reference'
                    ? `${result.provider || '网络参考'} · 已纳入本轮生成`
                    : `${result.actualSize || `${result.width || 0}x${result.height || 0}`} · ${String(result.format || '').toUpperCase()} · 已自动保存`;
            document.getElementById('saveEditedImageButton').disabled = ['partial', 'reference'].includes(result.status) || !result.path;
            if (preferFullImage && !['partial', 'reference'].includes(result.status) && result.path && window.pywebview?.api?.load_generated_image) {
                try {
                    const loaded = await window.pywebview.api.load_generated_image(result.path);
                    if (loaded.ok && window.imageEditState.viewerResult?.path === result.path) image.src = loaded.dataUrl;
                } catch (error) {
                    console.warn('完整图片加载失败，继续使用压缩预览:', error);
                }
            }
        }

        function openImageResultModal(result, setId = '', itemIndex = -1) {
            if (!result?.previewUri && !result?.path) return;
            Object.assign(window.imageEditState.viewer, { setId, itemIndex, scale: 1, x: 0, y: 0, dragging: false });
            void setImageViewerSource(result, result.status !== 'partial');
            resetImageViewer();
            openAnimatedModal(document.getElementById('imageResultModal'));
            renderLucideIcons();
        }

        async function copyGeneratedImage(result) {
            if (!result?.path || result.status === 'partial') {
                showToast('图片生成完成后才能复制', 'error');
                return false;
            }
            if (!window.pywebview?.api?.copy_generated_image) {
                showToast('当前环境无法访问图片剪贴板', 'error');
                return false;
            }
            try {
                const copied = await window.pywebview.api.copy_generated_image(result.path);
                if (!copied.ok) throw new Error(copied.error || '复制图片失败');
                showToast('图片已复制到剪贴板');
                return true;
            } catch (error) {
                showToast(error.message || String(error), 'error');
                return false;
            }
        }

        function refreshOpenImageViewer(set, itemIndex) {
            const modal = document.getElementById('imageResultModal');
            const viewer = window.imageEditState.viewer;
            if (modal.hidden || viewer.setId !== set?.setId || viewer.itemIndex !== itemIndex) return;
            const item = set.items[itemIndex];
            if (!item) return;
            const result = item.status === 'completed'
                ? item.result
                : { path: item.path, previewUri: item.previewUri, uri: item.uri, status: 'partial' };
            void setImageViewerSource(result, item.status === 'completed');
        }

        function initializeImageViewerInteractions() {
            const canvas = document.getElementById('imageResultModalCanvas');
            const modal = document.getElementById('imageResultModal');
            const appMain = document.getElementById('appMain');
            if (modal && appMain && modal.parentElement !== appMain) appMain.append(modal);
            if (!canvas || canvas.dataset.interactionsReady) return;
            canvas.dataset.interactionsReady = 'true';
            canvas.addEventListener('wheel', event => {
                event.preventDefault();
                setImageViewerScale(
                    window.imageEditState.viewer.scale * (event.deltaY < 0 ? 1.12 : 0.89),
                    event.clientX,
                    event.clientY
                );
            }, { passive: false });
            canvas.addEventListener('pointerdown', event => {
                if (event.button !== 0) return;
                const viewer = window.imageEditState.viewer;
                if (event.pointerType === 'touch') {
                    viewer.touchPointers = viewer.touchPointers || new Map();
                    viewer.touchPointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
                    if (viewer.touchPointers.size >= 2) {
                        const points = [...viewer.touchPointers.values()];
                        viewer.pinchDistance = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
                        viewer.dragging = false;
                    }
                    canvas.setPointerCapture(event.pointerId);
                    return;
                }
                viewer.dragging = true;
                viewer.pointerX = event.clientX;
                viewer.pointerY = event.clientY;
                canvas.setPointerCapture(event.pointerId);
                canvas.classList.add('is-dragging');
            });
            canvas.addEventListener('pointermove', event => {
                const viewer = window.imageEditState.viewer;
                if (event.pointerType === 'touch' && viewer.touchPointers?.has(event.pointerId)) {
                    viewer.touchPointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
                    if (viewer.touchPointers.size >= 2) {
                        const points = [...viewer.touchPointers.values()];
                        const distance = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
                        if (viewer.pinchDistance > 0) {
                            setImageViewerScale(
                                viewer.scale * distance / viewer.pinchDistance,
                                (points[0].x + points[1].x) / 2,
                                (points[0].y + points[1].y) / 2
                            );
                        }
                        viewer.pinchDistance = distance;
                    }
                    return;
                }
                if (!viewer.dragging) return;
                viewer.x += event.clientX - viewer.pointerX;
                viewer.y += event.clientY - viewer.pointerY;
                viewer.pointerX = event.clientX;
                viewer.pointerY = event.clientY;
                applyImageViewerTransform();
            });
            const endDrag = event => {
                const viewer = window.imageEditState.viewer;
                if (event.pointerType === 'touch' && viewer.touchPointers?.has(event.pointerId)) {
                    viewer.touchPointers.delete(event.pointerId);
                    viewer.pinchDistance = 0;
                    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
                    return;
                }
                viewer.dragging = false;
                canvas.classList.remove('is-dragging');
                if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
            };
            canvas.addEventListener('pointerup', endDrag);
            canvas.addEventListener('pointercancel', endDrag);
            canvas.addEventListener('dblclick', resetImageViewer);
            canvas.addEventListener('contextmenu', event => {
                event.preventDefault();
                void copyGeneratedImage(window.imageEditState.viewerResult);
            });
        }

        async function closeImageResultModal() {
            window.imageEditState.viewer.setId = '';
            window.imageEditState.viewer.itemIndex = -1;
            await closeAnimatedModal(document.getElementById('imageResultModal'));
            document.getElementById('imageResultModalImage').removeAttribute('src');
            window.imageEditState.viewerResult = null;
            resetImageViewer();
        }

        function handleImageResultModalBackdrop(event) {
            if (event.target === event.currentTarget) closeImageResultModal();
        }

        async function openGeneratedPictures() {
            try {
                const result = await window.pywebview.api.open_generated_pictures();
                if (!result.ok) throw new Error(result.error || '无法打开图片文件夹');
            } catch (error) {
                showToast(error.message || String(error), 'error');
            }
        }

        async function submitImageEdit(event) {
            event.preventDefault();
            const session = window.imageEditState.editSession
                ? { ...window.imageEditState.editSession }
                : null;
            if (session && [...window.imageEditState.activeRequests.values()].some(
                request => request.sessionId === session.sessionId
            )) {
                return showToast('该图片会话已有任务正在运行，请等待完成或先停止任务', 'error');
            }
            const files = [...window.imageEditState.files];
            const activeKey = getActiveKey();
            const promptInput = document.getElementById('imageEditPrompt');
            const prompt = promptInput.value.trim();
            if (!activeKey) return showToast('请先添加并选择一个有效的 API Key', 'error');
            if (!prompt) {
                promptInput.focus();
                return showToast('请输入生图提示词', 'error');
            }
            const button = document.getElementById('generateEditedImageButton');
            const status = document.getElementById('imageEditStatus');
            const requestId = globalThis.crypto?.randomUUID?.() || `image-${Date.now()}-${Math.random().toString(16).slice(2)}`;
            const imageCount = window.imageEditState.imageCount;
            const reasoningOptions = currentImageReasoningRequestOptions();
            const requestOptions = {
                size: document.getElementById('imageEditSize').value,
                quality: document.getElementById('imageEditQuality').value,
                outputPreset: document.getElementById('imageEditOutputPreset').value,
                imageCount,
                requestId,
                sessionId: session?.sessionId || requestId,
                parentSetId: session?.setId || '',
                continuation: Boolean(session),
                ...reasoningOptions,
                reasoningDepth: window.imageEditState.reasoningAdvanced
                    ? window.imageEditState.reasoningAdvancedEffort
                    : '',
                webSearchEnabled: window.imageEditState.reasoningAdvanced
                    ? true
                    : window.imageEditState.webSearchEnabled
            };
            createImageGenerationSet(requestId, imageCount, prompt, {
                sessionId: session?.sessionId || requestId,
                parentSetId: session?.setId || '',
                roundNumber: session ? session.roundNumber + 1 : 1,
                continuation: Boolean(session),
                ...reasoningOptions,
                reasoningDepth: requestOptions.reasoningDepth,
                reasoningStatus: reasoningOptions.reasoningMode === 'instant' ? 'idle' : 'running',
                reasoningStartedAt: Date.now(),
                reasoningTurns: reasoningOptions.reasoningMode === 'instant' ? [] : [{
                    turn: 1,
                    title: '正在分析问题',
                    text: '',
                    status: 'running',
                    startedAt: Date.now(),
                    completedAt: 0,
                    tools: []
                }],
                webSearchEnabled: reasoningOptions.reasoningMode === 'advanced'
                    || reasoningOptions.reasoningMode !== 'instant' && window.imageEditState.webSearchEnabled
            });
            closeImageGenerationControls();
            closeImageReasoningMenu();
            window.imageEditState.activeRequests.set(requestId, {
                requestId,
                sessionId: requestOptions.sessionId,
                parentSetId: requestOptions.parentSetId,
                startedAt: Date.now()
            });
            status.textContent = reasoningOptions.reasoningMode !== 'instant'
                ? `${imageReasoningModeLabel(
                    reasoningOptions.reasoningMode,
                    reasoningOptions.reasoningModel,
                    requestOptions.reasoningDepth || reasoningOptions.reasoningEffort
                )} 模式正在理解需求并设计方案`
                : session
                ? `正在生成第 ${session.roundNumber + 1} 轮图片`
                : files.length
                ? `正在参考 ${files.length} 张图片生成新图`
                : '正在根据提示词生成图片';
            if (!session) {
                promptInput.value = '';
                document.getElementById('imagePromptModalTextarea').value = '';
                resizeImagePrompt();
            }
            let finalStatus = '';
            try {
                const result = await window.pywebview.api.generate_image(
                    activeKey.id,
                    prompt,
                    files.map(file => file.path),
                    requestOptions
                );
                if (result.cancelled) {
                    syncImageGenerationResult(result, requestId);
                    finalStatus = '任务已停止，已完成图片已保留';
                    return;
                }
                if (!result.ok) throw new Error(result.error || '图片生成失败');
                syncImageGenerationResult(result, requestId);
                const currentSession = window.imageEditState.editSession;
                if (session
                    && currentSession?.sessionId === session.sessionId
                    && currentSession?.setId === session.setId) {
                    advanceImageEditSession(result);
                }
                const successCount = result.items.filter(item => item.ok).length;
                finalStatus = `任务完成：已生成 ${successCount}/${result.requestedCount} 张图片`;
            } catch (error) {
                const failedSet = imageGenerationSetById(requestId);
                if (failedSet) {
                    failedSet.status = 'failed';
                    failedSet.items.forEach(item => {
                        if (!['completed', 'failed'].includes(item.status)) {
                            item.status = 'failed';
                            item.error = error.message || String(error);
                        }
                    });
                    renderImageGenerationSets();
                }
                finalStatus = error.message || String(error);
                showToast(finalStatus, 'error');
            } finally {
                window.imageEditState.activeRequests.delete(requestId);
                const activeCount = window.imageEditState.activeRequests.size;
                status.textContent = activeCount
                    ? `${activeCount} 个任务正在后台运行，可继续创建新任务`
                    : finalStatus || '填写提示词即可生成，参考图可选';
                renderImageGenerationSets();
            }
        }

        async function saveEditedImage() {
            const result = window.imageEditState.viewerResult;
            if (!result?.path) return;
            try {
                const saved = await window.pywebview.api.save_edited_image(result.path);
                if (!saved.ok) throw new Error(saved.error || '保存图片失败');
                if (!saved.cancelled) showToast(`已保存到 ${saved.path}`);
            } catch (error) {
                showToast(error.message || String(error), 'error');
            }
        }
