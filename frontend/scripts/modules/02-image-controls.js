        function persistImageGenerationPreferences() {
            const state = window.imageEditState;
            localStorage.setItem(IMAGE_GENERATION_PREFERENCES_KEY, JSON.stringify({
                quality: state.preferredImageQuality ?? state.quality,
                complexity: state.preferredImageComplexity,
                outputFormat: state.preferredImageFormat,
                outputCompression: state.outputCompression,
                frameCount: state.frameCount,
                resolution: state.resolution,
                aspectRatio: state.aspectRatio,
                transparency: state.transparency,
                imageModel: state.imageModel,
                imageModelPreference: state.preferredImageModel || '',
                imageCount: state.preferredImageCount ?? state.imageCount,
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
            const state = window.imageEditState;
            state.preferredImageModel = saved.imageModelPreference ?? (saved.imageModel === 'gpt-image-2' ? '' : saved.imageModel || '');
            state.preferredImageComplexity = saved.complexity || 'auto';
            const legacyCompression = { small: 55, medium: 75, large: 90 }[saved.outputPreset];
            state.preferredImageFormat = saved.outputFormat || (legacyCompression ? 'jpeg' : 'png');
            state.preferredImageQuality = saved.quality || 'auto';
            syncManagedImageControls();
            setImageTransparency(saved.transparency || 'auto', false);
            setImageOutputCompression(saved.outputCompression ?? legacyCompression ?? 75, false);
            setImageFrameCount(saved.frameCount ?? 9, false);
            setImageResolution(saved.resolution || 'sd', false);
            setImageAspectRatio(saved.aspectRatio || 'auto', false);
            setImageGenerationCount(saved.imageCount || 1, false);
            setImageWebSearchEnabled(saved.webSearchEnabled !== false, false);
            restoreImageReasoningPreferences(saved, false);
        }

        function setWorkspaceMode(mode) {
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
            const empty = files.length === 0;
            const referencesChanged = selection.classList.contains('is-empty') !== empty;
            selection.classList.toggle('is-empty', empty);
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
            if (referencesChanged) setImageOutputFormat(window.imageEditState.preferredImageFormat, false);
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
            document.getElementById('expandImagePromptButton').hidden = contentHeight < metrics.expandThreshold;
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
            closeImagePicker(true);
            const metrics = pageZoomMetrics();
            const { layer, scale, rect: layerRect } = metrics;
            if (!layer || !layerRect) return;
            const buttonRect = button.getBoundingClientRect();
            const formRect = form.getBoundingClientRect();
            panel.style.setProperty('--image-popover-left', `${(formRect.left - layerRect.left) / scale + 12}px`);
            panel.style.setProperty('--image-popover-width', `${Math.max(0, formRect.width / scale - 24)}px`);
            positionImagePopover(panel, buttonRect, metrics);
        }

        function closeImageGenerationControls() {
            const panel = document.getElementById('imageGenerationControlPanel');
            const button = document.getElementById('imageGenerationControlsButton');
            if (!panel || button?.getAttribute('aria-expanded') !== 'true') return;
            closeImagePicker();
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
            if (button.getAttribute('aria-expanded') === 'true') return closeImageGenerationControls();
            closeImageReasoningMenu();
            clearTimeout(panel.__hideTimer);
            panel.hidden = false;
            positionImageGenerationPanel();
            button.setAttribute('aria-expanded', 'true');
            const icon = button.querySelector('[data-lucide="chevron-up"], [data-lucide="chevron-down"], svg:last-child');
            if (icon) setLucideIcon(icon, 'chevron-down');
            requestAnimationFrame(() => requestAnimationFrame(() => {
                if (button.getAttribute('aria-expanded') === 'true') panel.classList.add('is-open');
            }));
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
            const key = getActiveKey();
            if (key?.appModels) return key.appModels;
            if (!key?.directApi) return null;
            const usable = (key.models || []).filter(model => /^gpt-image-/.test(model));
            if (!usable.length) return null;
            const known = Object.keys(IMAGE_MODEL_LABELS).filter(model => usable.includes(model));
            const images = [...known, ...usable.filter(model => !known.includes(model)).sort()];
            return { imageModels: images, defaultImage: images.includes('gpt-image-2') ? 'gpt-image-2' : images[0],
                svg: { enabled: true } };
        }

        // OpenAI's Images API guide lists model-specific rendering and output controls.
        function imageModelFeatures(model = window.imageEditState.imageModel) {
            const configured = activeImageProfile()?.imageFeatures?.[model];
            if (Array.isArray(configured?.quality) && configured.sizeMode) return configured;
            const base = model.replace(/-\d{4}-\d{2}-\d{2}$/, '');
            const native26 = /^gpt-image-2\.6-(?:flare|sunburst)$/.test(base);
            const extended = native26 || /^gpt-image-2\.5(?:-(?:flare|sunburst))?$/.test(base);
            const standard = /^gpt-image-(?:1(?:-mini|\.5)?|2)$/.test(base) || base === 'chatgpt-image-latest';
            const quality = extended ? ['low', 'medium', 'high', 'xhigh', 'max'] : standard ? ['low', 'medium', 'high'] : [];
            const sizeMode = extended || base === 'gpt-image-2' ? 'flexible' : standard ? 'fixed' : 'auto';
            return { quality, complexity: native26, animation: native26, svg: native26, sizeMode };
        }

        let activeImagePicker = null;

        function closeImagePicker(restoreFocus = false) {
            if (!activeImagePicker) return;
            const picker = activeImagePicker;
            activeImagePicker = null;
            const [button, menu] = picker.children;
            picker.classList.remove('is-picker-open');
            menu.hidden = true;
            button.setAttribute('aria-expanded', 'false');
            if (restoreFocus) button.focus({ preventScroll: true });
        }

        function positionImagePicker(button, menu) {
            const panel = document.getElementById('imageGenerationControlPanel').getBoundingClientRect();
            const picker = button.parentElement;
            const anchor = picker.getBoundingClientRect();
            const scale = anchor.height / picker.offsetHeight || 1;
            const above = (anchor.top - Math.max(panel.top, 0)) / scale - 10;
            const below = (Math.min(panel.bottom, innerHeight) - anchor.bottom) / scale - 10;
            const opensUp = below < Math.min(menu.scrollHeight + 2, 220) && above > below;
            menu.classList.toggle('opens-up', opensUp);
            menu.style.maxHeight = Math.max(0, Math.min(220, opensUp ? above : below)) + 'px';

            menu.style.maxWidth = '';
            const left = (anchor.right - panel.left) / scale - 5;
            const right = (panel.right - anchor.left) / scale - 5;
            const alignEnd = menu.offsetWidth > right && left > right;
            menu.classList.toggle('align-end', alignEnd);
            menu.style.maxWidth = Math.min(220, alignEnd ? left : right) + 'px';
        }

        const IMAGE_MODEL_TIPS = {
            'gpt-image-1-mini': '较轻量的第一代模型，适合低成本草稿和简单视觉任务。',
            'gpt-image-1': '通用的第一代图像模型，适合文字出图和常规参考图编辑。',
            'gpt-image-1.5': '较早的高保真版本，适合常规图像生成及参考图编辑。',
            'chatgpt-image-latest': 'ChatGPT 图像模型的滚动别名，具体模型版本可能更新。',
            'gpt-image-2': '适合通用生图和高保真参考图编辑，支持自定义尺寸与低、中、高三档质量。',
            'gpt-image-2.5': '2.5 系列的兼容名称，支持五档质量，可用性由所选上游目录提供。',
            'gpt-image-2.5-flare': '适合快速生成、日常设计和多轮迭代，支持五档渲染质量。',
            'gpt-image-2.5-sunburst': '适合高保真图片编辑、保持参考图细节与精确调整，支持五档渲染质量。',
            'gpt-image-2.6-flare': '强大且经济的轻量级模型，适合快速出图与多轮修改。',
            'gpt-image-2.6-sunburst': '适合最复杂的任务，包括密集文字、多主体构图和精细编辑。'
        };
        const IMAGE_FORMAT_CHOICES = [
            ['png', 'PNG', '无损压缩，保留细节，支持透明背景，适合图标、插图和后期制作。'],
            ['jpeg', 'JPEG', '有损压缩，适合照片和不透明的成品图，压缩质量默认 75。'],
            ['webp', 'WebP', '兼顾清晰度和文件体积，可保留透明通道，压缩质量默认 75。'],
            ['svg', 'SVG', '可缩放矢量格式，适合图标、标志和线稿。每次生成一张静态图，需账号开通权限，仅用于无参考图的生成。'],
            ['gif', 'GIF', '可循环播放的动图。请选择帧数，每帧默认 200 毫秒，上游需开通 GIF 能力。']
        ];
        const IMAGE_QUALITY_CHOICES = [
            ['low', '低', '适合草稿和快速试构图，较小的文字、纹理和细节可能产生伪影。'],
            ['medium', '中', '适合一般出图，兼顾清晰度、生成速度和用量。'],
            ['high', '高', '适合正式出图，通常保留更多纹理和小字细节。'],
            ['xhigh', '超高', '进一步提高细节保真度，适合复杂材质、密集文字和精细参考图编辑。'],
            ['max', '最高', '该模型的最高显式渲染档位，适合精细成品，通常耗时和用量更高。']
        ];

        function imageModelTip(model) {
            const base = model.replace(/-\d{4}-\d{2}-\d{2}$/, '');
            const tip = Object.hasOwn(IMAGE_MODEL_TIPS, base) ? IMAGE_MODEL_TIPS[base]
                : '上游目录提供的图像模型，具体能力由该上游决定。';
            const features = imageModelFeatures(model);
            return features.complexity ? tip + ' 支持五档画质、复杂度、SVG 和 GIF。' : tip;
        }

        function initializeImageOptionTooltips() {
            const panels = ['imageGenerationControlPanel', 'imageReasoningMenu'].map(id => document.getElementById(id));
            const bubble = document.createElement('div');
            bubble.id = 'imageOptionTooltip';
            bubble.setAttribute('role', 'tooltip');
            bubble.hidden = true;
            document.body.append(bubble);
            let target = null;
            let hover = null;
            let timer;
            function hide() {
                clearTimeout(timer);
                if (target?.getAttribute('aria-describedby') === bubble.id) target.removeAttribute('aria-describedby');
                target = null;
                bubble.hidden = true;
            }
            function show(element, panel) {
                if (!element?.dataset.imageTip || panel.hidden) return;
                if (target === element) return;
                hide();
                target = element;
                timer = setTimeout(() => {
                    if (!element.getClientRects().length || !panel.classList.contains('is-open')) return hide();
                    bubble.textContent = element.dataset.imageTip;
                    bubble.hidden = false;
                    element.setAttribute('aria-describedby', bubble.id);
                    const box = element.getBoundingClientRect();
                    const width = bubble.offsetWidth;
                    const height = bubble.offsetHeight;
                    const left = Math.max(7, Math.min(innerWidth - width - 7, box.left + (box.width - width) / 2));
                    const top = box.top >= height + 12 ? box.top - height - 6 : box.bottom + 6;
                    bubble.style.left = left + 'px';
                    bubble.style.top = top + 'px';
                }, 280);
            }
            panels[0].addEventListener('scroll', () => closeImagePicker(true));
            for (const panel of panels) {
                const trackPointer = event => {
                    if (event.pointerType === 'touch') return;
                    hover = { panel, x: event.clientX, y: event.clientY };
                    show(event.target.closest('[data-image-tip]'), panel);
                };
                panel.addEventListener('pointerover', trackPointer);
                panel.addEventListener('pointermove', trackPointer);
                panel.addEventListener('pointerleave', () => {
                    if (hover?.panel === panel) hover = null;
                });
                panel.addEventListener('pointerout', event => {
                    if (target && target.contains(event.target) && !target.contains(event.relatedTarget)) hide();
                });
                panel.addEventListener('focusin', event => {
                    if (event.target.matches(':focus-visible')) show(event.target.closest('[data-image-tip]'), panel);
                });
                panel.addEventListener('focusout', hide);
                panel.addEventListener('scroll', () => {
                    hide();
                    if (hover?.panel !== panel) return;
                    const hit = document.elementFromPoint(hover.x, hover.y);
                    if (panel.contains(hit)) show(hit.closest('[data-image-tip]'), panel);
                }, true);
                panel.addEventListener('click', hide);
                new MutationObserver(() => {
                    if (target && panel.contains(target) && (panel.hidden || !panel.classList.contains('is-open'))) hide();
                }).observe(panel, { attributes: true, attributeFilter: ['hidden', 'class'] });
            }
        }

        function availableImageModels(profile) {
            return profile?.imageModels?.length ? profile.imageModels : Object.keys(IMAGE_MODEL_LABELS);
        }

        function syncManagedImageControls() {
            const state = window.imageEditState;
            const profile = activeImageProfile();
            const models = availableImageModels(profile);
            const fallback = profile?.defaultImage || 'gpt-image-2';
            const signature = JSON.stringify([fallback, models]);
            if (state.modelSignature !== signature) {
                state.modelSignature = signature;
                setImageModel(models.includes(state.preferredImageModel) ? state.preferredImageModel : fallback, false);
            } else {
                setImageComplexity(state.preferredImageComplexity, false);
                setImageOutputFormat(state.preferredImageFormat, false);
            }
            if (typeof syncImageReasoningLabels === 'function') syncImageReasoningLabels();
        }

        function buildImagePicker(container, items, onSelect, disabled) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'image-choice-trigger';
            button.disabled = disabled;
            button.setAttribute('aria-haspopup', 'listbox');
            button.setAttribute('aria-expanded', 'false');

            const label = document.createElement('span');
            label.id = container.id + 'Value';
            button.append(label);
            button.insertAdjacentHTML('beforeend', iconMarkup('chevron-down'));

            const menu = document.createElement('div');
            menu.id = container.id + 'Menu';
            menu.className = 'image-choice-menu';
            menu.setAttribute('role', 'listbox');
            menu.hidden = true;
            button.setAttribute('aria-controls', menu.id);
            const labelledBy = container.getAttribute('aria-labelledby');
            if (labelledBy) {
                button.setAttribute('aria-labelledby', labelledBy + ' ' + label.id);
                menu.setAttribute('aria-labelledby', labelledBy);
            } else {
                const name = container.getAttribute('aria-label') || '选项';
                menu.setAttribute('aria-label', name);
            }

            const options = items.map(([value, text, tip]) => {
                const option = document.createElement('button');
                option.type = 'button';
                option.tabIndex = -1;
                option.setAttribute('role', 'option');
                option.dataset.value = value;
                option.textContent = text;
                if (tip) option.dataset.imageTip = tip;
                return option;
            });
            menu.append(...options);
            container.append(button, menu);
            renderLucideIcons();

            function focusOption(option) {
                if (option.offsetTop < menu.scrollTop) {
                    menu.scrollTop = option.offsetTop;
                } else if (option.offsetTop + option.offsetHeight > menu.scrollTop + menu.clientHeight) {
                    menu.scrollTop = option.offsetTop + option.offsetHeight - menu.clientHeight;
                }
                option.focus({ preventScroll: true });
            }

            function open() {
                closeImagePicker();
                activeImagePicker = container;
                container.classList.add('is-picker-open');
                menu.hidden = false;
                positionImagePicker(button, menu);
                button.setAttribute('aria-expanded', 'true');
                focusOption(menu.querySelector('[aria-selected="true"]') || options[0]);
            }

            button.addEventListener('click', () => {
                if (menu.hidden) open();
                else closeImagePicker(true);
            });
            menu.addEventListener('click', event => {
                const option = event.target.closest('[role="option"]');
                if (!option) return;
                closeImagePicker(true);
                onSelect(option.dataset.value);
            });
            button.addEventListener('keydown', event => {
                if (menu.hidden && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
                    event.preventDefault();
                    open();
                }
            });
            menu.addEventListener('keydown', event => {
                const { key } = event;
                if (key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    closeImagePicker(true);
                    return;
                }
                if (key === 'Tab') {
                    closeImagePicker(true);
                    return;
                }
                if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key)) return;
                event.preventDefault();
                const index = options.indexOf(document.activeElement);
                const next = key === 'Home' ? 0 : key === 'End' ? options.length - 1
                    : Math.max(0, Math.min(options.length - 1, index + (key === 'ArrowDown' ? 1 : -1)));
                focusOption(options[next]);
            });
            container.onfocusout = event => {
                if (activeImagePicker === container && !container.contains(event.relatedTarget)) closeImagePicker();
            };
        }

        function renderImageChoices(id, items, value, onSelect, { segmented = false, disabled = false } = {}) {
            const container = document.getElementById(id);
            const inline = segmented || items.length <= 3;
            const signature = JSON.stringify([items, inline, disabled]);
            if (container.dataset.signature !== signature) {
                const hadFocus = container.contains(document.activeElement);
                if (activeImagePicker === container) closeImagePicker();
                container.dataset.signature = signature;
                container.classList.toggle('image-control-segmented', inline);
                container.classList.toggle('image-choice-picker', !inline);
                container.replaceChildren();
                if (inline) {
                    container.setAttribute('role', 'group');
                    for (const [item, label, tip] of items) {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.dataset.value = item;
                        button.textContent = label;
                        if (tip) button.dataset.imageTip = tip;
                        button.disabled = disabled;
                        button.addEventListener('click', () => onSelect(item));
                        container.append(button);
                    }
                } else {
                    container.removeAttribute('role');
                    buildImagePicker(container, items, onSelect, disabled);
                }
                if (hadFocus && !disabled) container.firstElementChild.focus({ preventScroll: true });
            }
            if (!inline) {
                const [button, menu] = container.children;
                const [, label, tip] = items.find(([item]) => item === value) || items[0];
                button.firstElementChild.textContent = label;
                if (tip) button.dataset.imageTip = tip;
                else delete button.dataset.imageTip;
                if (menu.hasAttribute('aria-label')) button.setAttribute('aria-label', menu.getAttribute('aria-label') + ' ' + label);
                for (const option of menu.children) {
                    option.setAttribute('aria-selected', String(option.dataset.value === value));
                }
                return;
            }
            for (const button of container.children) {
                const active = button.dataset.value === value;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', String(active));
            }
        }

        function setImageQuality(quality, persist = true) {
            const state = window.imageEditState;
            if (persist) state.preferredImageQuality = quality;
            const available = imageModelFeatures().quality;
            const levels = IMAGE_QUALITY_CHOICES.filter(([level]) => available.includes(level));
            state.quality = levels.some(([level]) => level === quality) ? quality : 'auto';
            const automatic = document.getElementById('imageQualityAuto');
            automatic.hidden = levels.length === 0;
            automatic.setAttribute('aria-pressed', String(state.quality === 'auto'));
            renderImageChoices('imageQualityChoices', levels.length ? levels : [
                ['auto', '自动', '上游决定该模型可用的渲染质量。']
            ], state.quality, setImageQuality, { segmented: true });
            updateImageGenerationControlSummary();
            if (persist) persistImageGenerationPreferences();
        }

        function setImageComplexity(complexity, persist = true) {
            const state = window.imageEditState;
            const supported = imageModelFeatures().complexity;
            document.getElementById('imageComplexitySection').hidden = !supported;
            if (!supported) {
                if (activeImagePicker?.id === 'imageComplexityChoices') closeImagePicker();
                state.complexity = 'auto';
                return;
            }
            const levels = [
                ['auto', '模型默认', '使用上游模型的默认规划深度。GPT Image 2.6 默认为高。'],
                ['low', '低', '适合换色、替换物件等范围明确的修改，通常更快。'],
                ['medium', '中', '适合有几项约束的构图或编辑，在规划深度和速度之间取平衡。'],
                ['high', '高', '适合密集文字、多主体关系和精确排版，规划耗时可能更长。']
            ];
            state.complexity = levels.some(([level]) => level === complexity) ? complexity : 'auto';
            renderImageChoices('imageComplexityChoices', levels, state.complexity, setImageComplexity);
            if (persist) {
                state.preferredImageComplexity = state.complexity;
                persistImageGenerationPreferences();
            }
        }

        function setImageOutputFormat(format, persist = true) {
            const state = window.imageEditState;
            const features = imageModelFeatures();
            const canGenerateSVG = features.svg && activeImageProfile()?.svg?.enabled === true && state.files.length === 0;
            const formats = IMAGE_FORMAT_CHOICES.filter(([item]) => {
                if (item === 'svg') return canGenerateSVG;
                if (item === 'gif') return features.animation;
                return true;
            });
            state.outputFormat = formats.some(([item]) => item === format) ? format : 'png';
            if (persist) state.preferredImageFormat = state.outputFormat;
            renderImageChoices('imageOutputChoices', formats, state.outputFormat, setImageOutputFormat);
            const gif = state.outputFormat === 'gif';
            const frames = document.getElementById('imageGifFrames');
            frames.inert = !gif;
            frames.classList.toggle('is-open', gif);
            setImageFrameCount(state.frameCount, false);
            document.getElementById('imageEditCompressionLabel').hidden = !['jpeg', 'webp'].includes(state.outputFormat);
            const supportsTransparency = ['png', 'webp'].includes(state.outputFormat);
            if (!supportsTransparency && state.transparency === 'transparent') state.transparency = 'auto';
            for (const button of document.querySelectorAll('#imageTransparencyButtons button')) {
                const active = button.dataset.transparency === state.transparency;
                button.disabled = button.dataset.transparency === 'transparent' && !supportsTransparency;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', String(active));
            }
            setImageGenerationCount(state.preferredImageCount ?? state.imageCount, false);
            if (persist) persistImageGenerationPreferences();
        }

        function setImageFrameCount(value, persist = true) {
            const counts = [4, 9, 12, 16];
            const state = window.imageEditState;
            state.frameCount = counts.includes(Number(value)) ? Number(value) : 9;
            renderImageChoices('imageFrameChoices', counts.map(count => [
                String(count), count + ' 帧',
                '每帧 200 毫秒，循环一轮 ' + count / 5 + ' 秒。更多帧可呈现更长的动作，通常增加时间和用量。'
            ]),
                String(state.frameCount), setImageFrameCount);
            if (persist) persistImageGenerationPreferences();
        }

        function setImageOutputCompression(value, persist = true) {
            const rounded = Number(value);
            const compression = value != null && value !== '' && Number.isInteger(rounded) && rounded >= 0 && rounded <= 100
                ? rounded : 75;
            window.imageEditState.outputCompression = compression;
            document.getElementById('imageEditCompression').value = compression;
            if (persist) persistImageGenerationPreferences();
        }

        const IMAGE_RESOLUTIONS = { sd: 1536, hd: 2560, uhd: 3840 };
        const IMAGE_PIXEL_LIMIT = 8294400;
        const STANDARD_IMAGE_SIZES = { '1:1': '1024x1024', '3:2': '1536x1024', '2:3': '1024x1536' };

        function supportsImageRatio(ratio, mode) {
            return ratio === 'auto' || mode === 'flexible' || (mode === 'fixed' && Object.hasOwn(STANDARD_IMAGE_SIZES, ratio));
        }

        function effectiveImageRatio(mode = imageModelFeatures().sizeMode) {
            const ratio = window.imageEditState.aspectRatio;
            return supportsImageRatio(ratio, mode) ? ratio : 'auto';
        }

        function imageRequestSize(ratio, resolution, mode = imageModelFeatures().sizeMode) {
            if (ratio === 'auto') return 'auto';
            if (mode === 'fixed') return STANDARD_IMAGE_SIZES[ratio] || 'auto';
            if (mode !== 'flexible') return 'auto';
            const [width, height] = ratio.split(':').map(Number);
            const longRatio = Math.max(width, height);
            const shortRatio = Math.min(width, height);
            let long = IMAGE_RESOLUTIONS[resolution] || IMAGE_RESOLUTIONS.sd;
            let short = Math.round(long * shortRatio / longRatio / 16) * 16;
            while (long * short > IMAGE_PIXEL_LIMIT) {
                long -= 16;
                short = Math.round(long * shortRatio / longRatio / 16) * 16;
            }
            const dimensions = width >= height ? [long, short] : [short, long];
            return dimensions.join('x');
        }

        function setImageResolution(resolution, persist = true) {
            if (!IMAGE_RESOLUTIONS[resolution]) return;
            window.imageEditState.resolution = resolution;
            renderImageChoices('imageResolutionChoices', [['sd', '标清', '最长边 1536 像素'],
                ['hd', '高清', '最长边目标 2560 像素，适合需要保留更多细节的图片。'],
                ['uhd', '超清', '最长边目标 3840 像素，会按总像素上限调整。2.5 系列的超高分辨率为实验支持。']],
                resolution, setImageResolution);
            updateImageGenerationControlSummary();
            if (persist) persistImageGenerationPreferences();
        }

        function setImageAspectRatio(ratio, persist = true) {
            if (!IMAGE_SIZE_PRESETS[ratio]) return;
            window.imageEditState.aspectRatio = ratio;
            updateImageGenerationControlSummary();
            if (persist) persistImageGenerationPreferences();
        }

        function syncImageRatioOptions(mode, ratio) {
            document.getElementById('imageResolutionSection').hidden = mode !== 'flexible';
            const group = document.getElementById('imageAspectRatioSection');
            group.hidden = mode === 'auto';
            group.classList.toggle('uses-standard-sizes', mode === 'fixed');
            document.querySelectorAll('#imageAspectRatioButtons button').forEach(button => {
                const active = button.dataset.ratio === ratio;
                button.hidden = !supportsImageRatio(button.dataset.ratio, mode);
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', String(active));
            });
        }

        function setImageTransparency(transparency, persist = true) {
            if (!['auto', 'opaque', 'transparent'].includes(transparency)) return;
            window.imageEditState.transparency = transparency;
            setImageOutputFormat(window.imageEditState.preferredImageFormat, false);
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
            const state = window.imageEditState;
            const profile = activeImageProfile();
            const models = availableImageModels(profile);
            const fallback = profile?.defaultImage || 'gpt-image-2';
            if (!models.includes(model)) model = models.includes(fallback) ? fallback : models[0];
            state.imageModel = model;
            if (persist) state.preferredImageModel = model;
            const label = IMAGE_MODEL_LABELS[model];
            renderImageChoices('imageModelChoices', models.map(item => [
                item, IMAGE_MODEL_LABELS[item] || item, imageModelTip(item)
            ]), model, setImageModel);
            if (!state.editSession) {
                document.getElementById('imageEditHeading').textContent = label ? 'GPT Image ' + label + ' 生图' : model + ' 生图';
            }
            setImageQuality(state.preferredImageQuality ?? state.quality, false);
            setImageComplexity(state.preferredImageComplexity, false);
            setImageOutputFormat(state.preferredImageFormat, false);
            if (persist) persistImageGenerationPreferences();
        }

        function setImageGenerationCount(count, persist = true) {
            const state = window.imageEditState;
            const desired = Math.max(1, Math.min(9, Math.trunc(Number(count)) || 1));
            state.preferredImageCount = desired;
            const limit = state.outputFormat === 'svg' ? 1 : 9;
            state.imageCount = Math.min(desired, limit);
            const counts = Array.from({ length: limit }, (_, index) => {
                const count = index + 1;
                return [String(count), `${count} 张`, limit === 1 ? 'SVG 每次生成一张矢量图。'
                    : `为同一个提示词生成 ${count} 张候选图片，数量越多通常用量越高。`];
            });
            renderImageChoices('imageCountChoices', counts, String(state.imageCount), setImageGenerationCount, { disabled: limit === 1 });
            updateImageGenerationControlSummary();
            if (persist) persistImageGenerationPreferences();
        }

        function updateImageGenerationControlSummary() {
            const mode = imageModelFeatures().sizeMode;
            const ratio = effectiveImageRatio(mode);
            syncImageRatioOptions(mode, ratio);
            const qualityLabels = { auto: '自动质量', low: '低质量', medium: '中质量', high: '高质量', xhigh: '超高质量', max: '最高质量' };
            const resolution = window.imageEditState.resolution || 'sd';
            const size = imageRequestSize(ratio, resolution, mode);
            document.getElementById('imageEditSize').value = size;
            const resolutionLabel = document.getElementById('imageResolutionLabel');
            resolutionLabel.dataset.imageTip = size === 'auto'
                ? '最长边控制输出的像素档位。智能比例由模型决定尺寸，选择固定比例后可应用标清、高清或超清。'
                : '预计输出 ' + size.replace('x', ' × ') + (Math.max(...size.split('x').map(Number)) < IMAGE_RESOLUTIONS[resolution]
                    ? ' 像素，已按模型的像素上限调整。' : ' 像素。分辨率越高通常用量越大。');
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
