        function initializePage() {
            if (window.__pageInitialized) return;
            window.__pageInitialized = true;

            restorePageZoom();
            runAuthAndInitialize();
            void loadImageGenerationHistory();
            initializeImageViewerInteractions();
            initializeEditImageScrollbar();
            initializeImageReasoningSlider();
            initializeImageAdvancedReasoningMatrix();
            initializeImageReasoningHoverPlayback();
            restoreImageGenerationPreferences();
            window.setInterval(updateReasoningTimers, 1000);

            window.addEventListener('pywebviewready', () => {
                if (window.__pendingNativeTheme) {
                    window.pywebview?.api?.set_window_background(window.__pendingNativeTheme);
                }
                void loadImageGenerationHistory();
            }, { once: true });

            const beginWindowDrag = event => {
                if (event.button !== 0 || event.target.closest('button, input, select, textarea, .window-controls')) return;
                event.preventDefault();
                const dragRequest = window.pywebview?.api?.native_drag('move');
                if (dragRequest && typeof dragRequest.then === 'function') {
                    dragRequest.then(result => {
                        if (result?.ok) window.applyWindowState(result.maximized);
                    }).catch(error => console.error('窗口拖动失败:', error));
                }
            };
            document.getElementById('windowTitleBar')?.addEventListener('mousedown', beginWindowDrag);
            document.getElementById('settingsHeader')?.addEventListener('mousedown', beginWindowDrag);

            document.querySelectorAll('[data-resize]').forEach(handle => {
                handle.addEventListener('mousedown', event => {
                    if (event.button !== 0) return;
                    event.preventDefault();
                    event.stopPropagation();
                    window.pywebview?.api?.native_drag(handle.dataset.resize);
                });
            });

            document.getElementById('thWarn').value = window.appState.thresholds.warn;
            document.getElementById('thDanger').value = window.appState.thresholds.danger;
            document.getElementById('thCritical').value = window.appState.thresholds.critical;
            updateRateLimitModeButtons();
            renderLucideIcons();

            const promptTextarea = document.getElementById('imageEditPrompt');
            const modalPromptTextarea = document.getElementById('imagePromptModalTextarea');
            promptTextarea.addEventListener('input', resizeImagePrompt);
            modalPromptTextarea.addEventListener('input', () => {
                promptTextarea.value = modalPromptTextarea.value;
            });
            document.addEventListener('paste', event => {
                const files = [...(event.clipboardData?.items || [])]
                    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
                    .map(item => item.getAsFile())
                    .filter(Boolean);
                if (files.length) {
                    event.preventDefault();
                    void importReferenceFiles(files);
                }
            });
            const referenceDropTargets = [
                document.getElementById('imageEditWorkspace'),
                document.getElementById('imagePromptModal')
            ];
            referenceDropTargets.forEach(target => {
                target.addEventListener('dragover', event => {
                    if ([...(event.dataTransfer?.items || [])].some(item => item.type.startsWith('image/'))) {
                        event.preventDefault();
                        event.dataTransfer.dropEffect = 'copy';
                        target.classList.add('is-dragging-image');
                    }
                });
                target.addEventListener('dragleave', () => target.classList.remove('is-dragging-image'));
                target.addEventListener('drop', event => {
                    target.classList.remove('is-dragging-image');
                    const files = [...(event.dataTransfer?.files || [])].filter(file => file.type.startsWith('image/'));
                    if (files.length) {
                        event.preventDefault();
                        void importReferenceFiles(files);
                    }
                });
            });
            document.getElementById('editImageSelection').addEventListener('click', event => {
                if (event.target === event.currentTarget && window.imageEditState.files.length === 0) chooseEditImages();
            });
            resizeImagePrompt();

            document.addEventListener('keydown', event => {
                handlePageZoomShortcut(event);
                if (event.key === 'Escape') {
                    closeKeySwitcher();
                    closeModelModal();
                    closeUpdateModal();
                    closeImagePromptModal();
                    closeImageResultModal();
                    closeImageSetDeleteModal();
                    closeImageGenerationStopModal();
                    closeImageReasoningMenu();
                }
                handleDevToolsSequence(event);
            });
            document.addEventListener('pointerdown', event => {
                if (!event.target.closest('#keySwitcher, #keySwitcherMenu')) closeKeySwitcher();
                if (!event.target.closest('#imageGenerationControlPanel, #imageGenerationControlsButton')) {
                    closeImageGenerationControls();
                }
                if (!event.target.closest('#imageReasoningControl, #imageReasoningMenu')) {
                    closeImageReasoningMenu();
                }
            });

            // 实时倒计时
            setInterval(updateClockCountdowns, 1000);
            setInterval(syncVisibleBackendState, 5000);
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') void syncVisibleBackendState();
            });

            // 响应式高宽监测
            window.addEventListener('resize', () => {
                handleResponsiveLayout();
                positionKeySwitcherMenu();
                positionImageGenerationPanel();
                positionImageReasoningMenu();
                resizeImagePrompt();
            }, { passive: true });
            if ('ResizeObserver' in window) {
                const rootResizeObserver = new ResizeObserver(handleResponsiveLayout);
                rootResizeObserver.observe(document.getElementById('widget-root'));
                window.__rootResizeObserver = rootResizeObserver;
                initializeProgressResizeObserver();
            }
            handleResponsiveLayout();

            updateRefreshBadge();
            reportFrontendStartup('frontend_interactive');
        }
