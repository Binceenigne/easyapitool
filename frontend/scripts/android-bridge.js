(function initializeAndroidBridge() {
    const capacitor = window.Capacitor;
    const native = capacitor?.Plugins?.AndroidStorage;
    const isAndroid = Boolean(native) && (
        capacitor?.getPlatform?.() === 'android' ||
        capacitor?.getPlatform?.() === undefined
    );
    if (!isAndroid || window.pywebview?.api) return;

    document.documentElement.dataset.platform = 'android';
    const DEFAULT_ANDROID_SERVICE_URL = 'https://39.105.74.222/v1';
    const GATEWAY_API_URL = 'https://39.105.74.222/openai/v1';
    const EVENT_CURSOR_KEY = 'api-tools-android-event-cursor';
    const EVENT_EPOCH_KEY = 'api-tools-android-event-epoch';
    const IMAGE_SETS_KEY = 'image-sets';
    const IMAGE_HISTORY_SCHEMA_KEY = 'image-history-schema';
    const IMAGE_HISTORY_SCHEMA_VERSION = 2;
    const localPreferences = {
        get(key, fallback) {
            try {
                const value = localStorage.getItem(`api-tools-android-${key}`);
                return value === null ? fallback : JSON.parse(value);
            } catch {
                return fallback;
            }
        },
        set(key, value) {
            try {
                localStorage.setItem(`api-tools-android-${key}`, JSON.stringify(value));
                return true;
            } catch {
                return false;
            }
        }
    };
    const eventQueue = [];
    const pendingTasks = new Map();
    const directTasks = new Map();
    const directKeyStates = new Map();
    const downloadCache = new Map();
    let eventStreamPromise = null;
    let eventStreamGeneration = 0;
    let eventStreamAbortController = null;
    let appUpdateState = { status: 'idle', percent: 0, message: '尚未检查服务器更新' };
    let updateCheck = null;
    let serviceConfig = {
        baseUrl: currentServiceUrl(window.__EASYAPITOOL_SERVICE_URL__ || DEFAULT_ANDROID_SERVICE_URL),
        bearerToken: String(window.__EASYAPITOOL_SERVICE_TOKEN__ || '').trim()
    };
    let serviceConfigLoad = null;
    let serviceConfigError = '';
    let deviceRegistrationPromise = null;
    const keyRegistrationPromises = new Map();

    function migrateLocalImageHistory() {
        const currentVersion = Number(localPreferences.get(IMAGE_HISTORY_SCHEMA_KEY, 0));
        if (currentVersion >= IMAGE_HISTORY_SCHEMA_VERSION) return;
        const storedRecords = localPreferences.get(IMAGE_SETS_KEY, []);
        const records = Array.isArray(storedRecords) ? storedRecords : [];
        const migrated = localPreferences.set(
            IMAGE_SETS_KEY,
            records.filter(record => record?.status !== 'failed')
        );
        if (migrated) {
            localPreferences.set(IMAGE_HISTORY_SCHEMA_KEY, IMAGE_HISTORY_SCHEMA_VERSION);
        }
    }

    migrateLocalImageHistory();

    function isDeviceToken(token) {
        return /^device-v1\./.test(String(token || '').trim());
    }

    function currentServiceUrl(value) {
        const url = String(value || '').trim().replace(/\/+$/, '');
        return /^https:\/\/imggen\.djyx\.(?:me|pw)\/v1$/i.test(url) ? DEFAULT_ANDROID_SERVICE_URL : url;
    }

    async function loadServiceConfig() {
        if (serviceConfig.baseUrl && isDeviceToken(serviceConfig.bearerToken)) {
            serviceConfigError = '';
            return serviceConfig;
        }
        if (serviceConfigLoad) return serviceConfigLoad;
        serviceConfigLoad = pluginCall('readServiceConfig')
            .then(config => {
                const savedBaseUrl = currentServiceUrl(config.serviceUrl);
                const savedBearerToken = String(config.bearerToken || '').trim();
                if (/^https:\/\/clife\.djyx\.(?:me|pw)(?:\/|$)/i.test(savedBaseUrl)) {
                    void pluginCall('clearServiceConfig').catch(() => {});
                    serviceConfig = { baseUrl: DEFAULT_ANDROID_SERVICE_URL, bearerToken: '' };
                    serviceConfigError = '';
                    return serviceConfig;
                }
                if (savedBearerToken && !isDeviceToken(savedBearerToken)) {
                    void pluginCall('clearServiceConfig').catch(() => {});
                    serviceConfig = { baseUrl: DEFAULT_ANDROID_SERVICE_URL, bearerToken: '' };
                    serviceConfigError = '';
                    return serviceConfig;
                }
                serviceConfig = {
                    baseUrl: savedBaseUrl || DEFAULT_ANDROID_SERVICE_URL,
                    bearerToken: savedBearerToken
                };
                if (savedBearerToken && savedBaseUrl && savedBaseUrl !== config.serviceUrl) {
                    void pluginCall('saveServiceConfig', {
                        serviceUrl: savedBaseUrl, bearerToken: savedBearerToken
                    }).catch(() => {});
                }
                serviceConfigError = '';
                return serviceConfig;
            })
            .catch(error => {
                serviceConfig = { baseUrl: DEFAULT_ANDROID_SERVICE_URL, bearerToken: '' };
                serviceConfigError = error?.message || '无法读取设备连接配置';
                return serviceConfig;
            })
            .finally(() => { serviceConfigLoad = null; });
        return serviceConfigLoad;
    }

    function setServiceStatus(message, isError = false) {
        const status = document.getElementById('androidServiceStatus');
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('color-critical', isError);
        status.classList.toggle('color-good', !isError && message === '已安全保存设备连接');
    }

    async function hydrateServiceForm() {
        const config = await loadServiceConfig();
        setServiceStatus(
            config.baseUrl && config.bearerToken
                ? '设备连接已自动配置'
                : serviceConfigError
                    ? serviceConfigError
                    : '添加 API Key 后将自动配置设备连接',
            Boolean(serviceConfigError)
        );
    }

    function serviceSettings() {
        return {
            baseUrl: serviceConfig.baseUrl || currentServiceUrl(localPreferences.get('service-url', DEFAULT_ANDROID_SERVICE_URL)),
            bearerToken: serviceConfig.bearerToken
        };
    }

    function publicServiceUrl(path, query = '') {
        return `${DEFAULT_ANDROID_SERVICE_URL}/${String(path || '').replace(/^\//, '')}${query}`;
    }

    async function getAppUpdateInfo() {
        return pluginCall('getAppUpdateInfo');
    }

    async function checkAndroidUpdate() {
        const appInfo = await getAppUpdateInfo();
        const params = new URLSearchParams({
            platform: 'android',
            currentVersionCode: String(appInfo.versionCode || 0),
            currentBuildTimestampMs: String(appInfo.buildTimestampMs || 0)
        });
        const response = await fetch(publicServiceUrl('/app/update', `?${params}`), {
            headers: { Accept: 'application/json' },
            cache: 'no-store',
            signal: AbortSignal.timeout?.(12000)
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
        return {
            status: body.available ? 'available' : 'current',
            percent: 100,
            message: body.available ? `发现新版本 v${body.latestVersion}` : '当前已是最新版本',
            latestVersion: body.latestVersion,
            available: body.available === true,
            showPrompt: body.available === true,
            releaseNotes: body.releaseNotes || '',
            fullReleaseNotes: body.releaseNotes || '',
            release: {
                version: body.latestVersion,
                versionCode: body.latestVersionCode,
                buildTimestampMs: body.latestBuildTimestampMs,
                downloadSize: body.downloadSize,
                sha256: body.sha256,
                downloadUrl: publicServiceUrl('/app/update/download?platform=android')
            }
        };
    }

    function latestAndroidUpdate() {
        if (!updateCheck) updateCheck = checkAndroidUpdate().finally(() => { updateCheck = null; });
        return updateCheck;
    }

    function requireService() {
        const settings = serviceSettings();
        if (!settings.baseUrl) throw new Error('尚未配置 API_TOOLS 服务地址');
        if (!settings.bearerToken) {
            const detail = serviceConfigError ? `：${serviceConfigError}` : '';
            throw new Error(`设备连接尚未自动配置${detail}。请先添加有效的 API Key`);
        }
        return settings;
    }

    function newRequestId(prefix = 'android-image') {
        return globalThis.crypto?.randomUUID?.()
            || `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function localImageUrl(path) {
        const source = String(path || '').trim();
        if (!source) return '';
        const uri = source.startsWith('file:') ? source : `file://${source}`;
        return capacitor.convertFileSrc?.(uri) || '';
    }

    function resolveUrl(path) {
        const settings = requireService();
        const baseUrl = settings.baseUrl.replace(/\/$/, '');
        const parsed = new URL(baseUrl);
        let cleanPath = `/${String(path || '').replace(/^\//, '')}`;
        if (parsed.pathname.replace(/\/$/, '').endsWith('/v1') && cleanPath.startsWith('/api/v1/')) {
            cleanPath = cleanPath.slice('/api/v1'.length);
        }
        const resolved = new URL(/^https?:\/\//i.test(String(path || '')) ? path : `${baseUrl}${cleanPath}`);
        if (resolved.origin !== parsed.origin) throw new Error('资源地址与设备网关不一致');
        return resolved.href;
    }

    async function requestJson(path, options = {}, retryAuthentication = true) {
        const settings = requireService();
        const response = await fetch(resolveUrl(path), {
            ...options,
            redirect: 'error',
            headers: {
                Accept: 'application/json',
                Authorization: `Bearer ${settings.bearerToken}`,
                ...(options.headers || {})
            }
        });
        const body = await response.json().catch(() => ({}));
        if (response.status === 401 && retryAuthentication) {
            serviceConfig.bearerToken = '';
            await pluginCall('clearServiceConfig').catch(() => {});
            await ensureDeviceRegistration();
            return requestJson(path, options, false);
        }
        if (!response.ok) {
            throw new Error(body.detail || body.error || `HTTP ${response.status}`);
        }
        return body;
    }

    async function pluginCall(method, options = {}) {
        if (!native?.[method]) throw new Error(`Android 原生能力不可用：${method}`);
        return native[method](options);
    }

    async function secureKeyMetadata() {
        const result = await pluginCall('listSecureKeys');
        return Array.isArray(result?.keys) ? result.keys : [];
    }

    async function readSecureKey(keyId) {
        return pluginCall('readSecureKey', { keyId: String(keyId) });
    }

    function usesDirectApi(key) {
        const base = String(key?.baseUrl || '').toLowerCase();
        return base !== '' && base !== GATEWAY_API_URL;
    }

    async function directKeyState(key, force = false) {
        const previous = directKeyStates.get(key.id);
        if (!force && previous && Date.now() - previous.cachedAt < 60_000) return previous;
        let state;
        try {
            const result = await pluginCall('requestDirectApi', { keyId: key.id, operation: 'models' });
            const models = [...new Set((result.data || []).map(item => typeof item === 'string' ? item : item?.id).filter(Boolean))];
            state = { status: 'active', models, modelsCount: models.length };
        } catch (error) {
            state = { status: 'unknown', models: previous?.models || [], lastError: error.message };
        }
        if (key.distributed) {
            try {
                const usage = await pluginCall('requestDirectApi', { keyId: key.id, operation: 'usage' });
                state = { ...state, appModels: usage.appModels, quota: usage.quota, windows: usage.windows };
            } catch (error) { state = { ...state, appModels: previous?.appModels, lastError: error.message }; }
        }
        state = { ...state, directApi: true, mode: 'official', serverQuota: key.distributed === true, hasPricedUsage: key.distributed === true, cachedAt: Date.now() };
        directKeyStates.set(key.id, state);
        return state;
    }

    async function ensureDeviceRegistration(preferredKeyId = '') {
        await loadServiceConfig();
        if (serviceConfig.bearerToken) return serviceConfig;
        if (deviceRegistrationPromise) return deviceRegistrationPromise;
        deviceRegistrationPromise = (async () => {
            const metadata = (await secureKeyMetadata()).filter(key => !usesDirectApi(key));
            const selected = metadata.find(key => String(key.id) === String(preferredKeyId)) || metadata[0];
            if (!selected) throw new Error('请先添加有效的 API Key');
            const [secret, device] = await Promise.all([
                readSecureKey(selected.id),
                pluginCall('getOrCreateDeviceId')
            ]);
            const response = await fetch(`${DEFAULT_ANDROID_SERVICE_URL}/device/register`, {
                method: 'POST',
                headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    deviceId: String(device.deviceId || ''),
                    keyId: String(selected.id),
                    name: secret.name || selected.name || 'API Key',
                    value: secret.value
                })
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok || !body.bearerToken) {
                throw new Error(body.detail || '设备连接自动配置失败');
            }
            await pluginCall('saveServiceConfig', {
                serviceUrl: DEFAULT_ANDROID_SERVICE_URL,
                bearerToken: body.bearerToken
            });
            serviceConfig = {
                baseUrl: DEFAULT_ANDROID_SERVICE_URL,
                bearerToken: String(body.bearerToken)
            };
            serviceConfigError = '';
            setServiceStatus('设备连接已自动配置');
            return serviceConfig;
        })().catch(error => {
            serviceConfigError = error?.message || '设备连接自动配置失败';
            setServiceStatus(serviceConfigError, true);
            throw error;
        }).finally(() => { deviceRegistrationPromise = null; });
        return deviceRegistrationPromise;
    }

    async function registerKey(metadata) {
        const keyId = String(metadata.id || '');
        const existing = keyRegistrationPromises.get(keyId);
        if (existing) return existing;
        const registration = (async () => {
            await ensureDeviceRegistration(keyId);
            const secret = await readSecureKey(keyId);
            return requestJson('/api/v1/keys', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    keyId,
                    name: secret.name || metadata.name || 'API Key',
                    value: secret.value
                })
            });
        })();
        keyRegistrationPromises.set(keyId, registration);
        void registration.finally(() => {
            if (keyRegistrationPromises.get(keyId) === registration) keyRegistrationPromises.delete(keyId);
        }).catch(() => {});
        return registration;
    }

    async function registerAllKeys() {
        const metadata = (await secureKeyMetadata()).filter(key => !usesDirectApi(key));
        const errors = [];
        for (const key of metadata) {
            try {
                await registerKey(key);
            } catch (error) {
                errors.push(error);
            }
        }
        return { metadata, errors };
    }

    function maskedKeyValue() {
        return '已安全保存';
    }

    async function getState(forceDirectRefresh = false) {
        await loadServiceConfig();
        const metadata = await secureKeyMetadata();
        const managed = metadata.filter(key => !usesDirectApi(key));
        const direct = metadata.filter(usesDirectApi);
        const directStates = new Map(await Promise.all(direct.map(async key => [key.id, await directKeyState(key, forceDirectRefresh)])));
        let remote = {};
        let registrationErrors = [];
        if (managed.length) {
            try {
                await ensureDeviceRegistration(managed[0].id);
                ({ errors: registrationErrors } = await registerAllKeys());
                remote = await requestJson('/api/v1/state');
            } catch (error) {
                registrationErrors = [error];
            }
        }
        const remoteKeys = new Map((remote.keys || []).map(key => [String(key.id), key]));
        return {
            ...remote,
            keys: metadata.map(key => ({
                ...remoteKeys.get(String(key.id)),
                id: String(key.id),
                name: key.name || 'API Key',
                value: maskedKeyValue(),
                status: remoteKeys.get(String(key.id))?.status || 'unknown',
                lastError: remoteKeys.get(String(key.id))?.lastError || registrationErrors[0]?.message || null,
                ...directStates.get(key.id),
            })),
            appName: 'Mirra(觅然)',
            appVersion: (await getAppUpdateInfo().catch(() => ({ versionName: 'android' }))).versionName || 'android',
            storageMode: 'android-filesystem-sqlite-index',
            serverPersistence: false,
            usagePersistence: remote.usagePersistence || null,
            closeAction: 'exit',
            alwaysOnTop: false,
            backgroundUiMode: 'active',
            titleBarMode: 'default',
            activeTitleBarMode: 'default',
            startupEnabled: false,
            isForeground: document.visibilityState !== 'hidden',
            nextRefreshSeconds: 60,
            update: appUpdateState,
            refreshIntervals: localPreferences.get('refresh-intervals', { foreground: 60, background: 300 }),
            thresholds: localPreferences.get('thresholds', { warn: 50, danger: 25, critical: 10 }),
            rateLimitProgressMode: localPreferences.get('rate-mode', 'remaining')
        };
    }

    async function readAsDataUrl(path) {
        const result = await pluginCall('readAsset', { path });
        if (!result?.ok) throw new Error(result?.error || '图片读取失败');
        return result.dataUrl;
    }

    function persistLocalSet(result, pending = null) {
        const requestId = String(result.requestId || pending?.requestId || '');
        const setId = String(result.setId || pending?.setId || requestId);
        if (!setId) return;
        const sessionId = String(result.sessionId || pending?.sessionId || requestId);
        const record = {
            setId,
            sessionId,
            parentSetId: String(result.parentSetId || pending?.parentSetId || ''),
            roundNumber: Number(result.roundNumber || pending?.roundNumber || 1),
            requestId,
            prompt: String(result.prompt || pending?.prompt || ''),
            originalPrompt: String(result.originalPrompt || pending?.prompt || ''),
            imageModel: result.imageModel || pending?.imageModel || 'gpt-image-2',
            aspectRatio: result.aspectRatio || pending?.aspectRatio || 'auto',
            transparency: ['auto', 'opaque', 'transparent'].includes(result.transparency)
                ? result.transparency
                : ['auto', 'opaque', 'transparent'].includes(pending?.transparency)
                    ? pending.transparency
                    : 'auto',
            createdAt: result.createdAt || pending?.createdAt || new Date().toISOString(),
            status: result.cancelled ? 'cancelled' : result.ok ? 'completed' : 'failed',
            error: String(result.error || ''),
            errorDetails: result.errorDetails || null,
            reasoningMode: result.reasoningMode || 'instant',
            reasoningModel: result.reasoningModel || '',
            reasoningEffort: result.reasoningEffort || '',
            reasoningSummary: result.reasoningSummary || '',
            reasoningStatus: result.reasoningStatus || 'idle',
            reasoningUsage: result.reasoningUsage || null,
            reasoningStats: result.reasoningStats || null,
            reasoningTurns: Array.isArray(result.reasoningTurns) ? result.reasoningTurns : [],
            workspaceId: result.workspaceId || sessionId,
            operation: result.operation || result.continuationPlan?.operation || '',
            continuationPlan: result.continuationPlan || null,
            inputAssetIds: result.inputAssetIds || [],
            webSearchEnabled: result.webSearchEnabled === true,
            webSearchUsed: result.webSearchUsed === true,
            webSearchSources: Array.isArray(result.webSearchSources) ? result.webSearchSources : [],
            webReferenceCount: Array.isArray(result.webReferences) ? result.webReferences.length : 0,
            webReferences: (result.webReferences || []).map(({ previewDataUrl, ...reference }) => reference),
            webInspectedReferences: (result.webInspectedReferences || []).map(({ previewDataUrl, ...reference }) => reference),
            webReferenceFailures: result.webReferenceFailures || [],
            webSelectionRationale: result.webSelectionRationale || '',
            webImageCandidateCount: Number(result.webImageCandidateCount) || 0,
            requestedCount: Number(result.requestedCount || pending?.requestedCount || 1),
            items: (result.items || []).map((item, itemIndex) => ({
                itemIndex: Number(item.itemIndex ?? itemIndex),
                ok: item.ok === true,
                status: item.ok === true ? 'completed' : item.status || 'failed',
                error: item.error || '',
                errorDetails: item.errorDetails || null,
                resourceId: String(item.resourceId || ''),
                previewResourceId: String(item.previewResourceId || ''),
                width: Number(item.width) || 0,
                height: Number(item.height) || 0,
                format: item.format || ''
            }))
        };
        const records = localPreferences.get(IMAGE_SETS_KEY, []).filter(item => item?.setId !== setId);
        records.unshift(record);
        const retained = records.slice(0, 100);
        while (!localPreferences.set(IMAGE_SETS_KEY, retained) && retained.length > 1) retained.pop();
    }

    async function listLocalImageSets() {
        const records = localPreferences.get(IMAGE_SETS_KEY, []);
        const assets = await pluginCall('listAssets');
        const assetsById = new Map((assets.assets || []).map(asset => [String(asset.resourceId), asset]));
        return records.map(record => ({
            ...record,
            history: true,
            webReferences: (record.webReferences || []).map(reference => {
                const asset = assetsById.get(String(reference.resourceId));
                const path = asset?.path || reference.path || '';
                const previewPath = asset?.previewPath || reference.previewPath || path;
                return { ...reference, path, previewPath, uri: localImageUrl(path), previewUri: localImageUrl(previewPath) };
            }),
            items: (record.items || []).map(item => {
                const asset = assetsById.get(String(item.resourceId));
                const path = asset?.path || '';
                const previewPath = asset?.previewPath || path;
                const previewUri = localImageUrl(previewPath);
                return {
                    ...item,
                    path,
                    uri: localImageUrl(path),
                    previewPath,
                    previewUri,
                    result: item.ok && path ? {
                        ...item,
                        path,
                        uri: localImageUrl(path),
                        previewUri,
                        fullUri: localImageUrl(path)
                    } : null
                };
            })
        }));
    }

    async function hydrateAsset(value, context = {}) {
        if (Array.isArray(value)) return Promise.all(value.map(item => hydrateAsset(item, context)));
        if (!value || typeof value !== 'object') return value;
        const result = { ...value };
        if (result.resourceId && result.downloadUrl) {
            const cacheKey = `${result.resourceId}:full`;
            let downloaded = downloadCache.get(cacheKey);
            if (!downloaded) {
                downloaded = pluginCall('downloadAsset', {
                    endpoint: resolveUrl(result.downloadUrl),
                    bearerToken: serviceSettings().bearerToken,
                    resourceId: result.resourceId,
                    sessionId: context.sessionId || result.sessionId || '',
                    setId: context.setId || result.setId || '',
                    mimeType: result.mimeType || result.contentType || 'image/png',
                    preview: false
                }).catch(error => { downloadCache.delete(cacheKey); throw error; });
                downloadCache.set(cacheKey, downloaded);
            }
            const local = await downloaded;
            result.path = local.path;
            result.uri = localImageUrl(local.path);
        }
        if (result.previewResourceId && result.previewUrl) {
            const cacheKey = `${result.previewResourceId}:preview`;
            let downloaded = downloadCache.get(cacheKey);
            if (!downloaded) {
                downloaded = pluginCall('downloadAsset', {
                    endpoint: resolveUrl(result.previewUrl),
                    bearerToken: serviceSettings().bearerToken,
                    resourceId: result.resourceId || result.previewResourceId,
                    sessionId: context.sessionId || result.sessionId || '',
                    setId: context.setId || result.setId || '',
                    mimeType: result.previewMimeType || 'image/jpeg',
                    preview: true
                }).catch(error => { downloadCache.delete(cacheKey); throw error; });
                downloadCache.set(cacheKey, downloaded);
            }
            const local = await downloaded;
            result.previewPath = local.path;
            result.previewUri = localImageUrl(local.path);
        }
        for (const [key, item] of Object.entries(result)) {
            if (!['path', 'uri', 'previewPath', 'previewUri', 'downloadUrl', 'previewUrl'].includes(key)) {
                result[key] = key === 'webReferences' && Array.isArray(item)
                    ? await Promise.all(item.map(reference => hydrateAsset(reference, context).catch(() => ({ ...reference, unavailable: true }))))
                    : await hydrateAsset(item, context);
            }
        }
        return result;
    }

    async function handleServerEvent(event) {
        if (!event || typeof event !== 'object') return;
        if (event.type === 'quota_changed' || event.type === 'models_changed') {
            window.applyBackendState?.(await getState());
            return;
        }
        if (event.type === 'task_result' && event.quota) {
            getState().then(state => window.applyBackendState?.(state)).catch(() => {});
        }
        const requestId = String(event.requestId || event.result?.requestId || '');
        const pending = pendingTasks.get(requestId);
        if (requestId && !pending) return;
        const terminal = event.type === 'task_result' || event.type === 'task_failed';
        if (terminal && pending?.processingTerminal) return;
        if (terminal && pending) pending.processingTerminal = true;
        try {
        if (event.type === 'task_result') {
            const result = await hydrateAsset(event.result || {}, {
                sessionId: event.sessionId,
                setId: event.setId || event.requestId
            });
            result.requestId = requestId;
            result.setId = pending?.setId || result.setId || requestId;
            result.sessionId = pending?.sessionId || result.sessionId || requestId;
            result.parentSetId = pending?.parentSetId || result.parentSetId || '';
            result.roundNumber = pending?.roundNumber || result.roundNumber || 1;
            persistLocalSet(result, pending);
            const eventType = result.cancelled ? 'set_cancelled' : 'set_completed';
            window.applyImageGenerationEvent?.({
                ...result,
                type: eventType,
                requestId,
                setId: result.setId,
                sessionId: result.sessionId
            });
            if (pending) {
                pendingTasks.delete(requestId);
                pending.resolve(result);
            }
            return;
        }
        if (event.type === 'task_failed') {
            const failed = persistFailedGeneration(
                {
                    requestId,
                    sessionId: pending?.sessionId || requestId,
                    parentSetId: pending?.parentSetId || '',
                    roundNumber: pending?.roundNumber || 1,
                    imageCount: pending?.requestedCount || 1
                },
                pending?.prompt || '',
                Object.assign(new Error(event.error || '图片生成失败'), { errorDetails: event.errorDetails }),
                pending
            );
            window.applyImageGenerationEvent?.({ ...failed, type: 'set_completed' });
            if (pending) {
                pendingTasks.delete(requestId);
                pending.failurePersisted = true;
                pending.reject(Object.assign(new Error(event.error || '图片生成失败'), { errorDetails: event.errorDetails }));
            }
            return;
        }
        const hydrated = await hydrateAsset({
            ...event,
            sessionId: pending?.sessionId || event.sessionId,
            setId: pending?.setId || event.setId
        }, {
            sessionId: event.sessionId,
            setId: event.setId || event.requestId
        });
        window.applyImageGenerationEvent?.(hydrated);
        } catch (error) {
            if (terminal && pending) pending.processingTerminal = false;
            throw error;
        }
    }

    function queueServerEvent(event) {
        eventQueue.push(event);
        if (eventQueue.length !== 1) return;
        void (async () => {
            while (eventQueue.length) {
                const next = eventQueue[0];
                try { await handleServerEvent(next); } catch (error) { console.warn('Android 事件处理失败:', error); }
                eventQueue.shift();
            }
        })();
    }

    async function readSseResponse(response) {
        const reader = response.body?.getReader();
        if (!reader) throw new Error('服务端事件流不可读');
        const decoder = new TextDecoder();
        let buffer = '';
        let eventId = '';
        let data = [];
        const flush = () => {
            if (!data.length) return;
            if (eventId) localStorage.setItem(EVENT_CURSOR_KEY, eventId);
            try { queueServerEvent(JSON.parse(data.join('\n'))); } catch (error) { console.warn('Android SSE 数据无效:', error); }
            eventId = '';
            data = [];
        };
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            lines.forEach(rawLine => {
                const line = rawLine.replace(/\r$/, '');
                if (line === '') flush();
                else if (line.startsWith('id:')) eventId = line.slice(3).trim();
                else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
            });
        }
        flush();
    }

    function startEventStream() {
        if (eventStreamPromise) return;
        const generation = ++eventStreamGeneration;
        eventStreamPromise = (async () => {
            while (generation === eventStreamGeneration) {
                const abortController = new AbortController();
                eventStreamAbortController = abortController;
                try {
                    const settings = requireService();
                    const cursor = Number(localStorage.getItem(EVENT_CURSOR_KEY) || 0);
                    const response = await fetch(resolveUrl(`/api/v1/events?cursor=${cursor}`), {
                        headers: { Authorization: `Bearer ${settings.bearerToken}`, Accept: 'text/event-stream' },
                        cache: 'no-store',
                        signal: abortController.signal
                    });
                    if (response.status === 401) {
                        serviceConfig.bearerToken = '';
                        await pluginCall('clearServiceConfig').catch(() => {});
                        await ensureDeviceRegistration();
                        continue;
                    }
                    if (!response.ok) throw new Error(`SSE HTTP ${response.status}`);
                    const eventEpoch = String(response.headers.get('X-Event-Epoch') || '');
                    const savedEpoch = String(localStorage.getItem(EVENT_EPOCH_KEY) || '');
                    if (eventEpoch && eventEpoch !== savedEpoch && (savedEpoch || cursor > 0)) {
                        localStorage.setItem(EVENT_CURSOR_KEY, '0');
                        localStorage.setItem(EVENT_EPOCH_KEY, eventEpoch);
                        await response.body?.cancel?.();
                        continue;
                    }
                    if (eventEpoch) localStorage.setItem(EVENT_EPOCH_KEY, eventEpoch);
                    await readSseResponse(response);
                } catch (error) {
                    if (generation !== eventStreamGeneration || error.name === 'AbortError') return;
                    if (error.message.includes('尚未配置')) return;
                    await new Promise(resolve => setTimeout(resolve, 2000));
                } finally {
                    if (eventStreamAbortController === abortController) {
                        eventStreamAbortController = null;
                    }
                }
            }
        })();
        const running = eventStreamPromise;
        void running.finally(() => {
            if (eventStreamPromise === running) eventStreamPromise = null;
        }).catch(() => {});
    }

    async function chooseEditImages() {
        const result = await pluginCall('pickImages');
        const files = [];
        for (const file of result.files || []) {
            const previewUri = localImageUrl(file.path);
            files.push({ ...file, previewUri, uri: previewUri });
        }
        return {
            ok: true,
            paths: files.map(file => file.path),
            files,
            warning: result.warning || ''
        };
    }

    async function importReferenceImage(dataUrl, name) {
        const result = await pluginCall('importReferenceDataUrl', { dataUrl, name });
        if (result?.ok && result.path) {
            const previewUri = localImageUrl(result.path);
            return { ...result, uri: previewUri, previewUri };
        }
        return result;
    }

    async function rasterImageDataUrl(path) {
        const dataUrl = await readAsDataUrl(path);
        const image = new Image();
        image.src = dataUrl;
        await image.decode();
        const canvas = document.createElement('canvas');
        canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
        if (!canvas.width || !canvas.height) throw new Error('图片的尺寸无效');
        try {
            canvas.getContext('2d').drawImage(image, 0, 0);
            return canvas.toDataURL('image/png');
        } finally { image.src = ''; canvas.width = 0; canvas.height = 0; }
    }

    const rasterReferenceCache = new Map();
    async function rasterizeReference(path, mimeType = '') {
        if (!/\.(?:gif|svg)$/i.test(path) && !['image/gif', 'image/svg+xml'].includes(mimeType)) return path;
        if (rasterReferenceCache.has(path)) return rasterReferenceCache.get(path);
        const conversion = (async () => {
            const result = await pluginCall('importReferenceDataUrl', {
                dataUrl: await rasterImageDataUrl(path), name: path.split(/[\\/]/).pop().replace(/\.[^.]+$/, '') + '-reference.png'
            });
            if (!result?.ok || !result.path) throw new Error(result?.error || '参考图转换失败');
            return result.path;
        })();
        rasterReferenceCache.set(path, conversion);
        while (rasterReferenceCache.size > 32) rasterReferenceCache.delete(rasterReferenceCache.keys().next().value);
        try { return await conversion; }
        catch (error) { rasterReferenceCache.delete(path); throw error; }
    }

    async function resolveGenerationReferences(keyId, paths, options) {
        const supplied = [...new Set((paths || []).filter(Boolean))];
        const tier = window.appState?.keys?.find(key => String(key.id) === String(keyId))?.appModels?.tier;
        const limit = tier ? tier.imageEditing ? Math.max(0, Math.min(16, Number(tier.maxReferences) || 0)) : 0 : 16;
        if ((supplied.length || options.parentSetId) && !limit) throw new Error('当前档位未开放参考图和续作编辑');
        if (supplied.length > limit) throw new Error(`当前档位最多使用 ${limit} 张参考图`);
        let parents = [];
        if (options.parentSetId) {
            const saved = await pluginCall('listAssets', { sessionId: String(options.sessionId || '') });
            const parentRecord = localPreferences.get(IMAGE_SETS_KEY, []).find(record => String(record.setId) === String(options.parentSetId));
            const outputIds = new Set((parentRecord?.items || []).filter(item => item.ok !== false).map(item => String(item.resourceId)));
            parents = (saved.assets || []).filter(asset => outputIds.has(String(asset.resourceId)) && String(asset.setId) === String(options.parentSetId) && asset.path && asset.exists !== false);
            const available = limit - supplied.length;
            parents = parents.filter(asset => !supplied.includes(asset.path)).slice(0, available);
        }
        const parentPaths = parents.map(asset => asset.path);
        const all = [...new Set([...parentPaths, ...supplied])];
        const converted = [];
        for (const path of all) converted.push(await rasterizeReference(path, parents.find(asset => asset.path === path)?.mimeType));
        return [...new Set(converted)];
    }

    async function uploadReference(path) {
        await loadServiceConfig();
        const settings = requireService();
        const result = await pluginCall('uploadReference', {
            path,
            endpoint: resolveUrl('/api/v1/uploads/references'),
            bearerToken: settings.bearerToken
        });
        if (!result?.ok) throw new Error(result?.error || '参考图上传失败');
        return result.resource;
    }

    function taskResult(requestId, metadata) {
        if (pendingTasks.has(String(requestId))) throw new Error('同一任务正在生成');
        return new Promise((resolve, reject) => pendingTasks.set(String(requestId), { resolve, reject, ...metadata }));
    }

    function normalizeRequestId(value) {
        const requestId = String(value || '').trim();
        if (!requestId || requestId.length > 80) throw new Error('任务编号无效');
        return requestId;
    }

    async function pollTaskState(requestId) {
        while (pendingTasks.has(requestId)) {
            await new Promise(resolve => setTimeout(resolve, 2000));
            if (!pendingTasks.has(requestId)) return;
            try {
                const response = await requestJson(`/api/v1/image-generations/${encodeURIComponent(requestId)}`);
                const event = response.event || {};
                if (event.type === 'task_result' || event.type === 'task_failed') {
                    await handleServerEvent(event);
                }
            } catch (error) {
                if (!pendingTasks.has(requestId)) return;
                console.warn('Android 任务状态轮询失败:', error);
            }
        }
    }

    function persistFailedGeneration(options, prompt, error, pending = null) {
        const requestId = String(options?.requestId || pending?.requestId || newRequestId());
        const requestedCount = Math.max(1, Number(options?.imageCount || pending?.requestedCount) || 1);
        const message = error?.message || String(error || '图片生成失败');
        const result = {
            ok: false,
            requestId,
            setId: String(pending?.setId || requestId),
            sessionId: String(options?.sessionId || pending?.sessionId || requestId),
            parentSetId: String(options?.parentSetId || pending?.parentSetId || ''),
            roundNumber: Number(options?.roundNumber || pending?.roundNumber) || 1,
            prompt: String(prompt || pending?.prompt || ''),
            originalPrompt: String(prompt || pending?.prompt || ''),
            imageModel: options?.imageModel || 'gpt-image-2',
            aspectRatio: options?.aspectRatio || 'auto',
            transparency: ['auto', 'opaque', 'transparent'].includes(options?.background || options?.transparency)
                ? options.background || options.transparency
                : ['auto', 'opaque', 'transparent'].includes(pending?.transparency)
                    ? pending.transparency
                    : 'auto',
            createdAt: pending?.createdAt || new Date().toISOString(),
            requestedCount,
            error: message,
            errorDetails: error?.errorDetails || null,
            items: Array.from({ length: requestedCount }, (_, itemIndex) => ({
                itemIndex,
                ok: false,
                status: 'failed',
                error: message,
                errorDetails: error?.errorDetails || null
            }))
        };
        persistLocalSet(result, pending);
        return result;
    }

    async function generateImage(keyId, prompt, paths, options = {}) {
        const requestId = normalizeRequestId(options.requestId || newRequestId());
        if (pendingTasks.has(requestId) || directTasks.has(requestId)) throw new Error('同一任务正在生成');
        options = { ...options, requestId };
        const key = (await secureKeyMetadata()).find(item => String(item.id) === String(keyId));
        try { paths = await resolveGenerationReferences(keyId, paths, options); }
        catch (error) { return persistFailedGeneration(options, prompt, error); }
        if (usesDirectApi(key)) {
            if (key.distributed) {
                const state = await directKeyState(key);
                if (!state.appModels) return persistFailedGeneration(options, prompt, new Error('无法读取网关模型配置，请刷新密钥后重试'));
            }
            return generateDirectImage(key, prompt, paths, options);
        }
        let pending = null;
        try {
            await ensureDeviceRegistration(keyId);
            const settings = requireService();
            await registerKey({ id: String(keyId), name: 'API Key' });
            startEventStream();
            const references = [];
            for (const path of paths || []) references.push(await uploadReference(path));
            const resultPromise = taskResult(requestId, {
                requestId,
                setId: requestId,
                sessionId: String(options.sessionId || requestId),
                parentSetId: String(options.parentSetId || ''),
                roundNumber: Number(options.roundNumber) || 1,
                prompt,
                requestedCount: Number(options.imageCount) || 1,
                transparency: options.background || options.transparency || 'auto',
                imageModel: options.imageModel || 'gpt-image-2',
                aspectRatio: options.aspectRatio || 'auto',
                createdAt: new Date().toISOString()
            });
            pending = pendingTasks.get(requestId) || null;
            const creation = await requestJson('/api/v1/image-generations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    keyId: String(keyId),
                    prompt,
                    referenceIds: references.map(reference => reference.resourceId),
                    options: { ...options, requestId }
                })
            });
            if (String(creation.requestId || '') !== requestId) {
                throw new Error('服务端任务编号不一致');
            }
            void pollTaskState(requestId);
            return await resultPromise;
        } catch (error) {
            const requestId = String(options.requestId || pending?.requestId || '');
            if (requestId) pendingTasks.delete(requestId);
            if (!pending?.failurePersisted) {
                const failed = persistFailedGeneration(options, prompt, error, pending);
                window.applyImageGenerationEvent?.({ ...failed, type: 'set_completed' });
            }
            throw error;
        }
    }

    function directReasoning(options, profile) {
        if (profile) {
            if (options.parentSetId && (!options.reasoningMode || options.reasoningMode === 'instant')) {
                if (profile.tier && !profile.tier.imageEditing || !profile.continuation?.model) throw new Error('当前密钥没有可用的继续编辑模型');
                return { model: profile.continuation.model, effort: profile.continuation.effort || '' };
            }
            const configured = options.reasoningMode === 'advanced' ? profile.reasoning?.[options.reasoningModel] : profile.reasoningModes?.[options.reasoningMode];
            if (configured?.model) return { model: configured.model, effort: options.reasoningMode === 'advanced' ? options.reasoningEffort : configured.effort };
        }
        if (options.parentSetId && (!options.reasoningMode || options.reasoningMode === 'instant')) return { model: 'gpt-6-luna', effort: 'low' };
        const slots = { luna: 'gpt-6-luna', terra: 'gpt-6.1-terra', sol: 'gpt-6.1-sol' };
        const preset = {
            flash: ['luna', 'medium'], medium: ['luna', 'medium'], high: ['terra', 'high'],
            extra: ['terra', 'xhigh'], max: ['sol', 'xhigh']
        }[options.reasoningMode];
        const [slot, effort] = preset || [options.reasoningModel || 'luna', options.reasoningEffort || 'medium'];
        return { model: slots[slot] || slot || slots.luna, effort };
    }

    function directSafeMessage(value) {
        return String(value || '').replace(/\bsk-[A-Za-z0-9_-]{8,}/gi, '[API Key]')
            .replace(/Bearer\s+[^\s",}]+/gi, 'Bearer [redacted]')
            .replace(/("?(?:api[_-]?key|authorization|password|secret)"?\s*[:=]\s*)"?[^\s",}]+"?/gi, '$1[redacted]')
            .replace(/(https?:\/\/)[^/\s@]+@/gi, '$1[redacted]@').slice(0, 1200);
    }

    function directError(failure, stage, attempts) {
        let nested = failure;
        for (let depth = 0; depth < 8 && nested?.cause && !nested.errorDetails; depth += 1) nested = nested.cause;
        const source = nested?.errorDetails || {};
        const status = Number(source.status ?? failure?.status ?? failure?.cause?.status) || 0;
        const cause = directSafeMessage(source.cause || failure?.message || failure || '请求失败');
        const code = directSafeMessage(source.code || failure?.code || (status ? `http_${status}`
            : /Connection|Timeout/i.test(failure?.name || '') ? 'network_error' : 'request_failed'));
        const cancelled = failure?.name === 'AbortError' || code === 'cancelled';
        const terminal = cancelled || /tls_certificate_invalid|insufficient_quota|quota_exceeded|billing|usage_limit|rpm_limit|permission|content_filter|model_refusal|max_turns|guardrail|invalid_request|sdk_unavailable/i.test(code)
            || /MaxTurnsExceeded|ModelRefusal|UserError|Guardrail/.test(failure?.name || '')
            || /insufficient.quota|exceeded.your.current.quota|额度不足|余额不足|同一任务正在生成/i.test(cause);
        const network = /network_error|connection_error|timeout|ECONNRESET|ECONNREFUSED|ENETUNREACH|ETIMEDOUT/i.test(code)
            || /APIConnection|Timeout/.test(failure?.name || '');
        const retryable = source.retryable !== false && !terminal && (network || status === 408 || status === 429 || status >= 500
            || /^(server_error|internal_error|rate_limit_exceeded|temporarily_unavailable|invalid_response)$/.test(code));
        const diagnosticId = directSafeMessage(source.diagnosticId || failure?.request_id || failure?.headers?.get?.('x-request-id') || '');
        const details = { stage, code: cancelled ? 'cancelled' : code, attempts: Math.max(attempts, Number(source.attempts) || 0), maxAttempts: 3,
            cause, lastCause: cause, status, diagnosticId, retryable };
        const error = new Error(cause);
        error.name = cancelled ? 'AbortError' : 'Error';
        error.errorDetails = details;
        error.retryAfter = failure?.retryAfter ?? failure?.headers?.['retry-after'] ?? failure?.headers?.get?.('retry-after');
        return error;
    }

    function directAbort() { return new DOMException('生成已停止', 'AbortError'); }

    function directRetryDelay(error, attempt) {
        const value = error.retryAfter;
        if (value != null && String(value).trim() !== '') {
            const seconds = Number(value);
            const milliseconds = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(value) - Date.now();
            if (Number.isFinite(milliseconds)) return Math.max(0, milliseconds);
        }
        return 750 * 2 ** (attempt - 1);
    }

    function waitDirectRetry(milliseconds, signal) {
        return new Promise((resolve, reject) => {
            if (signal.aborted) { reject(directAbort()); return; }
            const abort = () => { clearTimeout(timer); reject(directAbort()); };
            const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, milliseconds);
            signal.addEventListener('abort', abort, { once: true });
        });
    }

    async function retryDirectRequest(run, { stage, progress, options, itemIndex }) {
        for (let attempt = 1; attempt <= 3; attempt += 1) {
            if (progress.controller.signal.aborted) throw directAbort();
            try { return await run(attempt); }
            catch (failure) {
                if (progress.controller.signal.aborted) throw directAbort();
                const error = directError(failure, stage, attempt);
                if (!error.errorDetails.retryable || error.errorDetails.attempts >= 3) throw error;
                const delayMs = directRetryDelay(error, attempt);
                if (delayMs > 120000) {
                    error.errorDetails = { ...error.errorDetails, code: 'retry_after_too_long', retryable: false,
                        cause: `${error.errorDetails.cause}；上游要求等待 ${Math.ceil(delayMs / 1000)} 秒，超过 120 秒自动重试等待上限，请稍后重试` };
                    error.errorDetails.lastCause = error.errorDetails.cause;
                    error.message = error.errorDetails.cause;
                    throw error;
                }
                window.applyImageGenerationEvent?.({ type: 'request_retry', ...options,
                    setId: options.purpose === 'polish' ? '' : options.requestId,
                    stage, itemIndex, attempt: attempt + 1, maxAttempts: 3, delayMs,
                    cause: error.errorDetails.cause, status: error.errorDetails.status,
                    diagnosticId: error.errorDetails.diagnosticId, resetPreview: stage === 'image' });
                await waitDirectRetry(delayMs, progress.controller.signal);
            }
        }
    }

    async function requestNativeDirect(payload, signal, method = 'requestDirectApi') {
        if (signal.aborted) throw directAbort();
        return new Promise((resolve, reject) => {
            const abort = () => {
                void pluginCall('cancelDirectApi', { requestId: payload.requestId }).catch(() => {});
                reject(directAbort());
            };
            signal.addEventListener('abort', abort, { once: true });
            pluginCall(method, payload).then(result => {
                if (signal.aborted) { reject(directAbort()); return; }
                if (result?.ok === false) {
                    reject(Object.assign(new Error(result.error || 'API 直连失败'), {
                        errorDetails: result.errorDetails, headers: result.headers
                    }));
                } else resolve(result);
            }, reject).finally(() => signal.removeEventListener('abort', abort));
        });
    }

    async function createDirectWorkspace(options, initialPaths, totalReferences, referenceBudget, signal) {
        if (!options.parentSetId) return null;
        const records = localPreferences.get(IMAGE_SETS_KEY, []);
        const parent = records.find(record => String(record.setId) === String(options.parentSetId));
        const workspaceId = String(parent?.sessionId || options.requestId);
        const history = parent ? records.filter(record => String(record.sessionId) === workspaceId).slice().reverse() : [];
        const native = parent ? await pluginCall('listAssets', { sessionId: workspaceId }) : { assets: [] };
        const assets = new Map(), viewed = new Set();
        for (const asset of native.assets || []) {
            if (asset.sessionId && String(asset.sessionId) !== workspaceId) continue;
            if (asset.resourceId && asset.path) assets.set(String(asset.resourceId), { ...asset, assetId: String(asset.resourceId), sourceRoles: ['workspace'], sourceSetIds: [asset.setId || ''], exists: asset.exists !== false });
        }
        const selected = [];
        for (const [index, path] of initialPaths.entries()) {
            const existing = [...assets.values()].find(asset => asset.path === path);
            const id = existing?.assetId || `current-${index + 1}`;
            if (!existing) assets.set(id, { assetId: id, path, previewPath: path, sourceRoles: ['current-input'], sourceSetIds: [options.requestId], exists: true });
            viewed.add(id); selected.push(id);
        }
        let selectedIds = selected, operation = selected.length ? 'edit' : 'generate', rationale = '', webCount = 0, webViewed = 0;
        const publicAsset = asset => ({ ...asset, viewed: viewed.has(asset.assetId), previewPath: asset.previewPath || asset.path });
        const fail = message => ({ error: { code: 'workspace_tool_rejected', message } });
        return {
            async list_image_workspace({ kind, offset, limit }) {
                if (!['history', 'assets'].includes(kind) || !Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 50) return fail('工作区分页参数无效');
                const items = kind === 'assets' ? [...assets.values()].map(publicAsset) : history.map(record => ({ setId: record.setId, parentSetId: record.parentSetId, roundNumber: record.roundNumber, userPrompt: record.originalPrompt || record.prompt, reasoningSummary: record.reasoningSummary, operation: record.operation, inputAssetIds: record.inputAssetIds || [], outputAssetIds: (record.items || []).map(item => item.resourceId).filter(Boolean) }));
                let end = Math.min(offset + limit, items.length), bytes = 0;
                for (let index = offset; index < end; index++) { const size = new TextEncoder().encode(JSON.stringify(items[index])).length; if (index > offset && bytes + size > 1024 * 1024) { end = index; break; } bytes += size; }
                return { workspaceId, parentSetId: options.parentSetId, kind, offset, total: items.length, hasMore: end < items.length, items: items.slice(offset, end), resultCount: end-offset, selectedAssetIds: [...selectedIds], operation, maxReferences: totalReferences, remainingReferenceSlots: Math.max(0, totalReferences - selectedIds.length - webCount) };
            },
            async view_image_workspace_assets({ asset_ids }) {
                if (!Array.isArray(asset_ids) || asset_ids.length > 16 || asset_ids.some(id => !assets.has(id))) return fail('素材不属于当前工作区');
                const output = [], errors = [];
                for (const id of [...new Set(asset_ids)]) {
                    if (signal.aborted) throw directAbort();
                    const asset = assets.get(id), item = publicAsset(asset);
                    if (!viewed.has(id)) {
                        if (viewed.size + webViewed >= totalReferences) { errors.push({ assetId: id, message: '当前档位的图片分析槽位已满' }); continue; }
                        try {
                            const image = new Image(); image.src = await readAsDataUrl(asset.path); await image.decode();
                            const longest = Math.max(image.naturalWidth, image.naturalHeight), scale = Math.min(512, longest) / longest;
                            const canvas = document.createElement('canvas'); canvas.width = Math.max(1, image.naturalWidth * scale); canvas.height = Math.max(1, image.naturalHeight * scale);
                            try { canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height); item.previewDataUrl = canvas.toDataURL('image/jpeg', .65); if (item.previewDataUrl.length > 131100) throw new Error('参考预览超过大小限制'); }
                            finally { image.src = ''; canvas.width = canvas.height = 0; }
                            viewed.add(id);
                        } catch (error) { errors.push({ assetId: id, message: directSafeMessage(error.message) }); continue; }
                    }
                    item.viewed = true; output.push(item);
                }
                return { assets: output, errors, resultCount: output.length };
            },
            async select_image_workspace_inputs(args) {
                if (!Array.isArray(args.asset_ids) || args.asset_ids.length > 16) return fail('工作区选择参数无效');
                const ids = [...new Set(args.asset_ids)];
                if (!['edit', 'generate'].includes(args.operation) || !args.rationale?.trim() || ids.some(id => !assets.has(id) || !assets.get(id).exists)) return fail('只能选择本工作区存在的素材');
                if (ids.length + webCount > totalReferences) return fail('当前档位的参考图总数已满');
                if (args.operation === 'edit' && !ids.length) return fail('编辑操作至少需要一张参考图片');
                selectedIds = ids; operation = args.operation; rationale = args.rationale;
                return { operation, selectedAssetIds: [...selectedIds], descriptions: {}, rationale, resultCount: selectedIds.length };
            },
            setWebReferences(refs) { if (selectedIds.length + refs.length > totalReferences) throw new Error('当前档位的参考图总数已满'); webCount = refs.length; },
            setInspectedWebCount(count) { webViewed = count; },
            referenceLimits() { return { inspect: Math.min(12, Math.max(0, totalReferences - viewed.size)), adopt: Math.min(referenceBudget, Math.max(0, totalReferences - selectedIds.length)) }; },
            metadata() { return { workspaceId, continuationPlan: { operation, selectedAssetIds: [...selectedIds], descriptions: {}, rationale }, referencePaths: selectedIds.map(id => assets.get(id).path), inputAssetIds: [...selectedIds] }; }
        };
    }

    async function prepareDirectPrompt(keyId, prompt, options = {}, sharedProgress = null) {
        if (!window.MirraAgentsSDK?.runNativeAgent) {
            throw Object.assign(new Error('本地 OpenAI Agents SDK 未能加载，请重新启动或更新应用'), { code: 'sdk_unavailable' });
        }
        const progress = sharedProgress || { cancelled: false, controller: new AbortController() };
        const requestId = options.requestId || newRequestId('agent');
        const profile = directKeyStates.get(String(keyId))?.appModels || window.appState?.keys?.find(key => String(key.id) === String(keyId))?.appModels;
        const { model, effort } = directReasoning(options, profile);
        const originalPaths = options.agentReferencePaths || [];
        const tier = window.appState?.keys?.find(key => String(key.id) === String(keyId))?.appModels?.tier;
        const totalReferences = tier ? tier.imageEditing ? Math.max(0, Math.min(16, Number(tier.maxReferences) || 0)) : 0 : 16;
        const depthMode = options.reasoningMode === 'advanced' ? ({ low: 'flash', medium: 'medium', high: 'high', xhigh: 'extra', max: 'max' }[effort] || 'medium') : options.reasoningMode;
        const [turnBudget, referenceBudget] = ({ instant: [1, 0], flash: [3, 3], medium: [5, 4], high: [7, 6], extra: [10, 6], max: [12, 8] }[depthMode] || [5, 4]);
        const maxInspectReferences = options.purpose === 'polish' ? 0 : Math.min(12, Math.max(0, totalReferences - originalPaths.length));
        const maxAdoptReferences = options.output_format === 'svg' ? 0 : Math.min(referenceBudget, maxInspectReferences);
        const maxTurns = Math.min(32, turnBudget + (options.webSearchEnabled && maxInspectReferences ? 2 : 0) + (options.parentSetId || options.continuation ? 4 : 0));
        const initialInput = originalPaths.length ? [{ role: 'user', content: [{ type: 'input_text', text: prompt }, ...await Promise.all(originalPaths.map(async path => {
            const image = new Image(); image.src = await readAsDataUrl(path); await image.decode();
            const longest = Math.max(image.naturalWidth, image.naturalHeight);
            const canvas = document.createElement('canvas'); canvas.width = Math.max(1, image.naturalWidth * Math.min(512, longest) / longest); canvas.height = Math.max(1, image.naturalHeight * Math.min(512, longest) / longest);
            try { canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height); let data = canvas.toDataURL('image/jpeg', .75);
                if (data.length > 131100) data = canvas.toDataURL('image/jpeg', .5);
                if (data.length > 131100) throw new Error('参考图预览超过大小限制'); return { type: 'input_image', image_url: data };
            } finally { image.src = ''; canvas.width = canvas.height = 0; }
        }))] }] : prompt;
        const workspace = await createDirectWorkspace(options, originalPaths, totalReferences, referenceBudget, progress.controller.signal);
        const reasoningTurns = []; let httpAttempts = 0, latestStats = {};
        progress.agentRequestAttempts = 0;
        const publish = event => window.applyImageGenerationEvent?.({ ...event, requestId, setId: options.purpose === 'polish' ? '' : requestId, sessionId: options.sessionId || requestId });
        const recordEvent = event => {
            latestStats = Object.fromEntries(['turnCount', 'requestCount', 'toolCallCount', 'webSearchCallCount', 'localToolCallCount'].map(name => [name, Number(event[name]) || 0])); latestStats.requestCount = Math.max(httpAttempts, latestStats.requestCount);
            const index = Math.max(1, Number(event.turn) || 1); let turn = reasoningTurns.find(turn => turn.turn === index);
            if (!turn) { turn = { turn: index, status: 'running', text: '', tools: [] }; reasoningTurns.push(turn); }
            if (event.type === 'text_delta') turn.text += event.delta || '';
            if (event.type === 'turn_completed') turn.status = 'completed';
            if (/^(?:tool|web_search)_(?:started|completed|failed)$/.test(event.type)) {
                let tool = turn.tools.find(tool => tool.callId === event.callId); if (!tool) { tool = { callId: event.callId }; turn.tools.push(tool); }
                Object.assign(tool, event, { status: event.type.endsWith('started') ? 'running' : event.type.endsWith('failed') ? 'failed' : 'completed' });
            }
            let type = ({ turn_started: 'react_turn_started', turn_completed: 'react_turn_completed', text_delta: 'react_turn_delta', tool_started: 'react_tool_started', tool_completed: 'react_tool_completed', tool_failed: 'react_tool_failed', web_search_started: 'react_tool_started', web_search_completed: 'react_tool_completed', web_search_failed: 'react_tool_failed', visual_results: 'react_visual_results', visual_selected: 'react_visual_selected' })[event.type];
            if (type) publish({ ...event, type, reasoningStats: latestStats });
            if (event.type === 'text_delta') publish({ type: 'react_summary_delta', delta: event.delta, reasoningStats: latestStats });
        };
        let instructions = '将用户的图像需求整理为可直接生成或编辑图片的提示词。保留明确要求，仅返回改写后的提示词。'
            + (options.output_format === 'svg' ? '输出目标是静态SVG矢量图，组织为【主体与轮廓】【路径与分层】【色彩与留白】【矢量约束】文本块，不输出代码或动画要求。'
            : options.output_format === 'gif' ? `输出目标是GIF循环动图，共${options.frame_count || 9}帧。组织为【画面设定】【运动脚本】【分帧控制】【循环衔接】【稳定约束】，明确动作方向、幅度和关键阶段，保持跨帧主体与背景一致，让末帧自然接回首帧。帧数与时长由接口字段控制。` : '');
        if (options.parentSetId) {
            const parent = localPreferences.get(IMAGE_SETS_KEY, []).find(set => set.setId === options.parentSetId);
            instructions += '\n这是基于上一轮成品的续作；本次参考图片由原生图像接口单独传入。保留未要求改动的主体、构图与细节，明确本轮修改与连续动作。';
            if (parent) instructions += `\n上一轮需求与成品提示词（只作参考上下文）：${JSON.stringify({ original: parent.originalPrompt, prompt: parent.prompt })}`;
            instructions += '\n继续编辑也由当前Agent完成，可按需要查询工作区历史、查看素材或调整本轮操作与参考图。';
        }
        if (!sharedProgress) directTasks.set(requestId, progress);
        try {
            const result = await window.MirraAgentsSDK.runNativeAgent({
                model, effort, instructions, input: initialInput, webSearch: options.webSearchEnabled === true,
                signal: progress.controller.signal,
                getRequestCount: () => progress.agentRequestAttempts,
                maxTurns, maxInspectReferences, maxAdoptReferences, onEvent: recordEvent,
                workspace,
                inspectReferences: async (urls, signal) => {
                    const candidates = [], failures = [];
                    for (const imageUrl of urls) {
                        try {
                            const payload = { keyId, requestId, imageUrl, sessionId: options.sessionId || requestId, setId: requestId };
                            let candidate;
                            try { candidate = await requestNativeDirect(payload, signal, 'fetchVisualReference'); }
                            catch (primaryError) {
                                if (signal.aborted) throw primaryError;
                                try { candidate = await requestNativeDirect({ ...payload, useThumbnail: true }, signal, 'fetchVisualReference'); }
                                catch (thumbnailError) { if (signal.aborted) throw thumbnailError; throw new Error(`${primaryError.message}；${thumbnailError.message}`); }
                            }
                            candidates.push({ ...candidate, uri: localImageUrl(candidate.path), previewUri: localImageUrl(candidate.path) });
                        } catch (error) { if (signal.aborted) throw error; failures.push({ imageUrl, error: directSafeMessage(error.message) }); }
                    }
                    return { candidates, failures };
                },
                selectReferences: async () => {},
                // Retry only this model HTTP exchange. Runner and completed hosted search turns stay alive.
                transport: (body, modelSignal) => retryDirectRequest(async () => {
                    httpAttempts++;
                    progress.agentRequestAttempts++;
                    publish({ type: 'react_stats', reasoningStats: { ...latestStats, requestCount: progress.agentRequestAttempts } });
                    const response = await requestNativeDirect({ keyId, operation: 'respond', sdkTransport: true, requestId, body,
                        reasoningMode: options.parentSetId && (!options.reasoningMode || options.reasoningMode === 'instant') ? 'continuation' : options.reasoningMode || 'polish' }, modelSignal);
                    let data;
                    try { data = typeof response.body === 'string' ? JSON.parse(response.body) : response.body; }
                    catch { throw Object.assign(new Error('上游 Responses 返回了不完整的 JSON'), { code: 'invalid_response', status: response.status }); }
                    if (response.status < 200 || response.status >= 300) {
                        throw Object.assign(new Error(directSafeMessage(data?.error?.message || `HTTP ${response.status}`)), {
                            status: response.status, code: data?.error?.code || `http_${response.status}`,
                            headers: response.headers, request_id: response.headers?.['x-request-id']
                        });
                    }
                    if (data?.status === 'failed' || data?.status === 'incomplete' || data?.status === 'cancelled') {
                        const reason = data.incomplete_details?.reason;
                        const code = data.status === 'incomplete' ? `incomplete_${reason || 'response'}`
                            : data.status === 'cancelled' ? 'cancelled' : data.error?.code || 'failed_response';
                        throw Object.assign(new Error(directSafeMessage(data.error?.message
                            || (reason ? `上游响应不完整：${reason}` : `上游响应状态：${data.status}`))), {
                            status: response.status, code, headers: response.headers,
                            request_id: response.headers?.['x-request-id'] || data.id
                        });
                    }
                    return response;
                }, { stage: 'agent', progress: { controller: { signal: modelSignal } }, options: { ...options, requestId } })
            });
            return { ...result, model, effort, reasoningTurns, reasoningStats: { ...result.reasoningStats, requestCount: httpAttempts } };
        } catch (failure) {
            if (progress.controller.signal.aborted) throw directAbort();
            const error = directError(failure, 'agent', 1); error.reasoningStats = { ...latestStats, requestCount: httpAttempts }; error.reasoningTurns = reasoningTurns;
            for (const name of ['webInspectedReferences', 'webReferenceFailures', 'webSelectionRationale', 'webImageCandidateCount', 'continuationPlan', 'workspaceId']) if (failure[name] !== undefined) error[name] = failure[name];
            throw error;
        } finally { if (!sharedProgress) directTasks.delete(requestId); void pluginCall('clearVisualReferences', { keyId, requestId }).catch(() => {}); }
    }

    async function generateDirectImage(key, prompt, paths, options) {
        const { requestId } = options;
        const progress = { cancelled: false, controller: new AbortController() };
        directTasks.set(requestId, progress);
        const imageCount = Math.max(1, Math.min(9, Number(options.imageCount) || 1));
        const result = {
            ...options, ok: false, requestId, setId: requestId, sessionId: options.sessionId || requestId,
            imageModel: options.imageModel, createdAt: new Date().toISOString(),
            prompt, originalPrompt: prompt, transparency: options.background || 'auto', requestedCount: imageCount, items: []
        };
        const emit = event => window.applyImageGenerationEvent?.({ requestId, setId: requestId, sessionId: result.sessionId, ...event });
        const output = options.output_format || 'png';
        let actualReferencePaths = paths || [];
        try {
            if (options.reasoningMode && options.reasoningMode !== 'instant' || options.continuation || options.parentSetId) {
                const started = Date.now();
                const reasoning = directReasoning(options, directKeyStates.get(String(key.id))?.appModels || window.appState?.keys?.find(state => String(state.id) === String(key.id))?.appModels);
                emit({ type: 'react_started', mode: options.reasoningMode, model: reasoning.model,
                    reasoningEffort: reasoning.effort, webSearchEnabled: options.webSearchEnabled === true });
                const planned = await prepareDirectPrompt(key.id, prompt, { ...options, agentReferencePaths: paths }, progress);
                Object.assign(result, { prompt: planned.prompt, reasoningModel: planned.model, reasoningEffort: planned.effort,
                    reasoningDurationMs: Date.now() - started, reasoningSummary: planned.prompt, reasoningStatus: 'completed',
                    reasoningUsage: planned.usage, webSearchUsed: planned.webSearchUsed, webSearchEnabled: options.webSearchEnabled === true,
                    webReferences: planned.webReferences || [], webReferenceCount: planned.webReferences?.length || 0, webInspectedReferences: planned.webInspectedReferences || [], webReferenceFailures: planned.webReferenceFailures || [], webSelectionRationale: planned.webSelectionRationale || '', webImageCandidateCount: planned.webImageCandidateCount || 0, webSearchSources: planned.webSearchSources || [], reasoningTurns: planned.reasoningTurns || [], reasoningStats: planned.reasoningStats });
                if (planned.referencePaths) actualReferencePaths = planned.referencePaths;
                if (planned.continuationPlan) Object.assign(result, { continuationPlan: planned.continuationPlan, operation: planned.continuationPlan.operation, inputAssetIds: planned.inputAssetIds || [], workspaceId: planned.workspaceId });
                if (options.parentSetId && (!options.reasoningMode || options.reasoningMode === 'instant')) result.prompt = prompt;
                emit({ type: 'react_completed', ...result, model: planned.model, summary: planned.prompt });
            }
            const body = { model: options.imageModel, prompt: result.prompt, size: options.size || 'auto',
                quality: options.quality || 'medium', background: result.transparency, output_format: output, n: 1 };
            for (const field of ['complexity', 'output_compression', 'frame_count', 'frame_duration_ms']) {
                if (options[field] != null) body[field] = options[field];
            }
            for (let itemIndex = 0; itemIndex < imageCount && !progress.cancelled; itemIndex += 1) {
                try {
                    emit({ type: 'item_started', itemIndex });
                    const preparedPaths = []; for (const path of actualReferencePaths) preparedPaths.push(await rasterizeReference(path));
                    const asset = await retryDirectRequest(() => requestNativeDirect({
                        keyId: key.id, operation: 'generate', requestId, body, paths: [...preparedPaths, ...(result.webReferences || []).map(reference => reference.path)],
                        setId: requestId, sessionId: result.sessionId
                    }, progress.controller.signal), { stage: 'image', progress, options, itemIndex });
                    const uri = localImageUrl(asset.path);
                    result.items.push({ ...asset, ok: true, status: 'completed', itemIndex, uri, previewUri: uri,
                        format: asset.mimeType === 'image/svg+xml' ? 'svg' : asset.mimeType.split('/')[1] });
                    emit({ type: 'item_completed', itemIndex, result: result.items[result.items.length - 1] });
                } catch (failure) {
                    if (progress.cancelled) break;
                    const error = failure.errorDetails ? failure : directError(failure, 'image', 1);
                    result.error = error.message;
                    result.errorDetails = error.errorDetails;
                    result.items.push({ ok: false, status: 'failed', itemIndex, error: error.message, errorDetails: error.errorDetails });
                    emit({ type: 'item_failed', itemIndex, error: error.message, errorDetails: error.errorDetails });
                    if (!error.errorDetails.retryable) break;
                }
            }
            if (progress.cancelled) result.error = '生成已停止';
        } catch (error) {
            result.error = progress.cancelled ? '生成已停止' : error.message || String(error);
            for (const name of ['webInspectedReferences', 'webReferenceFailures', 'webSelectionRationale', 'webImageCandidateCount', 'continuationPlan', 'workspaceId']) if (error[name] !== undefined) result[name] = error[name];
            if (!progress.cancelled) {
                result.errorDetails = error.errorDetails || directError(error, 'agent', 1).errorDetails;
                result.reasoningStatus = 'failed';
                result.reasoningStats = error.reasoningStats; result.reasoningTurns = error.reasoningTurns;
                result.reasoningSummary = result.error;
                emit({ type: 'react_failed', error: result.error, errorDetails: result.errorDetails });
            }
        } finally {
            directTasks.delete(requestId);
        }
        result.ok = result.items.some(item => item.ok);
        result.cancelled = progress.cancelled;
        while (result.items.length < imageCount) {
            result.items.push({ ok: false, status: result.cancelled ? 'cancelled' : 'failed',
                itemIndex: result.items.length, error: result.error });
            result.items[result.items.length - 1].errorDetails = result.errorDetails || null;
        }
        persistLocalSet(result);
        emit({ ...result, type: result.cancelled ? 'set_cancelled' : 'set_completed' });
        return result;
    }

    async function deleteImageSet(sessionId, setId) {
        const assets = await pluginCall('listAssets', { sessionId: String(sessionId || '') });
        for (const asset of assets.assets || []) {
            if (!setId || asset.setId === setId) await pluginCall('deleteAsset', { resourceId: asset.resourceId });
        }
        localPreferences.set(
            IMAGE_SETS_KEY,
            localPreferences.get(IMAGE_SETS_KEY, []).filter(item => item?.setId !== setId)
        );
        return { ok: true, sessionId, setId };
    }

    async function copyGeneratedImage(path) {
        if (!navigator.clipboard?.write || !window.ClipboardItem) return { ok: false, error: '当前设备不支持图片剪贴板' };
        const original = await readAsDataUrl(path);
        const mime = /^data:([^;,]+)/.exec(original)?.[1] || '';
        if (mime === 'image/png' || ClipboardItem.supports?.(mime)) {
            try {
                const blob = await (await fetch(original)).blob();
                await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
                return { ok: true };
            } catch (error) { if (mime === 'image/png') throw error; }
        }
        const dataUrl = await rasterImageDataUrl(path);
        const blob = await (await fetch(dataUrl)).blob();
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        return { ok: true, message: mime === 'image/gif' ? '已复制 GIF 首帧' : mime === 'image/svg+xml' ? '已复制 SVG 预览' : '图片已复制到剪贴板' };
    }

    async function saveEditedImage(path) {
        return pluginCall('saveAssetToGallery', { path });
    }

    async function exportImageSets(selected) {
        const selectedIds = new Set((selected || []).map(set => String(set.setId || '')));
        const localSets = await listLocalImageSets();
        const skippedSets = [];
        let exported = 0;
        for (const set of localSets) {
            if (!selectedIds.has(String(set.setId || ''))) continue;
            let setExported = 0;
            for (const item of set.items || []) {
                const path = item.path || item.result?.path || '';
                if (!path || item.status !== 'completed') continue;
                await saveEditedImage(path);
                exported += 1;
                setExported += 1;
            }
            if (!setExported) skippedSets.push(set.setId);
        }
        return { ok: true, exported, skippedSets, path: 'DCIM/API_TOOLS' };
    }

    const api = {
        get_state: getState,
        add_key: async (name, value, baseUrl = GATEWAY_API_URL) => {
            const keyId = newRequestId('android-key');
            const key = { id: keyId, name, baseUrl };
            await pluginCall('saveSecureKey', { keyId, name, value, baseUrl });
            try {
                if (usesDirectApi(key)) {
                    const state = await directKeyState(key, true);
                    if (state.lastError) throw new Error(state.lastError);
                } else {
                    await ensureDeviceRegistration(keyId);
                    await registerKey(key);
                }
            } catch (error) {
                await pluginCall('deleteSecureKey', { keyId });
                directKeyStates.delete(keyId);
                throw error;
            }
            return { ok: true, activeKeyId: keyId, state: await getState() };
        },
        delete_key: async id => {
            const key = (await secureKeyMetadata()).find(item => String(item.id) === String(id));
            if (!usesDirectApi(key)) {
                try { await requestJson(`/api/v1/keys/${encodeURIComponent(id)}`, { method: 'DELETE' }); } catch {}
            }
            directKeyStates.delete(id);
            await pluginCall('deleteSecureKey', { keyId: id });
            return { state: await getState() };
        },
        choose_edit_images: chooseEditImages,
        import_reference_image: importReferenceImage,
        generate_image: generateImage,
        cancel_image_generation: requestId => {
            const direct = directTasks.get(requestId);
            if (direct) {
                direct.cancelled = true;
                direct.controller.abort();
                return pluginCall('cancelDirectApi', { requestId });
            }
            return requestJson(`/api/v1/image-generations/${encodeURIComponent(requestId)}/cancel`, { method: 'POST' });
        },
        load_generated_image: async path => ({ ok: true, path, dataUrl: localImageUrl(path) }),
        save_edited_image: saveEditedImage,
        copy_generated_image: copyGeneratedImage,
        list_image_sets: async () => ({ ok: true, sets: await listLocalImageSets(), source: 'android-local' }),
        delete_image_set: deleteImageSet,
        open_generated_pictures: async () => ({ ok: true }),
        export_image_sets: exportImageSets,
        polish_prompt: async (keyId, prompt, options = {}) => {
            const key = (await secureKeyMetadata()).find(item => String(item.id) === String(keyId));
            if (usesDirectApi(key)) {
                try { return { ok: true, ...(await prepareDirectPrompt(key.id, prompt, { ...options, purpose: 'polish' })) }; }
                catch (error) { return { ok: false, error: directSafeMessage(error.message), errorDetails: error.errorDetails || directError(error, 'agent', 1).errorDetails }; }
            }
            return requestJson('/api/v1/prompts/polish', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keyId: String(keyId), prompt, options })
            });
        },
        append_image_stream_debug: async () => ({ ok: true }),
        report_startup: async stage => {
            if (stage === 'frontend_interactive' && appUpdateState.status === 'idle') {
                const before = appUpdateState;
                void latestAndroidUpdate().then(update => {
                    if (appUpdateState !== before) return;
                    appUpdateState = {
                        ...update,
                        showPrompt: update.available && update.latestVersion !== localPreferences.get('ignored-app-update', '')
                    };
                    window.applyUpdateState?.(appUpdateState);
                }).catch(() => {});
            }
            return { ok: true };
        },
        refresh_now: async () => {
            const { metadata } = await registerAllKeys();
            if (metadata.length) await requestJson('/api/v1/refresh', { method: 'POST' });
            const state = await getState(true);
            return { ok: true, valid: state.keys.some(key => key.status === 'active'), state };
        },
        update_thresholds: async thresholds => { localPreferences.set('thresholds', thresholds); return { ok: true, thresholds }; },
        update_rate_limit_progress_mode: async mode => { localPreferences.set('rate-mode', mode); return { rateLimitProgressMode: mode }; },
        update_refresh_intervals: async (foreground, background) => {
            const refreshIntervals = { foreground: Number(foreground) || 60, background: Number(background) || 300 };
            localPreferences.set('refresh-intervals', refreshIntervals);
            return { state: { refreshIntervals } };
        },
        update_app_preferences: async () => ({ ok: true, state: await getState() }),
        set_window_background: async () => ({ ok: true }),
        native_drag: async () => ({ ok: true, maximized: false }),
        window_action: async () => ({ ok: true, maximized: false }),
        set_always_on_top: async () => ({ ok: true, alwaysOnTop: false }),
        open_devtools: async () => ({ ok: false, error: 'Android 版本不提供开发者工具' }),
        open_benchmark: async () => ({ ok: false, error: 'Android 版本不提供基准测试窗口' }),
        restart_app: async () => { window.location.reload(); return { ok: true }; },
        check_for_updates: async () => {
            const update = await latestAndroidUpdate();
            appUpdateState = update;
            return { ok: true, update };
        },
        download_update: async () => {
            const update = await latestAndroidUpdate();
            if (!update.available) {
                appUpdateState = update;
                return { ok: true, update };
            }
            const release = update.release || {};
            const sizeBytes = Number(release.downloadSize) || 0;
            appUpdateState = { ...update, status: 'downloading', percent: 0, message: '正在连接下载源' };
            const listener = await native.addListener?.('appUpdateProgress', event => {
                const downloaded = Math.max(0, Number(event.bytes) || 0);
                const percent = sizeBytes > 0 ? Math.min(99, 100 * downloaded / sizeBytes) : 0;
                const message = event.verifying ? '正在校验安装包' : `正在下载更新 · ${(downloaded / 1048576).toFixed(1)} MB`;
                appUpdateState = { ...appUpdateState, percent, message };
                window.applyUpdateState?.(appUpdateState);
            });
            let result;
            try {
                result = await pluginCall('downloadAppUpdate', { endpoint: release.downloadUrl, sha256: release.sha256, sizeBytes });
            } catch (error) {
                appUpdateState = { ...appUpdateState, status: 'failed', percent: 0, message: error.message || String(error) };
                throw error;
            } finally {
                if (listener) await listener.remove().catch(() => {});
            }
            appUpdateState = {
                ...update,
                status: 'ready',
                percent: 100,
                message: '下载完成，点击安装更新',
                downloadedPath: result.path,
                showPrompt: true
            };
            return {
                ok: true,
                update: appUpdateState
            };
        },
        restart_update: async () => {
            const path = appUpdateState.downloadedPath || window.appState.update.downloadedPath;
            if (!path) return { ok: false, error: '没有已下载的更新' };
            return pluginCall('installAppUpdate', { path });
        },
        defer_update_restart: async () => ({ ok: true, update: { status: 'idle' } }),
        dismiss_update_prompt: async () => {
            appUpdateState = { ...appUpdateState, showPrompt: false };
            return { update: appUpdateState };
        },
        ignore_update_version: async version => {
            localPreferences.set('ignored-app-update', version);
            appUpdateState = { ...appUpdateState, showPrompt: false };
            return { ok: true, update: appUpdateState };
        },
        resolve_close_action: async () => ({ ok: true })
    };

    window.clearAndroidService = async () => {
        eventStreamGeneration += 1;
        eventStreamAbortController?.abort();
        eventStreamAbortController = null;
        await pluginCall('clearServiceConfig');
        serviceConfig = { baseUrl: DEFAULT_ANDROID_SERVICE_URL, bearerToken: '' };
        eventStreamPromise = null;
        localStorage.removeItem(EVENT_CURSOR_KEY);
        localStorage.removeItem(EVENT_EPOCH_KEY);
        return { ok: true };
    };
    window.pywebview = { api };
    window.addEventListener('DOMContentLoaded', () => { void hydrateServiceForm(); }, { once: true });
    void loadServiceConfig().then(() => startEventStream());
})();
