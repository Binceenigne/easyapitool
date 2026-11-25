const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

const bridge = readFileSync(process.env.MIRRA_ANDROID_BRIDGE
    || join(__dirname, '../frontend/scripts/android-bridge.js'), 'utf8');

test('saved Mirra keys use the new device gateway and custom API keys stay direct', async () => {
    const legacyOrigins = ['https://imggen.djyx.me', 'https://39.105.74.222', 'https://39.105.74.222:8443'];
    const managed = legacyOrigins.map((origin, index) => ({
        id: `saved-${index}`, name: `Mirra ${index}`, baseUrl: `${origin}/openai/v1/`, distributed: true
    }));
    const direct = { id: 'custom-key', name: 'Custom API', baseUrl: 'https://custom.example/v1', distributed: false };
    const deviceToken = 'device-v1.fixture';
    const canonical = 'https://imggen.djyx.pw/v1';
    const calls = [];
    const registrations = [];
    const directCalls = [];
    const serviceWrites = [];
    const storage = new Map();
    const window = {
        Capacitor: { getPlatform: () => 'android', Plugins: { AndroidStorage: {
            readServiceConfig: async () => ({ serviceUrl: `${legacyOrigins[1]}/v1`, bearerToken: deviceToken }),
            saveServiceConfig: async config => { serviceWrites.push(config); return { ok: true }; },
            listSecureKeys: async () => ({ keys: [...managed, direct] }),
            readSecureKey: async ({ keyId }) => ({ name: keyId, value: `sk-mirra-test-${keyId}` }),
            requestDirectApi: async request => { directCalls.push(request); return { data: [{ id: 'custom-model' }] }; },
            getAppUpdateInfo: async () => ({ versionName: '1.3.9', versionCode: 39, buildTimestampMs: 1 })
        } } },
        addEventListener() {}
    };
    const ok = body => ({ ok: true, json: async () => body });
    runInNewContext(bridge, {
        window,
        document: { documentElement: { dataset: {} }, getElementById: () => null },
        localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
        URL, URLSearchParams, AbortController, AbortSignal, setTimeout, clearTimeout,
        fetch: async (address, options) => {
            calls.push({ address, options });
            const { pathname } = new URL(address);
            if (pathname === '/v1/events') return new Promise(() => {});
            if (pathname === '/v1/keys') {
                registrations.push(JSON.parse(options.body));
                return ok({ ok: true });
            }
            if (pathname === '/v1/state') return ok({ keys: managed.map(key => ({ id: key.id, status: 'active' })) });
            if (pathname === '/v1/app/update') return ok({ available: true });
            throw new Error(`Unexpected URL: ${address}`);
        }
    });

    const state = await window.pywebview.api.get_state();
    const update = await window.pywebview.api.check_for_updates();
    assert.deepEqual(registrations.map(key => key.keyId), managed.map(key => key.id));
    assert.deepEqual(directCalls.map(call => call.keyId), [direct.id]);
    assert.ok(state.keys.every(key => key.status === 'active'));
    assert.equal(state.keys.find(key => key.id === direct.id).directApi, true);
    assert.equal(serviceWrites.length, 1);
    assert.equal(serviceWrites[0].serviceUrl, canonical);
    assert.equal(serviceWrites[0].bearerToken, deviceToken);
    assert.equal(update.update.release.downloadUrl, canonical + '/app/update/download?platform=android');
    assert.ok(calls.every(call => call.address.startsWith(canonical + '/')));
    assert.equal(calls.find(call => call.address.includes('/events?')).options.headers.Authorization, `Bearer ${deviceToken}`);
});
