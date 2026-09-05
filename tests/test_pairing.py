from __future__ import annotations

import hashlib
import tempfile
import unittest
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.asgi import create_app
from backend.service import MAX_TASK_HISTORY, EventBus, HeadlessService, ServiceConfig


PROJECT_ROOT = Path(__file__).parents[1]


class PairingTests(unittest.TestCase):
    def test_events_are_isolated_by_device_owner_and_reset_stale_cursors(self) -> None:
        events = EventBus()
        first = events.publish({"type": "task_failed", "requestId": "request-a"}, "device:a")
        events.publish({"type": "task_failed", "requestId": "request-b"}, "device:b")

        device_a, cursor_a = events.wait_since("device:a", 0, timeout=0.01)
        device_b, cursor_b = events.wait_since("device:b", 0, timeout=0.01)

        self.assertEqual(first, {"eventId": 1, "type": "task_failed", "requestId": "request-a"})
        self.assertEqual([event["requestId"] for event in device_a], ["request-a"])
        self.assertEqual([event["requestId"] for event in device_b], ["request-b"])
        self.assertEqual(cursor_a, 2)
        self.assertEqual(cursor_b, 2)
        self.assertEqual(events.normalize_cursor(999), 0)
        self.assertNotIn("_owner", device_a[0])

    def test_android_native_storage_uses_database_dcim_and_ime_insets(self) -> None:
        plugin_path = (
            PROJECT_ROOT
            / "mobile"
            / "android"
            / "app"
            / "src"
            / "main"
            / "java"
            / "cn"
            / "easyapitool"
            / "mobile"
            / "AndroidStoragePlugin.java"
        )
        plugin = plugin_path.read_text(encoding="utf-8")
        activity_source = plugin_path.with_name("MainActivity.java").read_text(encoding="utf-8")
        manifest = (
            PROJECT_ROOT / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
        ).read_text(encoding="utf-8")
        android_styles = (
            PROJECT_ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "values" / "styles.xml"
        ).read_text(encoding="utf-8")
        capacitor_config = (PROJECT_ROOT / "mobile" / "capacitor.config.ts").read_text(
            encoding="utf-8"
        )
        bridge = (PROJECT_ROOT / "frontend" / "scripts" / "android-bridge.js").read_text(
            encoding="utf-8"
        )
        viewer = (
            PROJECT_ROOT / "frontend" / "scripts" / "modules" / "05-image-events-sessions.js"
        ).read_text(encoding="utf-8")
        image_controls = (
            PROJECT_ROOT / "frontend" / "scripts" / "modules" / "02-image-controls.js"
        ).read_text(encoding="utf-8")
        initializer = (
            PROJECT_ROOT / "frontend" / "scripts" / "modules" / "07-page-initializer.js"
        ).read_text(encoding="utf-8")
        base_scss = (
            PROJECT_ROOT / "frontend" / "styles" / "modules" / "_base.scss"
        ).read_text(encoding="utf-8")
        page = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        responsive_scss = (
            PROJECT_ROOT / "frontend" / "styles" / "modules" / "_responsive.scss"
        ).read_text(encoding="utf-8")
        asgi = (PROJECT_ROOT / "backend" / "asgi.py").read_text(encoding="utf-8")
        lucide = PROJECT_ROOT / "frontend" / "vendor" / "lucide" / "lucide.min.js"

        self.assertIn('android:allowBackup="false"', manifest)
        self.assertIn('"CREATE TABLE IF NOT EXISTS api_keys ("', plugin)
        self.assertIn("database.upsertKey(keyId, name, encrypt(value));", plugin)
        self.assertIn("getSecureKeyStore().edit().clear().apply();", plugin)
        self.assertNotIn("migrateSecureKeys", plugin)
        self.assertIn('Environment.DIRECTORY_DCIM + "/API_TOOLS"', plugin)
        self.assertIn("resizeOnFullScreen: true", capacitor_config)
        self.assertNotIn("setOnApplyWindowInsetsListener", activity_source)
        self.assertIn('android:windowLayoutInDisplayCutoutMode">shortEdges', android_styles)
        self.assertIn("viewport-fit=cover", page)
        self.assertIn("left: 4px", base_scss)
        self.assertIn("right: 4px", base_scss)
        self.assertIn('html[data-platform="android"] #usageTrendSection', base_scss)
        self.assertNotIn('html[data-platform="android"] #speedPanel', base_scss)
        self.assertIn('html[data-platform="android"] #speedPanel {', responsive_scss)
        self.assertIn('grid-template-rows: minmax(0, 1fr) auto !important;', responsive_scss)
        self.assertIn("viewer.x += event.clientX - previous.x", viewer)
        self.assertIn("viewer.touchPointers.size >= 2", viewer)
        self.assertIn("keyboardWillShow", initializer)
        self.assertNotIn("dataset.promptFocused", initializer)
        self.assertIn("resetKeyboardLayoutScroll", initializer)
        self.assertIn("pageZoomViewport').scrollTop = 0", initializer)
        self.assertIn("dataset.keyboardVisible === 'true'", image_controls)
        self.assertIn("self.service.events.wait_since", asgi)
        self.assertIn("owner,", asgi)
        self.assertIn('"X-Event-Epoch": self.service.events.epoch', asgi)
        self.assertIn('data-keyboard-visible="true"', responsive_scss)
        self.assertIn("z-index: 70", responsive_scss)
        self.assertIn("z-index: 100", responsive_scss)
        self.assertIn("display: none !important", responsive_scss)
        self.assertNotIn("box-shadow: 12px 0 24px", responsive_scss)
        self.assertEqual(lucide.stat().st_size, 357796)
        self.assertEqual(
            hashlib.sha256(lucide.read_bytes()).hexdigest(),
            "3411692820cb8d47543f69496aa25fd603a358f4498046f41c508a5a3342210e",
        )
        self.assertIn("async function exportImageSets(selected)", bridge)
        self.assertIn("const localSets = await listLocalImageSets();", bridge)

    def test_android_branding_uses_generated_mirra_assets(self) -> None:
        config = (PROJECT_ROOT / "mobile" / "capacitor.config.ts").read_text(encoding="utf-8")
        strings = (
            PROJECT_ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "values" / "strings.xml"
        ).read_text(encoding="utf-8")
        manifest = (
            PROJECT_ROOT / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
        ).read_text(encoding="utf-8")
        resources = PROJECT_ROOT / "mobile" / "resources" / "icon.png"
        adaptive = (
            PROJECT_ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "mipmap-anydpi-v26" / "ic_launcher.xml"
        ).read_text(encoding="utf-8")

        self.assertIn("appName: 'Mirra(觅然)'", config)
        self.assertIn('<string name="app_name">Mirra(觅然)</string>', strings)
        self.assertIn('<string name="title_activity_main">Mirra(觅然)</string>', strings)
        self.assertIn('android:icon="@mipmap/ic_launcher"', manifest)
        self.assertTrue(resources.is_file())
        self.assertIn("mipmap/ic_launcher_foreground", adaptive)

    def test_android_generation_restores_current_key_before_request(self) -> None:
        bridge = (PROJECT_ROOT / "frontend" / "scripts" / "android-bridge.js").read_text(
            encoding="utf-8"
        )
        generation_start = bridge.index("async function generateImage")
        restore_key = bridge.index(
            "await registerKey({ id: String(keyId), name: 'API Key' });",
            generation_start,
        )
        request_start = bridge.index("await requestJson('/api/v1/image-generations'", generation_start)

        self.assertLess(restore_key, request_start)

    def test_android_generation_persists_failures_and_uses_automatic_binding(self) -> None:
        bridge = (PROJECT_ROOT / "frontend" / "scripts" / "android-bridge.js").read_text(
            encoding="utf-8"
        )

        page = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn("async function ensureDeviceRegistration(preferredKeyId = '')", bridge)
        self.assertIn("/device/register", bridge)
        self.assertNotIn("androidServiceToken", page)
        self.assertNotIn("androidServicePairingCode", page)
        self.assertIn("function persistFailedGeneration(options, prompt, error, pending = null)", bridge)
        self.assertIn("persistLocalSet(result, pending);", bridge)
        self.assertIn("if (requestId && !pending) return;", bridge)
        self.assertIn("EVENT_EPOCH_KEY", bridge)
        self.assertIn("async function pollTaskState(requestId)", bridge)
        self.assertIn("void pollTaskState(requestId);", bridge)
        self.assertIn("function normalizeRequestId(value)", bridge)
        self.assertIn("eventStreamAbortController?.abort();", bridge)
        self.assertIn("function migrateLocalImageHistory()", bridge)
        self.assertIn("record?.status !== 'failed'", bridge)
        self.assertIn("window.applyImageGenerationEvent?.({ ...failed, type: 'set_completed' });", bridge)

    def test_valid_provider_key_automatically_registers_signed_device_token(self) -> None:
        payload = {"isValid": True, "quota": {"limit": 100, "used": 1, "remaining": 99}}
        provider = SimpleNamespace(fetch=lambda _base_url, _secret: (payload, ["gpt-image-2"]))
        with tempfile.TemporaryDirectory() as temporary:
            service = HeadlessService(
                ServiceConfig(work_root=Path(temporary), bearer_token="x" * 32),
                client=provider,
            )
            client = TestClient(create_app(service))
            registration = client.post(
                "/api/v1/device/register",
                json={
                    "deviceId": "device-1234567890abcdef",
                    "keyId": "phone-key",
                    "name": "Phone",
                    "value": "provider-secret",
                },
            )

            token = registration.json()["bearerToken"]
            state = client.get(
                "/api/v1/state",
                headers={"Authorization": f"Bearer {token}"},
            )
            tampered = client.get(
                "/api/v1/state",
                headers={"Authorization": f"Bearer {token[:-1]}x"},
            )

        self.assertEqual(registration.status_code, 200)
        self.assertTrue(token.startswith("device-v1."))
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["keys"][0]["id"], "phone-key")
        self.assertEqual(tampered.status_code, 401)

    def test_device_state_and_keys_are_isolated_by_owner(self) -> None:
        payload = {"isValid": True, "quota": {"limit": 10, "remaining": 10}}
        provider = SimpleNamespace(fetch=lambda _base_url, _secret: (payload, []))
        with tempfile.TemporaryDirectory() as temporary:
            service = HeadlessService(
                ServiceConfig(work_root=Path(temporary), bearer_token="x" * 32),
                client=provider,
            )
            token_a = service.register_device(
                "device-a-1234567890",
                "key-a",
                "Device A",
                "secret-a",
            )["bearerToken"]
            token_b = service.register_device(
                "device-b-1234567890",
                "key-b",
                "Device B",
                "secret-b",
            )["bearerToken"]
            client = TestClient(create_app(service))

            state_a = client.get("/api/v1/state", headers={"Authorization": f"Bearer {token_a}"})
            state_b = client.get("/api/v1/state", headers={"Authorization": f"Bearer {token_b}"})
            foreign_generation = client.post(
                "/api/v1/image-generations",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"keyId": "key-a", "prompt": "test", "options": {}},
            )

        self.assertEqual([key["id"] for key in state_a.json()["keys"]], ["key-a"])
        self.assertEqual([key["id"] for key in state_b.json()["keys"]], ["key-b"])
        self.assertEqual(foreign_generation.status_code, 404)

    def test_duplicate_request_id_cannot_reassign_task_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = HeadlessService(
                ServiceConfig(work_root=Path(temporary), bearer_token="x" * 32)
            )
            service.add_key("device:a", "A", "secret-a", key_id="key-a")
            service.add_key("device:b", "B", "secret-b", key_id="key-b")
            service.task_owners["shared-request"] = "device:a"

            with self.assertRaisesRegex(ValueError, "任务编号重复"):
                service.create_generation(
                    "device:b",
                    "key-b",
                    "test",
                    [],
                    {"requestId": "shared-request"},
                )

        self.assertEqual(service.task_owners["shared-request"], "device:a")

    def test_request_ids_are_not_silently_truncated_and_history_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = HeadlessService(
                ServiceConfig(work_root=Path(temporary), bearer_token="x" * 32)
            )
            service.add_key("device:a", "A", "secret-a", key_id="key-a")

            with self.assertRaisesRegex(ValueError, "任务编号无效"):
                service.create_generation(
                    "device:a",
                    "key-a",
                    "test",
                    [],
                    {"requestId": "x" * 81},
                )

            for index in range(MAX_TASK_HISTORY + 5):
                request_id = f"finished-{index}"
                service.task_owners[request_id] = "device:a"
                service.task_states[request_id] = {"requestId": request_id}
            with service.task_lock:
                service._trim_task_history_locked()

        self.assertEqual(len(service.task_owners), MAX_TASK_HISTORY)
        self.assertEqual(len(service.task_states), MAX_TASK_HISTORY)

    def test_active_generation_key_cannot_be_rebound_or_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = HeadlessService(
                ServiceConfig(work_root=Path(temporary), bearer_token="x" * 32)
            )
            service.add_key("device:a", "A", "secret-a", key_id="key-a")
            service.task_keys["running-request"] = "key-a"

            with self.assertRaisesRegex(ValueError, "API Key 正在生成图片"):
                service.add_key("device:b", "B", "secret-b", key_id="key-a")
            deleted = service.delete_key("device:a", "key-a")

        self.assertFalse(deleted["ok"])
        self.assertEqual(deleted["error"], "API Key 正在生成图片")
        self.assertEqual(service.credentials.get_secret("key-a"), "secret-a")

    def test_active_generation_key_can_be_registered_again_by_its_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = HeadlessService(
                ServiceConfig(work_root=Path(temporary), bearer_token="x" * 32)
            )
            service.add_key("device:a", "A", "secret-a", key_id="key-a")
            service.task_keys["running-request"] = "key-a"

            repeated = service.add_key(
                "device:a",
                "A",
                "secret-a",
                key_id="key-a",
            )
            with self.assertRaisesRegex(ValueError, "API Key 正在生成图片"):
                service.add_key(
                    "device:a",
                    "Changed",
                    "secret-a",
                    key_id="key-a",
                )

        self.assertTrue(repeated["ok"])
        self.assertEqual(repeated["keyId"], "key-a")
        self.assertEqual(service.credentials.get_secret("key-a"), "secret-a")

    def test_selected_aspect_ratio_is_appended_to_generation_prompt(self) -> None:
        events = (
            PROJECT_ROOT / "frontend" / "scripts" / "modules" / "05-image-events-sessions.js"
        ).read_text(encoding="utf-8")
        controls = (
            PROJECT_ROOT / "frontend" / "scripts" / "modules" / "02-image-controls.js"
        ).read_text(encoding="utf-8")

        self.assertIn("const aspectRatio = window.imageEditState.aspectRatio;", events)
        self.assertIn(
            "suffixes.push(`以以下比例要求为准：强制生成比例为${aspectRatio}的图片`)",
            controls,
        )
        self.assertIn("suffixes.push('以以下透明度要求为准：强制生成透明背景的图片')", controls)
        self.assertIn("suffixes.push('以以下透明度要求为准：强制生成不透明背景的图片')", controls)
        self.assertIn("const constraintSuffixes = imageGenerationConstraintSuffixes(aspectRatio, transparency);", events)
        self.assertIn("? `${prompt}\\n${constraintSuffixes.join('\\n')}`", events)
        self.assertIn("transparency,", events)
        self.assertIn("createImageGenerationSet(requestId, imageCount, finalPrompt", events)
        self.assertIn("activeKey.id,\n                    finalPrompt,", events)

    def test_android_failed_continuation_keeps_the_next_round_number(self) -> None:
        events = (
            PROJECT_ROOT / "frontend" / "scripts" / "modules" / "05-image-events-sessions.js"
        ).read_text(encoding="utf-8")
        request_options_start = events.index("const requestOptions = {")
        request_options_end = events.index("};", request_options_start)
        request_options = events[request_options_start:request_options_end]

        self.assertIn("roundNumber: session ? session.roundNumber + 1 : 1", request_options)

    def test_pairing_code_is_consumed_after_one_successful_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work_root = Path(temporary)
            service = HeadlessService(
                ServiceConfig(work_root=work_root, bearer_token="x" * 32)
            )
            pairing_path = work_root / "pairing-code"
            pairing_path.write_text("a" * 32, encoding="ascii")
            pairing_path.chmod(0o600)

            result = service.pair_device("a" * 32)

            self.assertTrue(result["ok"])
            self.assertEqual(result["bearerToken"], "x" * 32)
            self.assertFalse(pairing_path.exists())
            with self.assertRaises(PermissionError):
                service.pair_device("a" * 32)

    def test_pairing_code_rejects_insecure_file_permissions(self) -> None:
        if os.name != "posix":
            self.skipTest("Windows does not expose POSIX pairing-file mode bits")
        with tempfile.TemporaryDirectory() as temporary:
            work_root = Path(temporary)
            service = HeadlessService(
                ServiceConfig(work_root=work_root, bearer_token="x" * 32)
            )
            pairing_path = work_root / "pairing-code"
            pairing_path.write_text("a" * 32, encoding="ascii")
            pairing_path.chmod(0o644)

            with self.assertRaises(PermissionError):
                service.pair_device("a" * 32)

    def test_pairing_api_consumes_code_and_rejects_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work_root = Path(temporary)
            service = HeadlessService(
                ServiceConfig(work_root=work_root, bearer_token="x" * 32)
            )
            pairing_path = work_root / "pairing-code"
            pairing_path.write_text("a" * 32, encoding="ascii")
            pairing_path.chmod(0o600)
            client = TestClient(create_app(service))

            rejected = client.post("/api/v1/pair", json={"pairingCode": "b" * 32})
            paired = client.post("/api/v1/pair", json={"pairingCode": "a" * 32})
            replayed = client.post("/api/v1/pair", json={"pairingCode": "a" * 32})

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(paired.status_code, 200)
        self.assertEqual(paired.json()["bearerToken"], "x" * 32)
        self.assertEqual(replayed.status_code, 401)

    def test_android_refresh_returns_complete_quota_state(self) -> None:
        payload = {
            "isValid": True,
            "quota": {"limit": 100, "used": 25, "remaining": 75},
            "usage": {
                "today": {"cost": 1.25, "requests": 3},
                "total": {"cost": 25, "requests": 40},
            },
            "rate_limits": [
                {"window": "5h", "limit": 10, "used": 2, "remaining": 8},
            ],
        }
        provider = SimpleNamespace(fetch=lambda _base_url, _secret: (payload, ["gpt-image-2"]))
        with tempfile.TemporaryDirectory() as temporary:
            service = HeadlessService(
                ServiceConfig(work_root=Path(temporary), bearer_token="x" * 32),
                client=provider,
            )
            service.add_key("paired-device", "Phone", "provider-secret", key_id="phone-key")
            client = TestClient(create_app(service))

            response = client.post(
                "/api/v1/refresh",
                headers={"Authorization": f"Bearer {'x' * 32}"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["valid"])
        self.assertEqual(result["refreshed"], ["phone-key"])
        key = result["state"]["keys"][0]
        self.assertEqual(key["status"], "active")
        self.assertEqual(key["totalQuota"], 100)
        self.assertEqual(key["remainingQuota"], 75)
        self.assertEqual(key["todayCost"], 1.25)
        self.assertEqual(key["totalCost"], 25)
        self.assertEqual(key["win5h"]["remaining"], 8)
        self.assertEqual(key["models"], ["gpt-image-2"])