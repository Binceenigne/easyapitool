from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AppReleaseError(RuntimeError):
    pass


class AndroidReleaseCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.manifest_path = self.root / "manifest.json"

    def _load_manifest(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppReleaseError("Android 发布清单不可用") from exc
        if not isinstance(payload, dict) or payload.get("platform") != "android":
            raise AppReleaseError("Android 发布清单格式无效")
        return payload

    def _release_file(self, manifest: dict[str, Any]) -> Path:
        filename = Path(str(manifest.get("fileName") or "")).name
        if not filename or filename != str(manifest.get("fileName")):
            raise AppReleaseError("Android 发布文件名无效")
        path = (self.root / filename).resolve()
        if path.parent != self.root or not path.is_file():
            raise AppReleaseError("Android 安装包不存在")
        return path

    @staticmethod
    def _integer(value: Any, field: str, minimum: int = 0) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise AppReleaseError(f"Android 发布字段无效：{field}") from exc
        if result < minimum:
            raise AppReleaseError(f"Android 发布字段无效：{field}")
        return result

    def _verified_manifest(self) -> tuple[dict[str, Any], Path]:
        manifest = self._load_manifest()
        package_name = str(manifest.get("packageName") or "")
        if package_name != "cn.easyapitool.mobile":
            raise AppReleaseError("Android 包名不匹配")
        path = self._release_file(manifest)
        actual_size = path.stat().st_size
        expected_size = self._integer(manifest.get("sizeBytes"), "sizeBytes", 1)
        if actual_size != expected_size:
            raise AppReleaseError("Android 安装包大小校验失败")
        expected_sha256 = str(manifest.get("sha256") or "").lower()
        if len(expected_sha256) != 64:
            raise AppReleaseError("Android 安装包校验值无效")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise AppReleaseError("Android 安装包 SHA-256 校验失败")
        return manifest, path

    def check(
        self,
        *,
        current_version_code: int = 0,
        current_build_timestamp_ms: int = 0,
    ) -> dict[str, Any]:
        manifest, path = self._verified_manifest()
        latest_version_code = self._integer(manifest.get("versionCode"), "versionCode", 1)
        latest_build_timestamp_ms = self._integer(
            manifest.get("buildTimestampMs"), "buildTimestampMs", 1
        )
        current_code = max(0, int(current_version_code))
        current_timestamp = max(0, int(current_build_timestamp_ms))
        available = latest_version_code > current_code or (
            latest_version_code == current_code
            and latest_build_timestamp_ms > current_timestamp
        )
        build_time = datetime.fromtimestamp(
            latest_build_timestamp_ms / 1000,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        return {
            "ok": True,
            "platform": "android",
            "available": available,
            "packageName": manifest["packageName"],
            "currentVersionCode": current_code,
            "currentBuildTimestampMs": current_timestamp,
            "latestVersion": str(manifest.get("versionName") or latest_version_code),
            "latestVersionCode": latest_version_code,
            "latestBuildTimestampMs": latest_build_timestamp_ms,
            "latestBuildTime": build_time,
            "releaseNotes": str(manifest.get("releaseNotes") or ""),
            "fileName": path.name,
            "downloadSize": path.stat().st_size,
            "sha256": str(manifest["sha256"]).lower(),
            "downloadUrl": "/app/update/download?platform=android",
        }

    def download(self) -> tuple[Path, str]:
        manifest, path = self._verified_manifest()
        return path, str(manifest.get("mimeType") or "application/vnd.android.package-archive")