from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from fastapi.testclient import TestClient

from backend.asgi import create_app
from backend.service import HeadlessService, ServiceConfig


PROJECT_ROOT = Path(__file__).parents[1]


class PairingTests(unittest.TestCase):
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