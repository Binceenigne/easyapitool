(function initializeAndroidBridge() {
    const capacitor = window.Capacitor;
    const native = capacitor?.Plugins?.AndroidStorage;
    const isAndroid = Boolean(native) && (
        capacitor?.getPlatform?.() === 'android' ||
        capacitor?.getPlatform?.() === undefined
    );
    if (!isAndroid || window.pywebview?.api) return;

    document.documentElement.dataset.platform = 'android';
    const DEFAULT_ANDROID_SERVICE_URL = 'https://imggen.djyx.me/v1';
    const ANDROID_UPDATE_SERVICE_URL = 'https://imggen.djyx.me/v1';
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
    const downloadCache = new Map();
    let eventStreamPromise = null;
    let eventStreamGeneration = 0;
    let eventStreamAbortController = null;
    let appUpdateState = { status: 'idle', percent: 0, message: '尚未检查服务器更新' };
    let serviceConfig = {
        baseUrl: String(window.__EASYAPITOOL_SERVICE_URL__ || DEFAULT_ANDROID_SERVICE_URL).trim().replace(/\/$/, ''),
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

    async function loadServiceConfig() {
        if (serviceConfig.baseUrl && isDeviceToken(serviceConfig.bearerToken)) {
            serviceConfigError = '';
            return serviceConfig;
        }
        if (serviceConfigLoad) return serviceConfigLoad;
        serviceConfigLoad = pluginCall('readServiceConfig')
            .then(config => {
                const savedBaseUrl = String(config.serviceUrl || '').trim().replace(/\/$/, '');
                const savedBearerToken = String(config.bearerToken || '').trim();
                if (/^https:\/\/clife\.djyx\.me(?:\/|$)/i.test(savedBaseUrl)) {
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
            baseUrl: serviceConfig.baseUrl || String(localPreferences.get('service-url', DEFAULT_ANDROID_SERVICE_URL)).replace(/\/$/, ''),
            bearerToken: serviceConfig.bearerToken
        };
    }

    function publicServiceUrl(path, query = '') {
        const baseUrl = ANDROID_UPDATE_SERVICE_URL.replace(/\/$/, '');
        return `${baseUrl}/${String(path || '').replace(/^\//, '')}${query}`;
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
            cache: 'no-store'
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
        return /^https?:\/\//i.test(String(path || ''))
            ? String(path)
            : `${baseUrl}${cleanPath}`;
    }

    async function requestJson(path, options = {}, retryAuthentication = true) {
        const settings = requireService();
        const response = await fetch(resolveUrl(path), {
            ...options,
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

    async function ensureDeviceRegistration(preferredKeyId = '') {
        await loadServiceConfig();
        if (serviceConfig.bearerToken) return serviceConfig;
        if (deviceRegistrationPromise) return deviceRegistrationPromise;
        deviceRegistrationPromise = (async () => {
            const metadata = await secureKeyMetadata();
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
        const metadata = await secureKeyMetadata();
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

    async function getState() {
        await loadServiceConfig();
        const metadata = await secureKeyMetadata();
        if (!serviceConfig.bearerToken && metadata.length) {
            await ensureDeviceRegistration(metadata[0].id).catch(() => {});
        }
        let remote = {};
        let registrationErrors = [];
        try {
            ({ errors: registrationErrors } = await registerAllKeys());
            remote = await requestJson('/api/v1/state');
        } catch (error) {
            registrationErrors = [error];
        }
        const remoteKeys = new Map((remote.keys || []).map(key => [String(key.id), key]));
        return {
            ...remote,
            keys: metadata.map(key => ({
                id: String(key.id),
                name: key.name || 'API Key',
                value: maskedKeyValue(),
                baseUrl: '',
                status: remoteKeys.get(String(key.id))?.status || (registrationErrors.length ? 'unknown' : 'active'),
                lastError: registrationErrors.length ? registrationErrors[0].message : null
            })),
            appName: 'Mirra(觅然)',
            appVersion: (await getAppUpdateInfo().catch(() => ({ versionName: 'android' }))).versionName || 'android',
            storageMode: 'android-filesystem-sqlite-index',
            serverPersistence: false,
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
            createdAt: result.createdAt || pending?.createdAt || new Date().toISOString(),
            status: result.cancelled ? 'cancelled' : result.ok ? 'completed' : 'failed',
            error: String(result.error || ''),
            requestedCount: Number(result.requestedCount || pending?.requestedCount || 1),
            items: (result.items || []).map((item, itemIndex) => ({
                itemIndex: Number(item.itemIndex ?? itemIndex),
                ok: item.ok === true,
                status: item.ok === true ? 'completed' : item.status || 'failed',
                error: item.error || '',
                resourceId: String(item.resourceId || ''),
                previewResourceId: String(item.previewResourceId || ''),
                width: Number(item.width) || 0,
                height: Number(item.height) || 0,
                format: item.format || ''
            }))
        };
        const records = localPreferences.get(IMAGE_SETS_KEY, []).filter(item => item?.setId !== setId);
        records.unshift(record);
        localPreferences.set(IMAGE_SETS_KEY, records.slice(0, 100));
    }

    async function listLocalImageSets() {
        const records = localPreferences.get(IMAGE_SETS_KEY, []);
        const assets = await pluginCall('listAssets');
        const assetsById = new Map((assets.assets || []).map(asset => [String(asset.resourceId), asset]));
        return records.map(record => ({
            ...record,
            history: true,
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
                });
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
                    mimeType: 'image/jpeg',
                    preview: true
                });
                downloadCache.set(cacheKey, downloaded);
            }
            const local = await downloaded;
            result.previewPath = local.path;
            result.previewUri = localImageUrl(local.path);
        }
        for (const [key, item] of Object.entries(result)) {
            if (!['path', 'uri', 'previewPath', 'previewUri', 'downloadUrl', 'previewUrl'].includes(key)) {
                result[key] = await hydrateAsset(item, context);
            }
        }
        return result;
    }

    async function handleServerEvent(event) {
        if (!event || typeof event !== 'object') return;
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
                new Error(event.error || '图片生成失败'),
                pending
            );
            window.applyImageGenerationEvent?.({ ...failed, type: 'set_completed' });
            if (pending) {
                pendingTasks.delete(requestId);
                pending.failurePersisted = true;
                pending.reject(new Error(event.error || '图片生成失败'));
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
            createdAt: pending?.createdAt || new Date().toISOString(),
            requestedCount,
            error: message,
            items: Array.from({ length: requestedCount }, (_, itemIndex) => ({
                itemIndex,
                ok: false,
                status: 'failed',
                error: message
            }))
        };
        persistLocalSet(result, pending);
        return result;
    }

    async function generateImage(keyId, prompt, paths, options = {}) {
        const requestId = normalizeRequestId(options.requestId || newRequestId());
        if (pendingTasks.has(requestId)) throw new Error('同一任务正在生成');
        options = { ...options, requestId };
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
        const dataUrl = await readAsDataUrl(path);
        const response = await fetch(dataUrl);
        const blob = await response.blob();
        if (!navigator.clipboard?.write || !window.ClipboardItem) return { ok: false, error: '当前设备不支持图片剪贴板' };
        await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
        return { ok: true };
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
        add_key: async (name, value) => {
            const keyId = newRequestId('android-key');
            await pluginCall('saveSecureKey', { keyId, name, value, baseUrl: '' });
            try {
                await ensureDeviceRegistration(keyId);
                await registerKey({ id: keyId, name });
            } catch (error) {
                throw new Error(`密钥已安全保存，但服务注册失败：${error.message}`);
            }
            return { ok: true, activeKeyId: keyId, state: await getState() };
        },
        delete_key: async id => {
            try { await requestJson(`/api/v1/keys/${encodeURIComponent(id)}`, { method: 'DELETE' }); } catch {}
            await pluginCall('deleteSecureKey', { keyId: id });
            return { state: await getState() };
        },
        choose_edit_images: chooseEditImages,
        import_reference_image: importReferenceImage,
        generate_image: generateImage,
        cancel_image_generation: requestId => requestJson(`/api/v1/image-generations/${encodeURIComponent(requestId)}/cancel`, { method: 'POST' }),
        load_generated_image: async path => ({ ok: true, path, dataUrl: localImageUrl(path) }),
        save_edited_image: saveEditedImage,
        copy_generated_image: copyGeneratedImage,
        list_image_sets: async () => ({ ok: true, sets: await listLocalImageSets(), source: 'android-local' }),
        delete_image_set: deleteImageSet,
        open_generated_pictures: async () => ({ ok: true }),
        export_image_sets: exportImageSets,
        polish_prompt: async (keyId, prompt) => requestJson('/api/v1/prompts/polish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keyId: String(keyId), prompt })
        }),
        append_image_stream_debug: async () => ({ ok: true }),
        report_startup: async () => ({ ok: true }),
        refresh_now: async () => {
            await ensureDeviceRegistration();
            await registerAllKeys();
            return requestJson('/api/v1/refresh', { method: 'POST' });
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
            const update = await checkAndroidUpdate();
            appUpdateState = update;
            return { ok: true, update };
        },
        download_update: async () => {
            const update = await checkAndroidUpdate();
            if (!update.available) {
                appUpdateState = update;
                return { ok: true, update };
            }
            const release = update.release || {};
            const result = await pluginCall('downloadAppUpdate', {
                endpoint: release.downloadUrl,
                sha256: release.sha256,
                sizeBytes: Number(release.downloadSize) || 0
            });
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
        dismiss_update_prompt: async () => ({ update: { status: 'idle' } }),
        ignore_update_version: async () => ({ ok: true, update: { status: 'idle' } }),
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