from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app_updates import AndroidReleaseCatalog
from backend.asgi import create_app
from backend.service import HeadlessService, ServiceConfig


class AndroidReleaseTests(unittest.TestCase):
    def _catalog(self, root: Path, *, timestamp: int = 1_700_000_000_000) -> AndroidReleaseCatalog:
        apk = root / "easyapitool-mobile.apk"
        apk.write_bytes(b"test-apk")
        (root / "manifest.json").write_text(
            json.dumps({
                "platform": "android",
                "packageName": "cn.easyapitool.mobile",
                "versionName": "1.0.0",
                "versionCode": 1,
                "buildTimestampMs": timestamp,
                "fileName": apk.name,
                "sizeBytes": apk.stat().st_size,
                "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
                "releaseNotes": "初始版本",
            }),
            encoding="utf-8",
        )
        return AndroidReleaseCatalog(root)

    def test_build_timestamp_can_make_same_version_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = self._catalog(Path(temporary), timestamp=2_000)
            result = catalog.check(current_version_code=1, current_build_timestamp_ms=1_999)
        self.assertTrue(result["available"])
        self.assertEqual(result["latestBuildTimestampMs"], 2_000)

    def test_equal_or_newer_build_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = self._catalog(Path(temporary), timestamp=2_000)
            equal = catalog.check(current_version_code=1, current_build_timestamp_ms=2_000)
            newer = catalog.check(current_version_code=2, current_build_timestamp_ms=1)
        self.assertFalse(equal["available"])
        self.assertFalse(newer["available"])

    def test_api_check_and_download_are_public(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = self._catalog(root)
            service = HeadlessService(ServiceConfig(work_root=root / "service", bearer_token="x" * 32))
            client = TestClient(create_app(service, update_catalog=catalog))

            checked = client.get(
                "/api/v1/app/update",
                params={"platform": "android", "currentVersionCode": 0},
            )
            downloaded = client.get("/api/v1/app/update/download?platform=android")

        self.assertEqual(checked.status_code, 200)
        self.assertTrue(checked.json()["available"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"test-apk")
        self.assertEqual(downloaded.headers["content-type"], "application/vnd.android.package-archive")