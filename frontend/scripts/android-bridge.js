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
    const IMAGE_SETS_KEY = 'image-sets';
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
            try { localStorage.setItem(`api-tools-android-${key}`, JSON.stringify(value)); } catch {}
        }
    };
    const eventQueue = [];
    const pendingTasks = new Map();
    const downloadCache = new Map();
    let eventStreamPromise = null;
    let appUpdateState = { status: 'idle', percent: 0, message: '尚未检查服务器更新' };
    let serviceConfig = {
        baseUrl: String(window.__EASYAPITOOL_SERVICE_URL__ || DEFAULT_ANDROID_SERVICE_URL).trim().replace(/\/$/, ''),
        bearerToken: String(window.__EASYAPITOOL_SERVICE_TOKEN__ || '').trim()
    };
    let serviceConfigLoad = null;

    async function loadServiceConfig() {
        if (serviceConfig.baseUrl && serviceConfig.bearerToken) return serviceConfig;
        if (serviceConfigLoad) return serviceConfigLoad;
        serviceConfigLoad = pluginCall('readServiceConfig')
            .then(config => {
                const savedBaseUrl = String(config.serviceUrl || '').trim().replace(/\/$/, '');
                if (/^https:\/\/clife\.djyx\.me(?:\/|$)/i.test(savedBaseUrl)) {
                    void pluginCall('clearServiceConfig').catch(() => {});
                    serviceConfig = { baseUrl: DEFAULT_ANDROID_SERVICE_URL, bearerToken: '' };
                    return serviceConfig;
                }
                serviceConfig = {
                    baseUrl: savedBaseUrl || DEFAULT_ANDROID_SERVICE_URL,
                    bearerToken: String(config.bearerToken || '').trim()
                };
                return serviceConfig;
            })
            .catch(() => {
                serviceConfig = { baseUrl: DEFAULT_ANDROID_SERVICE_URL, bearerToken: '' };
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
        const address = document.getElementById('androidServiceUrl');
        if (address) address.value = config.baseUrl;
        setServiceStatus(
            config.baseUrl && config.bearerToken ? '已安全保存设备连接' : '尚未完成服务配对',
            false
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
        if (!settings.bearerToken) throw new Error('尚未配置设备访问令牌');
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

    async function requestJson(path, options = {}) {
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
        if (!response.ok) {
            throw new Error(body.detail || body.error || `HTTP ${response.status}`);
        }
        return body;
    }

    async function pairService(serviceUrl, pairingCode) {
        const baseUrl = String(serviceUrl || '').trim().replace(/\/$/, '');
        if (!baseUrl.startsWith('https://')) throw new Error('服务地址必须使用 HTTPS');
        const response = await fetch(`${baseUrl}/pair`, {
            method: 'POST',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ pairingCode: String(pairingCode || '').trim() })
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || !body.bearerToken) {
            throw new Error(body.detail || '配对码无效或已过期');
        }
        return body.bearerToken;
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

    async function registerKey(metadata) {
        await loadServiceConfig();
        const secret = await readSecureKey(metadata.id);
        return requestJson('/api/v1/keys', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                keyId: metadata.id,
                name: secret.name || metadata.name || 'API Key',
                value: secret.value
            })
        });
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
            appName: 'DJYX_APITOOL Android',
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
        if (event.type === 'task_result') {
            const result = await hydrateAsset(event.result || {}, {
                sessionId: event.sessionId,
                setId: event.setId || event.requestId
            });
            const requestId = String(event.requestId || result.requestId || '');
            const pending = pendingTasks.get(requestId);
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
            const requestId = String(event.requestId || '');
            const pending = pendingTasks.get(requestId);
            if (pending) {
                pendingTasks.delete(requestId);
                pending.reject(new Error(event.error || '图片生成失败'));
            }
            return;
        }
        const pending = pendingTasks.get(String(event.requestId || ''));
        const hydrated = await hydrateAsset({
            ...event,
            sessionId: pending?.sessionId || event.sessionId,
            setId: pending?.setId || event.setId
        }, {
            sessionId: event.sessionId,
            setId: event.setId || event.requestId
        });
        window.applyImageGenerationEvent?.(hydrated);
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
        eventStreamPromise = (async () => {
            while (true) {
                try {
                    const settings = requireService();
                    const cursor = Number(localStorage.getItem(EVENT_CURSOR_KEY) || 0);
                    const response = await fetch(resolveUrl(`/api/v1/events?cursor=${cursor}`), {
                        headers: { Authorization: `Bearer ${settings.bearerToken}`, Accept: 'text/event-stream' },
                        cache: 'no-store'
                    });
                    if (!response.ok) throw new Error(`SSE HTTP ${response.status}`);
                    await readSseResponse(response);
                } catch (error) {
                    if (error.message.includes('尚未配置')) return;
                    await new Promise(resolve => setTimeout(resolve, 2000));
                }
            }
        })();
        eventStreamPromise.catch(() => {});
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
        return new Promise((resolve, reject) => pendingTasks.set(String(requestId), { resolve, reject, ...metadata }));
    }

    async function generateImage(keyId, prompt, paths, options = {}) {
        await loadServiceConfig();
        const settings = requireService();
        try {
            await registerKey({ id: String(keyId), name: 'API Key' });
        } catch (error) {
            throw new Error(`无法恢复设备密钥：${error.message || String(error)}`);
        }
        startEventStream();
        const references = [];
        for (const path of paths || []) references.push(await uploadReference(path));
        const requestId = String(options.requestId || newRequestId());
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
        try {
            await requestJson('/api/v1/image-generations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    keyId: String(keyId),
                    prompt,
                    referenceIds: references.map(reference => reference.resourceId),
                    options: { ...options, requestId }
                })
            });
            return await resultPromise;
        } catch (error) {
            pendingTasks.delete(requestId);
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

    const api = {
        get_state: getState,
        add_key: async (name, value) => {
            const keyId = newRequestId('android-key');
            await pluginCall('saveSecureKey', { keyId, name, value, baseUrl: '' });
            try {
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
        export_image_sets: async selected => {
            let exported = 0;
            for (const set of selected || []) {
                for (const item of set.items || []) {
                    if (item.path && item.status === 'completed') { await saveEditedImage(item.path); exported++; }
                }
            }
            return { ok: true, exported, skippedSets: [] };
        },
        polish_prompt: async (keyId, prompt) => requestJson('/api/v1/prompts/polish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keyId: String(keyId), prompt })
        }),
        append_image_stream_debug: async () => ({ ok: true }),
        report_startup: async () => ({ ok: true }),
        refresh_now: async () => ({ ok: true, valid: true, state: await getState(), refreshed: [], failed: [] }),
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

    window.configureAndroidService = async (serviceUrl, bearerToken) => {
        await pluginCall('saveServiceConfig', { serviceUrl, bearerToken });
        serviceConfig = {
            baseUrl: String(serviceUrl || '').trim().replace(/\/$/, ''),
            bearerToken: String(bearerToken || '').trim()
        };
        eventStreamPromise = null;
        startEventStream();
        return { ok: true };
    };
    window.saveAndroidServiceConfig = async () => {
        const address = document.getElementById('androidServiceUrl');
        const token = document.getElementById('androidServiceToken');
        const pairingCode = document.getElementById('androidServicePairingCode');
        const button = document.getElementById('saveAndroidServiceButton');
        const serviceUrl = String(address?.value || '').trim();
        let bearerToken = String(token?.value || '').trim();
        const requestedPairingCode = String(pairingCode?.value || '').trim();
        if (!serviceUrl || (!bearerToken && !requestedPairingCode)) {
            setServiceStatus('请输入服务地址，以及设备令牌或一次性配对码', true);
            return;
        }
        if (button) button.disabled = true;
        try {
            if (!bearerToken) bearerToken = await pairService(serviceUrl, requestedPairingCode);
            await window.configureAndroidService(serviceUrl, bearerToken);
            if (token) token.value = '';
            if (pairingCode) pairingCode.value = '';
            setServiceStatus('已安全保存设备连接');
            const state = await window.pywebview.api.get_state();
            window.applyBackendState?.(state);
            window.showToast?.('服务连接已保存');
        } catch (error) {
            setServiceStatus(error.message || '服务连接保存失败', true);
        } finally {
            if (button) button.disabled = false;
        }
    };
    window.clearAndroidService = async () => {
        await pluginCall('clearServiceConfig');
        serviceConfig = { baseUrl: DEFAULT_ANDROID_SERVICE_URL, bearerToken: '' };
        eventStreamPromise = null;
        return { ok: true };
    };
    window.pywebview = { api };
    window.addEventListener('DOMContentLoaded', () => { void hydrateServiceForm(); }, { once: true });
    void loadServiceConfig().then(() => startEventStream());
})();