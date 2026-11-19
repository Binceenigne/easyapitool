        function persistImageGenerationPreferences() {
            const state = window.imageEditState;
            localStorage.setItem(IMAGE_GENERATION_PREFERENCES_KEY, JSON.stringify({
                quality: state.quality,
                complexity: state.complexity,
                outputFormat: state.outputFormat,
                outputCompression: state.outputCompression,
                aspectRatio: state.aspectRatio,
                transparency: state.transparency,
                imageModel: state.imageModel,
                imageModelPreference: state.preferredImageModel || '',
                imageCount: state.imageCount,
                reasoningMode: state.reasoningMode,
                reasoningAdvanced: state.reasoningAdvanced,
                reasoningAdvancedModel: state.reasoningAdvancedModel,
                reasoningAdvancedEffort: state.reasoningAdvancedEffort,
                reasoningPreviousMode: state.reasoningPreviousMode,
                webSearchEnabled: state.webSearchEnabled
            }));
        }

        function restoreImageGenerationPreferences() {
            let saved = null;
            try {
                saved = JSON.parse(localStorage.getItem(IMAGE_GENERATION_PREFERENCES_KEY) || 'null');
            } catch (error) {
                console.warn('图片生成配置读取失败:', error);
            }
            if (!saved || typeof saved !== 'object') saved = {};
            window.imageEditState.preferredImageModel = saved.imageModelPreference ?? (saved.imageModel === 'gpt-image-2' ? '' : saved.imageModel || '');
            setImageModel(saved.imageModel || 'gpt-image-2', false);
            setImageQuality(saved.quality || 'auto', false);
            setImageComplexity(saved.complexity || 'auto', false);
            const legacyCompression = { small: 55, medium: 75, large: 90 }[saved.outputPreset];
            setImageTransparency(saved.transparency || 'auto', false);
            setImageOutputFormat(saved.outputFormat || (legacyCompression ? 'jpeg' : 'png'), false);
            setImageOutputCompression(saved.outputCompression ?? legacyCompression, false);
            setImageAspectRatio(saved.aspectRatio || 'auto', false);
            setImageGenerationCount(saved.imageCount || 1, false);
            setImageWebSearchEnabled(saved.webSearchEnabled !== false, false);
            restoreImageReasoningPreferences(saved, false);
        }

        function setWorkspaceMode(mode, showResults = false) {
            const imageMode = mode === 'image-edit';
            window.imageEditState.mode = imageMode ? 'image-edit' : 'monitor';
            document.getElementById('widget-root').classList.toggle('image-edit-mode', imageMode);
            document.getElementById('imageEditWorkspace').hidden = !imageMode;
            const button = document.getElementById('workspaceModeButton');
            button.classList.toggle('is-active', imageMode);
            button.setAttribute('aria-pressed', String(imageMode));
            button.title = imageMode ? '切换到额度监控' : '切换到图片生成';
            button.setAttribute('aria-label', button.title);
            setLucideIcon(button.querySelector('[data-lucide], svg'), imageMode ? 'image' : 'activity');
            if (isAndroidPlatform()) {
                document.getElementById('widget-root').dataset.mobilePage = imageMode ? (showResults ? 'results' : 'image') : 'usage';
                syncMobileNavigation();
            }
            closeKeySwitcher();
            handleResponsiveLayout();
        }

        function toggleWorkspaceMode() {
            setWorkspaceMode(window.imageEditState.mode === 'image-edit' ? 'monitor' : 'image-edit');
        }

        function formatImageFileSize(bytes) {
            const value = Number(bytes) || 0;
            return value >= 1024 * 1024
                ? `${(value / (1024 * 1024)).toFixed(1)} MB`
                : `${Math.max(1, Math.round(value / 1024))} KB`;
        }

        function syncEditImageScrollbar() {
            const list = document.getElementById('editImageList');
            const scrollbar = document.getElementById('editImageScrollbar');
            const thumb = document.getElementById('editImageScrollbarThumb');
            if (!list || !scrollbar || !thumb) return;
            const maximumScrollLeft = Math.max(0, list.scrollWidth - list.clientWidth);
            scrollbar.hidden = maximumScrollLeft <= 1;
            if (scrollbar.hidden) return;
            const trackWidth = scrollbar.clientWidth;
            const thumbWidth = Math.max(24, trackWidth * list.clientWidth / list.scrollWidth);
            const maximumThumbLeft = Math.max(0, trackWidth - thumbWidth);
            const thumbLeft = maximumScrollLeft
                ? list.scrollLeft / maximumScrollLeft * maximumThumbLeft
                : 0;
            thumb.style.width = `${thumbWidth}px`;
            thumb.style.transform = `translateX(${thumbLeft}px)`;
        }

        function initializeEditImageScrollbar() {
            const list = document.getElementById('editImageList');
            const scrollbar = document.getElementById('editImageScrollbar');
            const thumb = document.getElementById('editImageScrollbarThumb');
            if (!list || !scrollbar || !thumb || scrollbar.dataset.ready) return;
            scrollbar.dataset.ready = 'true';
            list.addEventListener('scroll', syncEditImageScrollbar, { passive: true });
            list.addEventListener('wheel', event => {
                const maximumScrollLeft = Math.max(0, list.scrollWidth - list.clientWidth);
                if (!maximumScrollLeft) return;
                const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
                const nextScrollLeft = Math.max(0, Math.min(maximumScrollLeft, list.scrollLeft + delta));
                if (nextScrollLeft === list.scrollLeft) return;
                list.scrollLeft = nextScrollLeft;
                syncEditImageScrollbar();
                event.preventDefault();
            }, { passive: false });

            let dragging = false;
            let pointerOffset = 0;
            const moveThumb = clientX => {
                const trackRect = scrollbar.getBoundingClientRect();
                const thumbWidth = thumb.getBoundingClientRect().width;
                const maximumThumbLeft = Math.max(0, trackRect.width - thumbWidth);
                const thumbLeft = Math.max(0, Math.min(maximumThumbLeft, clientX - trackRect.left - pointerOffset));
                const maximumScrollLeft = Math.max(0, list.scrollWidth - list.clientWidth);
                list.scrollLeft = maximumThumbLeft
                    ? thumbLeft / maximumThumbLeft * maximumScrollLeft
                    : 0;
                syncEditImageScrollbar();
            };
            scrollbar.addEventListener('pointerdown', event => {
                event.preventDefault();
                dragging = true;
                const thumbRect = thumb.getBoundingClientRect();
                pointerOffset = event.target === thumb
                    ? event.clientX - thumbRect.left
                    : thumbRect.width / 2;
                scrollbar.setPointerCapture(event.pointerId);
                moveThumb(event.clientX);
            });
            scrollbar.addEventListener('pointermove', event => {
                if (dragging) moveThumb(event.clientX);
            });
            const stopDragging = event => {
                dragging = false;
                if (scrollbar.hasPointerCapture(event.pointerId)) scrollbar.releasePointerCapture(event.pointerId);
            };
            scrollbar.addEventListener('pointerup', stopDragging);
            scrollbar.addEventListener('pointercancel', stopDragging);
            if ('ResizeObserver' in window) {
                const observer = new ResizeObserver(syncEditImageScrollbar);
                observer.observe(list);
                window.__editImageScrollbarResizeObserver = observer;
            }
            syncEditImageScrollbar();
        }

        function renderEditImageList(scrollToEnd = false) {
            const files = window.imageEditState.files;
            const selection = document.getElementById('editImageSelection');
            const list = document.getElementById('editImageList');
            const addTile = document.getElementById('editImageAddTile');
            const previousScrollLeft = list.scrollLeft;
            selection.classList.toggle('is-empty', files.length === 0);
            document.getElementById('editImageCount').textContent = `${files.length}/16`;
            list.replaceChildren();
            files.forEach((file, index) => {
                const item = document.createElement('div');
                item.className = 'image-edit-file';
                item.title = `${file.name} · ${formatImageFileSize(file.sizeBytes)}`;
                const preview = document.createElement('img');
                preview.src = browserImageSource(file.previewUri, file.uri);
                preview.alt = `参考图 ${index + 1}`;
                preview.draggable = false;
                const name = document.createElement('span');
                name.textContent = `图片${index + 1}`;
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.title = `移除 ${file.name}`;
                remove.setAttribute('aria-label', remove.title);
                remove.innerHTML = iconMarkup('trash-2', 'icon-12');
                remove.addEventListener('click', () => removeEditImage(index));
                item.append(preview, name, remove);
                list.append(item);
            });
            addTile.hidden = files.length >= 16;
            list.append(addTile);
            renderLucideIcons();
            const maximumScrollLeft = Math.max(0, list.scrollWidth - list.clientWidth);
            list.scrollLeft = scrollToEnd
                ? maximumScrollLeft
                : Math.min(previousScrollLeft, maximumScrollLeft);
            syncEditImageScrollbar();
        }

        function appendReferenceImage(file) {
            if (!file?.path || window.imageEditState.files.length >= 16) return false;
            if (window.imageEditState.files.some(existing => existing.path === file.path)) return false;
            window.imageEditState.files.push(file);
            return true;
        }

        async function chooseEditImages() {
            if (!window.pywebview?.api?.choose_edit_images) return;
            try {
                const result = await window.pywebview.api.choose_edit_images();
                if (!result.ok) throw new Error(result.error || '无法选择图片');
                if (!Array.isArray(result.paths) || result.paths.length === 0) return;
                const available = Math.max(0, 16 - window.imageEditState.files.length);
                result.paths.slice(0, available).forEach((path, index) => appendReferenceImage({
                    path,
                    name: result.files?.[index]?.name || path.split(/[\\/]/).pop(),
                    sizeBytes: result.files?.[index]?.sizeBytes || 0,
                    uri: result.files?.[index]?.uri || `file:///${path.replace(/\\/g, '/')}`,
                    previewUri: result.files?.[index]?.previewUri || ''
                }));
                renderEditImageList(true);
                if (result.warning) showToast(result.warning, 'info');
                else if (result.paths.length > available) {
                    showToast(`参考图片最多 16 张，已仅导入前 ${available} 张`, 'info');
                }
                document.getElementById('imageEditStatus').textContent = `已选择 ${window.imageEditState.files.length} 张参考图片`;
            } catch (error) {
                showToast(error.message || String(error), 'error');
            }
        }

        function readImageAsDataUrl(file) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result || ''));
                reader.onerror = () => reject(new Error(`无法读取 ${file.name || '图片'}`));
                reader.readAsDataURL(file);
            });
        }

        async function importReferenceFiles(files) {
            const imageFiles = [...files].filter(file => file.type?.startsWith('image/'));
            if (!imageFiles.length) return false;
            if (!window.pywebview?.api?.import_reference_image) {
                showToast('图片导入服务尚未就绪', 'error');
                return true;
            }
            const available = Math.max(0, 16 - window.imageEditState.files.length);
            const withinSizeLimit = imageFiles.filter(file => file.size <= 50 * 1024 * 1024);
            const oversizedCount = imageFiles.length - withinSizeLimit.length;
            if (imageFiles.length > available) {
                showToast(`参考图片最多 16 张，已仅导入前 ${available} 张`, 'info');
            }
            if (oversizedCount) {
                showToast(`${oversizedCount} 张图片超过 50 MB，未导入`, 'error');
            }
            for (const file of withinSizeLimit.slice(0, available)) {
                try {
                    const result = await window.pywebview.api.import_reference_image(
                        await readImageAsDataUrl(file),
                        file.name || 'reference.png'
                    );
                    if (!result.ok) throw new Error(result.error || '图片导入失败');
                    appendReferenceImage(result);
                } catch (error) {
                    showToast(error.message || String(error), 'error');
                }
            }
            renderEditImageList(true);
            document.getElementById('imageEditStatus').textContent = window.imageEditState.files.length
                ? `已选择 ${window.imageEditState.files.length} 张参考图片`
                : '填写提示词即可生成，参考图可选';
            return true;
        }

        function imagePromptMetrics(textarea) {
            const style = getComputedStyle(textarea);
            const lineHeight = Number.parseFloat(style.lineHeight) || 20;
            const verticalPadding = Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom);
            const pageZoom = Math.max(0.01, Number(getPageZoom()) || 1);
            const prompt = textarea.closest('.image-edit-prompt');
            const footer = document.querySelector('.image-generation-footer');
            const form = document.getElementById('imageEditForm');
            const textareaRect = textarea.getBoundingClientRect();
            const formRect = form?.getBoundingClientRect();
            const footerRect = footer?.getBoundingClientRect();
            const formStyle = form ? getComputedStyle(form) : null;
            const fixedBeforePrompt = formRect ? (textareaRect.top - formRect.top) / pageZoom : 0;
            const footerHeight = (footerRect?.height || 0) / pageZoom;
            const formGap = Number.parseFloat(formStyle?.rowGap || formStyle?.gap || '0');
            const formPaddingBottom = Number.parseFloat(formStyle?.paddingBottom || '0');
            const containerHeight = formRect ? formRect.height / pageZoom : window.innerHeight / pageZoom;
            const availableHeight = Math.max(
                lineHeight + verticalPadding,
                containerHeight - fixedBeforePrompt - footerHeight - formGap - formPaddingBottom - 2
            );
            const minimum = Math.min(Math.ceil(lineHeight * 7 + verticalPadding), Math.floor(availableHeight));
            const maximum = Math.max(minimum, Math.floor(availableHeight));
            return {
                minimum,
                maximum,
                expandThreshold: Math.max(minimum, Math.floor(maximum * 0.75))
            };
        }

        function resizeImagePrompt() {
            const textarea = document.getElementById('imageEditPrompt');
            const metrics = imagePromptMetrics(textarea);
            textarea.style.height = 'auto';
            const contentHeight = Math.max(metrics.minimum, textarea.scrollHeight);
            const nextHeight = Math.min(metrics.maximum, contentHeight);
            textarea.style.height = `${nextHeight}px`;
            const overflowing = contentHeight > metrics.maximum + 1;
            textarea.style.overflowY = overflowing ? 'auto' : 'hidden';
            document.getElementById('expandImagePromptButton').hidden = !(
                isAndroidPlatform() && document.documentElement.dataset.keyboardVisible === 'true'
            ) && contentHeight < metrics.expandThreshold;
        }

        function openImagePromptModal() {
            const modal = document.getElementById('imagePromptModal');
            const appMain = document.getElementById('appMain');
            if (modal && appMain && modal.parentElement !== appMain) appMain.append(modal);
            const modalTextarea = document.getElementById('imagePromptModalTextarea');
            modalTextarea.value = document.getElementById('imageEditPrompt').value;
            openAnimatedModal(modal);
            renderLucideIcons();
            requestAnimationFrame(() => modalTextarea.focus());
        }

        async function closeImagePromptModal() {
            const modal = document.getElementById('imagePromptModal');
            if (modal.hidden) return;
            const prompt = document.getElementById('imageEditPrompt');
            prompt.value = document.getElementById('imagePromptModalTextarea').value;
            resizeImagePrompt();
            updateImageGenerationControlSummary();
            await closeAnimatedModal(modal);
        }

        function handleImagePromptModalBackdrop(event) {
            if (event.target === event.currentTarget) closeImagePromptModal();
        }

        function removeEditImage(index) {
            window.imageEditState.files.splice(index, 1);
            renderEditImageList();
            document.getElementById('imageEditStatus').textContent = window.imageEditState.files.length
                ? `已选择 ${window.imageEditState.files.length} 张参考图片`
                : '填写提示词即可生成，参考图可选';
        }

        function positionImageGenerationPanel() {
            const panel = document.getElementById('imageGenerationControlPanel');
            const button = document.getElementById('imageGenerationControlsButton');
            const form = document.getElementById('imageEditForm');
            if (!panel || panel.hidden || !button || !form) return;
            const { layer, scale, rect: layerRect } = pageZoomMetrics();
            if (!layer || !layerRect) return;
            const buttonRect = button.getBoundingClientRect();
            const formRect = form.getBoundingClientRect();
            panel.style.setProperty('--image-popover-left', `${(formRect.left - layerRect.left) / scale + 12}px`);
            panel.style.setProperty('--image-popover-width', `${Math.max(0, formRect.width / scale - 24)}px`);
            panel.style.setProperty('--image-popover-bottom', `${Math.max(12, (layerRect.bottom - buttonRect.top) / scale + 8)}px`);
        }

        function closeImageGenerationControls() {
            const panel = document.getElementById('imageGenerationControlPanel');
            const button = document.getElementById('imageGenerationControlsButton');
            if (!panel || panel.hidden) return;
            clearTimeout(panel.__hideTimer);
            panel.classList.remove('is-open');
            button.setAttribute('aria-expanded', 'false');
            const icon = button.querySelector('[data-lucide="chevron-up"], [data-lucide="chevron-down"], svg:last-child');
            if (icon) setLucideIcon(icon, 'chevron-up');
            panel.__hideTimer = setTimeout(() => { panel.hidden = true; }, 180);
        }

        function toggleImageGenerationControls() {
            const panel = document.getElementById('imageGenerationControlPanel');
            const button = document.getElementById('imageGenerationControlsButton');
            if (!panel.hidden) return closeImageGenerationControls();
            closeImageReasoningMenu();
            clearTimeout(panel.__hideTimer);
            panel.hidden = false;
            positionImageGenerationPanel();
            button.setAttribute('aria-expanded', 'true');
            const icon = button.querySelector('[data-lucide="chevron-up"], [data-lucide="chevron-down"], svg:last-child');
            if (icon) setLucideIcon(icon, 'chevron-down');
            requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.add('is-open')));
        }

        const IMAGE_MODEL_LABELS = {
            'gpt-image-2': '2.0',
            'gpt-image-2.5': '2.5',
            'gpt-image-2.5-flare': '2.5 Flare',
            'gpt-image-2.5-sunburst': '2.5 Sunburst',
            'gpt-image-2.6-flare': '2.6 Flare',
            'gpt-image-2.6-sunburst': '2.6 Sunburst'
        };

        function activeImageProfile() {
            return getActiveKey()?.appModels || null;
        }

        function syncManagedImageControls() {
            const state = window.imageEditState;
            const profile = activeImageProfile();
            const models = profile?.imageModels?.length ? profile.imageModels : Object.keys(IMAGE_MODEL_LABELS);
            const fallback = profile?.defaultImage || 'gpt-image-2';
            const signature = JSON.stringify([fallback, models]);
            const picker = document.getElementById('imageModelPicker');
            if (state.modelSignature !== signature) {
                const selected = state.preferredImageModel;
                picker.replaceChildren(...models.map(model => new Option(IMAGE_MODEL_LABELS[model] || model, model)));
                state.modelSignature = signature;
                setImageModel(models.includes(selected) ? selected : fallback, false);
            }
            setImageOutputFormat(state.outputFormat, false);
            if (typeof syncImageReasoningLabels === 'function') syncImageReasoningLabels();
        }

        function setImageQuality(quality, persist = true) {
            const state = window.imageEditState;
            const control = document.getElementById('imageEditQuality');
            const extended = /^gpt-image-2\.[56](?:-|$)/.test(state.imageModel);
            for (const value of ['xhigh', 'max']) {
                control.querySelector('option[value="' + value + '"]').disabled = !extended;
            }
            state.quality = ['auto', 'low', 'medium', 'high'].includes(quality)
                || (extended && ['xhigh', 'max'].includes(quality)) ? quality : 'auto';
            control.value = state.quality;
            updateImageGenerationControlSummary();
            if (persist) persistImageGenerationPreferences();
        }

        function setImageComplexity(complexity, persist = true) {
            const state = window.imageEditState;
            const control = document.getElementById('imageEditComplexity');
            control.disabled = !state.imageModel.startsWith('gpt-image-2.6-');
            state.complexity = !control.disabled && ['low', 'medium', 'high'].includes(complexity) ? complexity : 'auto';
            control.value = state.complexity;
            if (persist) persistImageGenerationPreferences();
        }

        function setImageOutputFormat(format, persist = true) {
            const state = window.imageEditState;
            const control = document.getElementById('imageEditOutputFormat');
            const profile = activeImageProfile();
            const svg = profile ? profile.svg?.enabled === true : window.appState.features?.svg === true;
            const svgChoice = control.querySelector('option[value="svg"]');
            svgChoice.hidden = svgChoice.disabled = !svg;
            const jpeg = control.querySelector('option[value="jpeg"]');
            jpeg.disabled = state.transparency === 'transparent';
            const supported = ['png', 'jpeg', 'webp'].includes(format) || (svg && format === 'svg');
            state.outputFormat = supported && !(format === 'jpeg' && jpeg.disabled) ? format : 'png';
            control.value = state.outputFormat;
            const svgProfile = document.getElementById('imageSVGProfile');
            const vector = activeImageProfile()?.svg;
            svgProfile.hidden = state.outputFormat !== 'svg' || !vector;
            svgProfile.textContent = vector ? [vector.model || '上游默认模型', vector.effort || '上游默认深度'].join(' · ') : '';
            document.getElementById('imageEditCompressionLabel').hidden = !['jpeg', 'webp'].includes(state.outputFormat);
            if (persist) persistImageGenerationPreferences();
        }

        function setImageOutputCompression(value, persist = true) {
            const rounded = Number(value);
            const compression = value != null && value !== '' && Number.isInteger(rounded) && rounded >= 0 && rounded <= 100
                ? rounded : null;
            window.imageEditState.outputCompression = compression;
            document.getElementById('imageEditCompression').value = compression ?? '';
            if (persist) persistImageGenerationPreferences();
        }

        function setImageAspectRatio(ratio, persist = true) {
            if (!IMAGE_SIZE_PRESETS[ratio]) return;
            window.imageEditState.aspectRatio = ratio;
            document.querySelectorAll('#imageAspectRatioButtons button').forEach(button => {
                const active = button.dataset.ratio === ratio;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', String(active));
            });
            updateImageGenerationControlSummary();
            if (persist) persistImageGenerationPreferences();
        }

        function setImageTransparency(transparency, persist = true) {
            if (!['auto', 'opaque', 'transparent'].includes(transparency)) return;
            window.imageEditState.transparency = transparency;
            setImageOutputFormat(window.imageEditState.outputFormat, false);
            document.querySelectorAll('#imageTransparencyButtons button').forEach(button => {
                const active = button.dataset.transparency === transparency;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', String(active));
            });
            updateImageGenerationControlSummary();
            if (persist) persistImageGenerationPreferences();
        }

        function visibleImagePrompt(prompt) {
            let clean = String(prompt || '').trim();
            clean = clean.replace(/\s*\[内部输出约束\][\s\S]*?\[\/内部输出约束\]\s*$/, '').trim();
            const suffix = /(?:\s*以以下比例要求为准：强制生成比例为\d+:\d+的图片|\s*以以下透明度要求为准：强制生成(?:不透明|透明)背景的图片)$/;
            while (suffix.test(clean)) clean = clean.replace(suffix, '').trim();
            return clean;
        }

        function setImageModel(model, persist = true) {
            const picker = document.getElementById('imageModelPicker');
            const fallback = activeImageProfile()?.defaultImage || 'gpt-image-2';
            if (![...picker.options].some(option => option.value === model)) model = fallback;
            window.imageEditState.imageModel = model;
            if (persist) window.imageEditState.preferredImageModel = model;
            picker.value = model;
            if (!window.imageEditState.editSession) {
                const label = IMAGE_MODEL_LABELS[model];
                document.getElementById('imageEditHeading').textContent = label ? 'GPT Image ' + label + ' 生图' : model + ' 生图';
            }
            setImageQuality(window.imageEditState.quality, false);
            setImageComplexity(window.imageEditState.complexity, false);
            if (persist) persistImageGenerationPreferences();
        }

        function setImageGenerationCount(count, persist = true) {
            window.imageEditState.imageCount = Math.max(1, Math.min(9, Number(count) || 1));
            document.querySelectorAll('#imageGenerationCountButtons button').forEach(button => {
                const active = Number(button.dataset.count) === window.imageEditState.imageCount;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', String(active));
            });
            updateImageGenerationControlSummary();
            if (persist) persistImageGenerationPreferences();
        }

        function updateImageGenerationControlSummary() {
            const ratio = window.imageEditState.aspectRatio;
            const qualityLabels = { auto: '自动质量', low: '低质量', medium: '中质量', high: '高质量', xhigh: '超高质量', max: '最高质量' };
            const size = IMAGE_SIZE_PRESETS[ratio] || 'auto';
            document.getElementById('imageEditSize').value = size;
            const qualitySummary = document.getElementById('imageGenerationQualitySummary');
            qualitySummary.textContent = qualityLabels[window.imageEditState.quality];
            qualitySummary.dataset.quality = window.imageEditState.quality;
            document.getElementById('imageGenerationRatioSummary').textContent = ratio === 'auto' ? '智能' : ratio;
            document.getElementById('imageGenerationModelSummary').textContent = IMAGE_MODEL_LABELS[window.imageEditState.imageModel] || window.imageEditState.imageModel;
            document.getElementById('imageGenerationCountSummary').textContent = `${window.imageEditState.imageCount} 张`;
            const buttonLabel = document.querySelector('#generateEditedImageButton span');
            if (buttonLabel && !window.imageEditState.busy) {
                buttonLabel.textContent = `生成 ${window.imageEditState.imageCount} 张`;
            }
        }
