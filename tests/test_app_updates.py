from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app_updates import AndroidReleaseCatalog, WindowsReleaseCatalog
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


class WindowsReleaseTests(unittest.TestCase):
    def _catalog(self, root: Path) -> WindowsReleaseCatalog:
        executable = root / "API_TOOLS.exe"
        executable.write_bytes(b"test-executable")
        (root / "manifest.json").write_text(
            json.dumps({
                "platform": "windows",
                "versionName": "1.1.10",
                "versionCode": 10110,
                "buildTimestampMs": 1_800_000_000_000,
                "fileName": executable.name,
                "sizeBytes": executable.stat().st_size,
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            }),
            encoding="utf-8",
        )
        return WindowsReleaseCatalog(root)

    def test_public_windows_download_uses_its_own_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = self._catalog(root)
            service = HeadlessService(ServiceConfig(work_root=root / "service", bearer_token="x" * 32))
            client = TestClient(create_app(service, windows_catalog=catalog))
            checked = client.get("/api/v1/app/update?platform=windows")
            downloaded = client.get("/api/v1/app/update/download?platform=windows")
            android = client.get("/api/v1/app/update?platform=android")
            unsupported = client.get("/api/v1/app/update/download?platform=macos")

        self.assertEqual(checked.status_code, 200)
        self.assertEqual(checked.json()["platform"], "windows")
        self.assertEqual(checked.json()["latestVersion"], "1.1.10")
        self.assertEqual(checked.json()["downloadUrl"], "/app/update/download?platform=windows")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"test-executable")
        self.assertIn('filename="API_TOOLS.exe"', downloaded.headers["content-disposition"])
        self.assertEqual(android.status_code, 404)
        self.assertEqual(unsupported.status_code, 404)

    def test_modified_installer_is_not_served(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = self._catalog(root)
            (root / "API_TOOLS.exe").write_bytes(b"evil-executable")
            service = HeadlessService(ServiceConfig(work_root=root / "service", bearer_token="x" * 32))
            client = TestClient(create_app(service, windows_catalog=catalog))
            checked = client.get("/api/v1/app/update?platform=windows")
            downloaded = client.get("/api/v1/app/update/download?platform=windows")

        self.assertEqual(checked.status_code, 503)
        self.assertEqual(downloaded.status_code, 503)

    def test_both_platforms_can_be_published_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            android_root, windows_root = root / "android", root / "windows"
            android_root.mkdir()
            windows_root.mkdir()
            android_catalog = AndroidReleaseTests()._catalog(android_root)
            windows_catalog = self._catalog(windows_root)
            service = HeadlessService(ServiceConfig(work_root=root / "service", bearer_token="x" * 32))
            client = TestClient(create_app(service, android_catalog, windows_catalog))
            for platform, expected in (("android", b"test-apk"), ("windows", b"test-executable")):
                with self.subTest(platform=platform):
                    response = client.get(f"/api/v1/app/update/download?platform={platform}")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, expected)
