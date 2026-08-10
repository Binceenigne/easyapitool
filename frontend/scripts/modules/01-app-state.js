        // 统一基准系统时间。
        window.ctxTime = new Date();

        window.appState = {
            userId: null,
            keys: [],
            activeKeyId: null,
            firebaseInitialized: false,
            // 默认警报阈值配置
            thresholds: {
                warn: 50,
                danger: 25,
                critical: 10
            },
            // 自适应后台刷新参数
            refreshCounter: 60,
            refreshIntervals: { foreground: 60, background: 300 },
            rateLimitProgressMode: 'remaining',
            appVersion: '',
            closeAction: 'ask',
            alwaysOnTop: false,
            backgroundUiMode: 'delayed',
            titleBarMode: 'default',
            activeTitleBarMode: 'default',
            startupEnabled: false,
            update: { status: 'idle', percent: 0, message: '尚未检查更新', releaseNotes: '' },
            showFullChangelog: false,
            isTabActive: true,
            averagePeriod: 'today',
            trendPeriod: '1h'
        };

        window.imageEditState = {
            files: [],
            resultSets: [],
            viewerResult: null,
            viewer: { setId: '', itemIndex: -1, scale: 1, x: 0, y: 0, dragging: false },
            editSession: null,
            draftBeforeSession: null,
            pendingDeleteSetId: '',
            pendingDeleteSetIds: [],
            selectedSetIds: new Set(),
            aspectRatio: 'auto',
            quality: 'auto',
            outputPreset: 'lossless',
            imageCount: 1,
            reasoningMode: 'instant',
            webSearchEnabled: true,
            polishing: false,
            busy: false,
            mode: 'image-edit'
        };

        const IMAGE_SIZE_PRESETS = {
            auto: 'auto', '9:16': '720x1280', '2:3': '768x1152', '3:4': '768x1024',
            '1:1': '1024x1024', '4:3': '1024x768', '3:2': '1152x768', '16:9': '1280x720', '21:9': '1344x576'
        };
        const IMAGE_GENERATION_PREFERENCES_KEY = 'api-tools-image-generation-preferences-v1';
