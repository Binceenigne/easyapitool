from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import math
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PIL import Image


SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
CONTENT_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
OUTPUT_PRESETS = {
    "lossless": ("png", None),
    "large": ("jpeg", 90),
    "medium": ("jpeg", 75),
    "small": ("jpeg", 55),
}


def image_preview_bytes(
    image_bytes: bytes,
    max_side: int = 720,
    optimize: bool = True,
) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as source_image:
        source_image.load()
        preview_image = source_image.copy()
    preview_image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if preview_image.mode in {"RGBA", "LA"} or "transparency" in preview_image.info:
        rgba_image = preview_image.convert("RGBA")
        flattened = Image.new("RGB", rgba_image.size, "white")
        flattened.paste(rgba_image, mask=rgba_image.getchannel("A"))
        preview_image = flattened
    else:
        preview_image = preview_image.convert("RGB")
    preview_buffer = io.BytesIO()
    preview_image.save(preview_buffer, format="JPEG", quality=78, optimize=optimize)
    return preview_buffer.getvalue()


def image_preview_data_url(
    image_bytes: bytes,
    max_side: int = 720,
    optimize: bool = True,
) -> str:
    encoded = base64.b64encode(
        image_preview_bytes(image_bytes, max_side, optimize)
    ).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class ImageSessionStore:
    SCHEMA_VERSION = 2
    _manifest_lock = threading.RLock()

    def __init__(self, pictures_root: Path) -> None:
        self.root = pictures_root / "sessions"

    @staticmethod
    def _safe_id(value: Any, fallback: str = "") -> str:
        clean = "".join(
            character
            for character in str(value or "")[:80]
            if character.isalnum() or character in "-_"
        )
        return clean or fallback or uuid.uuid4().hex

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _reasoning_usage(value: Any) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}

        def nonnegative_int(name: str) -> int:
            try:
                return max(0, int(source.get(name) or 0))
            except (TypeError, ValueError):
                return 0

        try:
            cost_usd = float(source.get("costUsd") or 0)
        except (TypeError, ValueError):
            cost_usd = 0.0
        if not math.isfinite(cost_usd) or cost_usd < 0:
            cost_usd = 0.0
        return {
            "inputTokens": nonnegative_int("inputTokens"),
            "outputTokens": nonnegative_int("outputTokens"),
            "totalTokens": nonnegative_int("totalTokens"),
            "callCount": nonnegative_int("callCount"),
            "costUsd": cost_usd,
            "hasTokenUsage": bool(source.get("hasTokenUsage")),
            "hasCost": bool(source.get("hasCost")),
        }

    def _session_dir(self, session_id: str) -> Path:
        return self.root / self._safe_id(session_id)

    def _manifest_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "manifest.json"

    def _read_manifest(self, session_id: str) -> dict[str, Any] | None:
        manifest_path = self._manifest_path(session_id)
        try:
            manifest_text = manifest_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            payload = json.loads(manifest_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("图片会话清单已损坏") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("图片会话清单格式无效")
        return payload

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        manifest["schemaVersion"] = self.SCHEMA_VERSION
        manifest.setdefault("assets", [])
        manifest["updatedAt"] = self._timestamp()
        manifest["roundCount"] = len(manifest.get("rounds") or [])
        manifest_path = self._manifest_path(str(manifest["sessionId"]))
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = manifest_path.with_name(
            f".{manifest_path.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            for attempt in range(3):
                try:
                    os.replace(temporary_path, manifest_path)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.04 * (2 ** attempt))
        finally:
            temporary_path.unlink(missing_ok=True)

    def begin_round(
        self,
        session_id: str,
        set_id: str,
        prompt: str,
        requested_count: int,
        reference_count: int,
        options: dict[str, Any],
        parent_set_id: str = "",
    ) -> dict[str, Any]:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(set_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                created_at = self._timestamp()
                manifest = {
                    "schemaVersion": self.SCHEMA_VERSION,
                    "sessionId": clean_session_id,
                    "createdAt": created_at,
                    "updatedAt": created_at,
                    "roundCount": 0,
                    "rounds": [],
                    "assets": [],
                }
            existing = next(
                (round_data for round_data in manifest["rounds"] if round_data.get("setId") == clean_set_id),
                None,
            )
            if existing is not None:
                return dict(existing)
            round_number = max(
                (int(round_data.get("roundNumber") or 0) for round_data in manifest["rounds"]),
                default=0,
            ) + 1
            directory_name = f"round-{round_number:03d}-{clean_set_id[:12]}"
            round_data = {
                "roundNumber": round_number,
                "setId": clean_set_id,
                "requestId": clean_set_id,
                "parentSetId": self._safe_id(parent_set_id, "") if parent_set_id else "",
                "prompt": str(prompt),
                "requestedCount": int(requested_count),
                "referenceCount": int(reference_count),
                "status": "running",
                "createdAt": self._timestamp(),
                "completedAt": "",
                "directory": directory_name,
                "options": {
                    "size": str(options.get("size") or "auto"),
                    "quality": str(options.get("quality") or "auto"),
                    "outputPreset": str(options.get("outputPreset") or "lossless"),
                    "operation": str(options.get("operation") or "generate"),
                    "continuation": bool(options.get("continuation")),
                    "continuationRationale": str(options.get("continuationRationale") or "")[:600],
                    "selectedReferenceIndexes": list(options.get("selectedReferenceIndexes") or []),
                    "selectedAssetIds": list(options.get("selectedAssetIds") or [])[:16],
                    "inputAssetIds": list(options.get("inputAssetIds") or [])[:16],
                    "inputReferencePaths": [
                        str(path) for path in list(options.get("inputReferencePaths") or [])[:16]
                    ],
                    "reasoningMode": str(options.get("reasoningMode") or "instant"),
                    "reasoningModel": str(options.get("reasoningModel") or ""),
                    "reasoningEffort": str(options.get("reasoningEffort") or ""),
                    "reasoningSummary": str(options.get("reasoningSummary") or ""),
                    "reasoningDurationMs": max(0, int(options.get("reasoningDurationMs") or 0)),
                    "reasoningUsage": self._reasoning_usage(options.get("reasoningUsage")),
                    "originalPrompt": str(options.get("originalPrompt") or prompt),
                    "webSearchEnabled": bool(options.get("webSearchEnabled")),
                    "webSearchUsed": bool(options.get("webSearchUsed")),
                    "webSearchFailed": bool(options.get("webSearchFailed")),
                    "webSearchResultCount": int(options.get("webSearchResultCount") or 0),
                    "webReferenceCount": int(options.get("webReferenceCount") or 0),
                },
                "items": [
                    {"itemIndex": item_index, "status": "queued", "error": ""}
                    for item_index in range(int(requested_count))
                ],
            }
            manifest["rounds"].append(round_data)
            (self._session_dir(clean_session_id) / directory_name).mkdir(parents=True, exist_ok=True)
            self._write_manifest(manifest)
            return dict(round_data)

    def update_round_options(
        self,
        session_id: str,
        set_id: str,
        updates: dict[str, Any],
    ) -> None:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(set_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                raise RuntimeError("图片会话不存在")
            round_data = next(
                (item for item in manifest["rounds"] if item.get("setId") == clean_set_id),
                None,
            )
            if round_data is None:
                raise RuntimeError("图片生成轮次不存在")
            options = round_data.setdefault("options", {})
            options.update(updates)
            self._write_manifest(manifest)

    def _register_asset_locked(
        self,
        manifest: dict[str, Any],
        session_id: str,
        source_path: Path,
        source_set_id: str,
        source_role: str,
        description: str = "",
    ) -> dict[str, Any] | None:
        try:
            image_bytes = source_path.read_bytes()
            preview_bytes = image_preview_bytes(image_bytes)
        except (OSError, ValueError):
            return None
        digest = hashlib.sha256(image_bytes).hexdigest()
        asset_id = f"asset-{digest[:24]}"
        assets = manifest.setdefault("assets", [])
        existing = next(
            (item for item in assets if item.get("assetId") == asset_id),
            None,
        )
        if existing is not None:
            source_set_ids = existing.setdefault("sourceSetIds", [])
            if source_set_id and source_set_id not in source_set_ids:
                source_set_ids.append(source_set_id)
            source_roles = existing.setdefault("sourceRoles", [])
            if source_role and source_role not in source_roles:
                source_roles.append(source_role)
            if description and not str(existing.get("description") or "").strip():
                existing["description"] = description[:1200]
            return existing

        suffix = source_path.suffix.lower()
        if suffix not in SUPPORTED_IMAGE_SUFFIXES:
            suffix = ".png"
        session_dir = self._session_dir(session_id)
        asset_dir = session_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        original_path = asset_dir / f"{asset_id}{suffix}"
        preview_path = asset_dir / f"{asset_id}-preview.jpg"
        if source_path.resolve() != original_path.resolve():
            original_path.write_bytes(image_bytes)
        preview_path.write_bytes(preview_bytes)
        asset = {
            "assetId": asset_id,
            "sha256": digest,
            "original": original_path.relative_to(session_dir).as_posix(),
            "preview": preview_path.relative_to(session_dir).as_posix(),
            "description": description[:1200],
            "sourceSetIds": [source_set_id] if source_set_id else [],
            "sourceRoles": [source_role] if source_role else [],
            "createdAt": self._timestamp(),
        }
        assets.append(asset)
        return asset

    def register_assets(
        self,
        session_id: str,
        source_paths: list[str] | tuple[Path, ...],
        source_set_id: str,
        source_role: str,
        descriptions: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(source_set_id, "") if source_set_id else ""
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                raise RuntimeError("图片会话不存在")
            registered: list[dict[str, Any]] = []
            for raw_path in source_paths:
                source_path = Path(str(raw_path or "")).expanduser().resolve()
                description = str((descriptions or {}).get(str(source_path)) or "")
                asset = self._register_asset_locked(
                    manifest,
                    clean_session_id,
                    source_path,
                    clean_set_id,
                    source_role,
                    description,
                )
                if asset is not None:
                    registered.append(dict(asset))
            self._write_manifest(manifest)
            return registered

    def update_asset_descriptions(
        self,
        session_id: str,
        descriptions: dict[str, str],
    ) -> None:
        clean_session_id = self._safe_id(session_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                raise RuntimeError("图片会话不存在")
            changed = False
            for asset in manifest.setdefault("assets", []):
                asset_id = str(asset.get("assetId") or "")
                description = str(descriptions.get(asset_id) or "").strip()[:1200]
                if description and not str(asset.get("description") or "").strip():
                    asset["description"] = description
                    asset["describedAt"] = self._timestamp()
                    changed = True
            if changed:
                self._write_manifest(manifest)

    def continuation_context(self, session_id: str, set_id: str) -> dict[str, Any]:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(set_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                return {}
            rounds = manifest.get("rounds") or []
            round_by_id = {
                str(item.get("setId") or ""): item
                for item in rounds
                if item.get("setId")
            }
            round_data = round_by_id.get(clean_set_id)
            if round_data is None:
                return {}
            session_dir = self._session_dir(clean_session_id)
            dirty = False
            for existing_round in rounds:
                options = existing_round.get("options")
                if not isinstance(options, dict):
                    options = {}
                    existing_round["options"] = options
                input_asset_ids = list(options.get("inputAssetIds") or [])
                for raw_path in options.get("inputReferencePaths") or []:
                    input_path = Path(str(raw_path or "")).expanduser().resolve()
                    asset = self._register_asset_locked(
                        manifest,
                        clean_session_id,
                        input_path,
                        str(existing_round.get("setId") or ""),
                        "input",
                    )
                    if asset is not None and asset["assetId"] not in input_asset_ids:
                        input_asset_ids.append(asset["assetId"])
                        dirty = True
                if input_asset_ids != list(options.get("inputAssetIds") or []):
                    options["inputAssetIds"] = input_asset_ids
                for item in existing_round.get("items") or []:
                    if item.get("status") != "completed":
                        continue
                    output_path = session_dir / str(item.get("original") or "")
                    asset = self._register_asset_locked(
                        manifest,
                        clean_session_id,
                        output_path,
                        str(existing_round.get("setId") or ""),
                        "output",
                    )
                    if asset is not None and item.get("assetId") != asset["assetId"]:
                        item["assetId"] = asset["assetId"]
                        dirty = True
                for reference in existing_round.get("webReferences") or []:
                    reference_path = session_dir / str(reference.get("original") or "")
                    asset = self._register_asset_locked(
                        manifest,
                        clean_session_id,
                        reference_path,
                        str(existing_round.get("setId") or ""),
                        "web",
                        str(reference.get("caption") or reference.get("title") or ""),
                    )
                    if asset is not None and reference.get("assetId") != asset["assetId"]:
                        reference["assetId"] = asset["assetId"]
                        dirty = True
            if dirty:
                self._write_manifest(manifest)

            history: list[dict[str, Any]] = []
            pending = round_data
            seen: set[str] = set()
            while pending is not None:
                pending_id = str(pending.get("setId") or "")
                if not pending_id or pending_id in seen:
                    break
                seen.add(pending_id)
                options = pending.get("options")
                if not isinstance(options, dict):
                    options = {}
                history.append(
                    {
                        "setId": pending_id,
                        "roundNumber": int(pending.get("roundNumber") or 0),
                        "userPrompt": str(
                            options.get("originalPrompt") or pending.get("prompt") or ""
                        ),
                        "reasoningSummary": str(
                            options.get("reasoningSummary")
                            or options.get("continuationRationale")
                            or ""
                        ),
                        "operation": str(
                            options.get("operation")
                            or ("edit" if pending.get("referenceCount") else "generate")
                        ),
                        "inputAssetIds": list(options.get("inputAssetIds") or []),
                        "outputAssetIds": [
                            str(item.get("assetId") or "")
                            for item in pending.get("items") or []
                            if item.get("status") == "completed" and item.get("assetId")
                        ],
                    }
                )
                pending = round_by_id.get(str(pending.get("parentSetId") or ""))
            history.reverse()

            assets: list[dict[str, Any]] = []
            for asset in manifest.get("assets") or []:
                original_path = session_dir / str(asset.get("original") or "")
                preview_path = session_dir / str(asset.get("preview") or "")
                if not original_path.is_file():
                    continue
                assets.append(
                    {
                        "assetId": str(asset.get("assetId") or ""),
                        "description": str(asset.get("description") or ""),
                        "describedAt": str(asset.get("describedAt") or ""),
                        "sourceSetIds": list(asset.get("sourceSetIds") or []),
                        "sourceRoles": list(asset.get("sourceRoles") or []),
                        "path": str(original_path),
                        "previewPath": str(preview_path) if preview_path.is_file() else "",
                    }
                )
            return {
                "history": history,
                "assets": assets,
                "parentOutputAssetIds": list(history[-1]["outputAssetIds"]) if history else [],
            }

    def _update_item(
        self,
        session_id: str,
        set_id: str,
        item_index: int,
        replacement: dict[str, Any],
    ) -> dict[str, Any]:
        with self._manifest_lock:
            manifest = self._read_manifest(session_id)
            if manifest is None:
                raise RuntimeError("图片会话不存在")
            round_data = next(
                (item for item in manifest["rounds"] if item.get("setId") == set_id),
                None,
            )
            if round_data is None:
                raise RuntimeError("图片生成轮次不存在")
            while len(round_data["items"]) <= item_index:
                round_data["items"].append(
                    {"itemIndex": len(round_data["items"]), "status": "queued", "error": ""}
                )
            round_data["items"][item_index] = replacement
            self._write_manifest(manifest)
            return replacement

    def persist_result(
        self,
        session_id: str,
        set_id: str,
        item_index: int,
        source_path: Path,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(set_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                raise RuntimeError("图片会话不存在")
            round_data = next(
                (item for item in manifest["rounds"] if item.get("setId") == clean_set_id),
                None,
            )
            if round_data is None:
                raise RuntimeError("图片生成轮次不存在")
            round_dir = self._session_dir(clean_session_id) / str(round_data["directory"])
            round_dir.mkdir(parents=True, exist_ok=True)
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES else ".png"
            original_path = round_dir / f"image-{item_index + 1:02d}-original{suffix}"
            preview_path = round_dir / f"image-{item_index + 1:02d}-preview.jpg"
            shutil.copy2(source_path, original_path)
            original_bytes = original_path.read_bytes()
            preview_bytes = image_preview_bytes(original_bytes)
            preview_path.write_bytes(preview_bytes)
            relative_original = original_path.relative_to(self._session_dir(clean_session_id)).as_posix()
            relative_preview = preview_path.relative_to(self._session_dir(clean_session_id)).as_posix()
            item_data = {
                "itemIndex": item_index,
                "status": "completed",
                "error": "",
                "original": relative_original,
                "preview": relative_preview,
                "width": int(result.get("width") or 0),
                "height": int(result.get("height") or 0),
                "sizeBytes": len(original_bytes),
                "format": str(result.get("format") or suffix.lstrip(".")),
                "actualSize": str(result.get("actualSize") or ""),
                "quality": str(result.get("quality") or ""),
            }
            asset = self._register_asset_locked(
                manifest,
                clean_session_id,
                original_path,
                clean_set_id,
                "output",
            )
            if asset is not None:
                item_data["assetId"] = asset["assetId"]
            while len(round_data["items"]) <= item_index:
                round_data["items"].append(
                    {
                        "itemIndex": len(round_data["items"]),
                        "status": "queued",
                        "error": "",
                    }
                )
            round_data["items"][item_index] = item_data
            self._write_manifest(manifest)
            return {
                **result,
                "assetId": str(item_data.get("assetId") or ""),
                "path": str(original_path),
                "uri": original_path.as_uri(),
                "savedPath": str(original_path),
                "previewPath": str(preview_path),
                "previewUri": f"data:image/jpeg;base64,{base64.b64encode(preview_bytes).decode('ascii')}",
                "sessionId": clean_session_id,
                "setId": clean_set_id,
                "roundNumber": int(round_data["roundNumber"]),
            }

    def record_failure(
        self,
        session_id: str,
        set_id: str,
        item_index: int,
        error: str,
    ) -> None:
        self._update_item(
            self._safe_id(session_id),
            self._safe_id(set_id),
            item_index,
            {"itemIndex": item_index, "status": "failed", "error": str(error)},
        )

    def persist_web_references(
        self,
        session_id: str,
        set_id: str,
        references: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(set_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                raise RuntimeError("图片会话不存在")
            round_data = next(
                (item for item in manifest["rounds"] if item.get("setId") == clean_set_id),
                None,
            )
            if round_data is None:
                raise RuntimeError("图片生成轮次不存在")
            round_dir = self._session_dir(clean_session_id) / str(round_data["directory"])
            round_dir.mkdir(parents=True, exist_ok=True)
            persisted: list[dict[str, Any]] = []
            stored: list[dict[str, Any]] = []
            for reference in references:
                source_path = Path(str(reference.get("path") or ""))
                if not source_path.is_file():
                    continue
                try:
                    image_bytes = source_path.read_bytes()
                    preview_bytes = image_preview_bytes(image_bytes)
                except (OSError, ValueError):
                    continue
                index = len(persisted) + 1
                original_path = round_dir / f"web-reference-{index:02d}.jpg"
                preview_path = round_dir / f"web-reference-{index:02d}-preview.jpg"
                shutil.copy2(source_path, original_path)
                preview_path.write_bytes(preview_bytes)
                stored_reference = {
                    "id": str(reference.get("id") or "")[:80],
                    "title": str(reference.get("title") or "")[:240],
                    "caption": str(reference.get("caption") or "")[:400],
                    "provider": str(reference.get("provider") or "")[:120],
                    "sourceUrl": str(reference.get("sourceUrl") or "")[:4096],
                    "imageUrl": str(reference.get("imageUrl") or "")[:4096],
                    "original": original_path.relative_to(self._session_dir(clean_session_id)).as_posix(),
                    "preview": preview_path.relative_to(self._session_dir(clean_session_id)).as_posix(),
                }
                asset = self._register_asset_locked(
                    manifest,
                    clean_session_id,
                    original_path,
                    clean_set_id,
                    "web",
                    str(reference.get("caption") or reference.get("title") or ""),
                )
                if asset is not None:
                    stored_reference["assetId"] = asset["assetId"]
                stored.append(stored_reference)
                persisted.append(
                    {
                        **{key: value for key, value in stored_reference.items() if key not in {"original", "preview"}},
                        "path": str(original_path),
                        "previewPath": str(preview_path),
                        "previewUri": f"data:image/jpeg;base64,{base64.b64encode(preview_bytes).decode('ascii')}",
                    }
                )
            round_data["webReferences"] = stored
            options = round_data.setdefault("options", {})
            options["webReferenceCount"] = len(persisted)
            input_asset_ids = list(options.get("inputAssetIds") or [])
            for reference in stored:
                asset_id = str(reference.get("assetId") or "")
                if asset_id and asset_id not in input_asset_ids:
                    input_asset_ids.append(asset_id)
            options["inputAssetIds"] = input_asset_ids[:16]
            self._write_manifest(manifest)
            return persisted

    def complete_round(self, session_id: str, set_id: str) -> None:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(set_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                return
            round_data = next(
                (item for item in manifest["rounds"] if item.get("setId") == clean_set_id),
                None,
            )
            if round_data is None:
                return
            round_data["status"] = "completed"
            round_data["completedAt"] = self._timestamp()
            self._write_manifest(manifest)

    def list_sets(self) -> list[dict[str, Any]]:
        restored: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return restored
        with self._manifest_lock:
            manifest_paths = sorted(
                self.root.glob("*/manifest.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for manifest_path in manifest_paths:
                try:
                    manifest = self._read_manifest(manifest_path.parent.name)
                except (OSError, RuntimeError):
                    continue
                if manifest is None:
                    continue
                session_dir = manifest_path.parent
                for round_data in manifest.get("rounds") or []:
                    options = round_data.get("options")
                    if not isinstance(options, dict):
                        options = {}
                    reasoning_mode = str(options.get("reasoningMode") or "instant")
                    reasoning_summary = str(options.get("reasoningSummary") or "")
                    web_references: list[dict[str, Any]] = []
                    for reference in round_data.get("webReferences") or []:
                        original_path = session_dir / str(reference.get("original") or "")
                        preview_path = session_dir / str(reference.get("preview") or "")
                        if not original_path.is_file() or not preview_path.is_file():
                            continue
                        web_references.append(
                            {
                                "id": str(reference.get("id") or ""),
                                "assetId": str(reference.get("assetId") or ""),
                                "title": str(reference.get("title") or ""),
                                "caption": str(reference.get("caption") or ""),
                                "provider": str(reference.get("provider") or ""),
                                "sourceUrl": str(reference.get("sourceUrl") or ""),
                                "imageUrl": str(reference.get("imageUrl") or ""),
                                "path": str(original_path),
                                "previewPath": str(preview_path),
                                "previewUri": "",
                            }
                        )
                    items: list[dict[str, Any]] = []
                    for item_data in round_data.get("items") or []:
                        item_index = int(item_data.get("itemIndex") or 0)
                        if item_data.get("status") != "completed":
                            items.append(
                                {
                                    "itemIndex": item_index,
                                    "status": "failed" if round_data.get("status") == "completed" else str(item_data.get("status") or "queued"),
                                    "uri": "",
                                    "result": None,
                                    "error": str(item_data.get("error") or "生成已中断"),
                                }
                            )
                            continue
                        original_path = session_dir / str(item_data.get("original") or "")
                        preview_path = session_dir / str(item_data.get("preview") or "")
                        if not original_path.is_file() or not preview_path.is_file():
                            continue
                        result = {
                            "ok": True,
                            "itemIndex": item_index,
                            "assetId": str(item_data.get("assetId") or ""),
                            "path": str(original_path),
                            "uri": original_path.as_uri(),
                            "previewPath": str(preview_path),
                            "previewUri": "",
                            "width": int(item_data.get("width") or 0),
                            "height": int(item_data.get("height") or 0),
                            "sizeBytes": int(item_data.get("sizeBytes") or original_path.stat().st_size),
                            "format": str(item_data.get("format") or original_path.suffix.lstrip(".")),
                            "actualSize": str(item_data.get("actualSize") or ""),
                            "quality": str(item_data.get("quality") or ""),
                            "sessionId": str(manifest["sessionId"]),
                            "setId": str(round_data.get("setId") or ""),
                            "roundNumber": int(round_data.get("roundNumber") or 0),
                        }
                        items.append(
                            {
                                "itemIndex": item_index,
                                "status": "completed",
                                "uri": original_path.as_uri(),
                                "previewPath": str(preview_path),
                                "previewUri": "",
                                "result": result,
                                "error": "",
                            }
                        )
                    restored.append(
                        {
                            "setId": str(round_data.get("setId") or ""),
                            "requestId": str(round_data.get("requestId") or ""),
                            "sessionId": str(manifest["sessionId"]),
                            "parentSetId": str(round_data.get("parentSetId") or ""),
                            "roundNumber": int(round_data.get("roundNumber") or 0),
                            "requestedCount": int(round_data.get("requestedCount") or len(items)),
                            "prompt": str(round_data.get("prompt") or ""),
                            "originalPrompt": str(options.get("originalPrompt") or round_data.get("prompt") or ""),
                            "operation": str(options.get("operation") or ("edit" if round_data.get("referenceCount") else "generate")),
                            "continuation": bool(options.get("continuation")),
                            "continuationRationale": str(options.get("continuationRationale") or ""),
                            "selectedAssetIds": list(options.get("selectedAssetIds") or []),
                            "inputAssetIds": list(options.get("inputAssetIds") or []),
                            "reasoningMode": reasoning_mode,
                            "reasoningModel": str(options.get("reasoningModel") or ""),
                            "reasoningEffort": str(options.get("reasoningEffort") or ""),
                            "reasoningSummary": reasoning_summary,
                            "reasoningDurationMs": max(0, int(options.get("reasoningDurationMs") or 0)),
                            "reasoningUsage": self._reasoning_usage(options.get("reasoningUsage")),
                            "reasoningStatus": "completed" if reasoning_mode != "instant" and reasoning_summary else "idle",
                            "effectivePrompt": str(round_data.get("prompt") or "") if reasoning_mode != "instant" else "",
                            "webSearchEnabled": bool(options.get("webSearchEnabled")),
                            "webSearchUsed": bool(options.get("webSearchUsed")),
                            "webSearchFailed": bool(options.get("webSearchFailed")),
                            "webSearchResultCount": int(options.get("webSearchResultCount") or 0),
                            "webReferenceCount": len(web_references),
                            "webReferences": web_references,
                            "createdAt": str(round_data.get("createdAt") or manifest.get("createdAt") or ""),
                            "status": str(round_data.get("status") or "completed"),
                            "items": items,
                        }
                    )
        restored.sort(key=lambda item: (item["createdAt"], item["roundNumber"]), reverse=True)
        return restored

    def _prune_assets_locked(
        self,
        manifest: dict[str, Any],
        session_id: str,
    ) -> list[Path]:
        rounds = manifest.get("rounds") or []
        remaining_set_ids = {
            str(round_data.get("setId") or "")
            for round_data in rounds
            if round_data.get("setId")
        }
        referenced_asset_ids: set[str] = set()
        for round_data in rounds:
            options = round_data.get("options")
            if not isinstance(options, dict):
                options = {}
            for field in ("inputAssetIds", "selectedAssetIds"):
                referenced_asset_ids.update(
                    str(asset_id)
                    for asset_id in options.get(field) or []
                    if asset_id
                )
            referenced_asset_ids.update(
                str(item.get("assetId") or "")
                for item in round_data.get("items") or []
                if item.get("assetId")
            )
            referenced_asset_ids.update(
                str(reference.get("assetId") or "")
                for reference in round_data.get("webReferences") or []
                if reference.get("assetId")
            )

        session_dir = self._session_dir(session_id)
        retained_assets: list[dict[str, Any]] = []
        cleanup_paths: list[Path] = []
        for asset in manifest.get("assets") or []:
            source_set_ids = [
                str(source_set_id)
                for source_set_id in asset.get("sourceSetIds") or []
                if str(source_set_id) in remaining_set_ids
            ]
            asset["sourceSetIds"] = source_set_ids
            asset_id = str(asset.get("assetId") or "")
            if source_set_ids or asset_id in referenced_asset_ids:
                retained_assets.append(asset)
                continue
            for field in ("original", "preview"):
                asset_path = session_dir / str(asset.get(field) or "")
                if asset_path.is_file() and asset_path.parent == session_dir / "assets":
                    cleanup_paths.append(asset_path)
        manifest["assets"] = retained_assets
        return cleanup_paths

    @staticmethod
    def _cleanup_committed_paths(paths: list[Path]) -> None:
        for path in paths:
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    def discard_asset_source(self, session_id: str, source_set_id: str) -> None:
        clean_session_id = self._safe_id(session_id)
        clean_source_set_id = self._safe_id(source_set_id, "")
        if not clean_source_set_id:
            return
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                return
            for asset in manifest.get("assets") or []:
                asset["sourceSetIds"] = [
                    str(existing_set_id)
                    for existing_set_id in asset.get("sourceSetIds") or []
                    if str(existing_set_id) != clean_source_set_id
                ]
            cleanup_paths = self._prune_assets_locked(manifest, clean_session_id)
            self._write_manifest(manifest)
            self._cleanup_committed_paths(cleanup_paths)

    def delete_set(self, session_id: str, set_id: str) -> bool:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(set_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                return False
            round_data = next(
                (item for item in manifest["rounds"] if item.get("setId") == clean_set_id),
                None,
            )
            if round_data is None:
                return False
            if any(
                str(item.get("parentSetId") or "") == clean_set_id
                for item in manifest["rounds"]
            ):
                raise ValueError("该轮次已有后续创作，请先删除后续轮次")
            session_dir = self._session_dir(clean_session_id)
            round_dir = session_dir / str(round_data.get("directory") or "")
            manifest["rounds"] = [
                item for item in manifest["rounds"] if item.get("setId") != clean_set_id
            ]
            if manifest["rounds"]:
                cleanup_paths = self._prune_assets_locked(manifest, clean_session_id)
                self._write_manifest(manifest)
                if round_dir.is_dir() and round_dir.parent == session_dir:
                    cleanup_paths.append(round_dir)
                self._cleanup_committed_paths(cleanup_paths)
            else:
                self._prune_assets_locked(manifest, clean_session_id)
                self._write_manifest(manifest)
                self._cleanup_committed_paths([session_dir])
            return True


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    image_paths: tuple[Path, ...]
    operation: str
    fields: dict[str, Any]
    output_preset: str
    output_format: str
    requested_size: str
    requested_quality: str
    stream: bool
    partial_images: int


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def prepare_image_generation(
    prompt: str,
    image_paths: list[str],
    options: dict[str, Any] | None = None,
) -> ImageGenerationRequest:
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("请输入生图提示词")
    if len(clean_prompt) > 32_000:
        raise ValueError("生图提示词不能超过 32000 个字符")
    if not isinstance(image_paths, list) or len(image_paths) > 16:
        raise ValueError("参考图片不能超过 16 张")

    valid_paths: list[Path] = []
    for raw_path in image_paths:
        image_path = Path(str(raw_path or "")).expanduser().resolve()
        if not image_path.is_file():
            raise ValueError(f"找不到参考图片：{image_path.name}")
        if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise ValueError(f"不支持的图片格式：{image_path.name}")
        if image_path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError(f"参考图片不能超过 50 MB：{image_path.name}")
        try:
            with Image.open(image_path) as source_image:
                source_image.verify()
        except (OSError, ValueError):
            raise ValueError(f"参考图片无效：{image_path.name}") from None
        valid_paths.append(image_path)

    clean_options = options if isinstance(options, dict) else {}
    operation = str(
        clean_options.get("operation") or ("edit" if valid_paths else "generate")
    ).lower()
    if operation not in {"edit", "generate"}:
        raise ValueError("无效的图片操作")
    if operation == "edit" and not valid_paths:
        raise ValueError("图片编辑至少需要一张参考图")
    output_preset = str(clean_options.get("outputPreset") or "lossless").lower()
    quality = str(clean_options.get("quality") or "auto").lower()
    size = str(clean_options.get("size") or "auto").lower()
    background = str(clean_options.get("background") or "auto").lower()
    moderation = str(clean_options.get("moderation") or "low").lower()
    stream = bool(clean_options.get("stream"))
    partial_images = int(_number(clean_options.get("partialImages"), 0))
    if output_preset not in OUTPUT_PRESETS:
        raise ValueError("无效的输出大小")
    output_format, _jpeg_quality = OUTPUT_PRESETS[output_preset]
    if quality not in {"low", "medium", "high", "auto"}:
        raise ValueError("无效的图片质量")
    if size != "auto":
        size_match = __import__("re").fullmatch(r"(\d+)x(\d+)", size)
        if not size_match:
            raise ValueError("图片尺寸必须是 auto 或 WIDTHxHEIGHT")
        width, height = (int(value) for value in size_match.groups())
        pixels = width * height
        if (
            width % 16
            or height % 16
            or max(width, height) / min(width, height) > 3
            or max(width, height) > 3840
            or not 655_360 <= pixels <= 8_294_400
        ):
            raise ValueError("图片尺寸不符合 GPT Image 2 的边长、比例或像素限制")
    if background not in {"transparent", "opaque", "auto"}:
        raise ValueError("无效的背景模式")
    if background == "transparent" and output_preset != "lossless":
        raise ValueError("透明背景只能使用无损 PNG 输出")
    if moderation not in {"low", "auto"}:
        raise ValueError("无效的审核级别")
    if not 0 <= partial_images <= 3:
        raise ValueError("流式预览图数量必须在 0 到 3 之间")

    fields = {
        "model": "gpt-image-2",
        "prompt": clean_prompt,
        "quality": quality,
        "size": size,
        "output_format": "png",
        "background": background,
        "moderation": moderation,
        "stream": stream,
    }
    if stream:
        fields["partial_images"] = partial_images
    return ImageGenerationRequest(
        prompt=clean_prompt,
        image_paths=tuple(valid_paths),
        operation=operation,
        fields=fields,
        output_preset=output_preset,
        output_format=output_format,
        requested_size=size,
        requested_quality=quality,
        stream=stream,
        partial_images=partial_images,
    )


class ImageGenerationClient:
    @staticmethod
    def _opener() -> urllib.request.OpenerDirector:
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: Any, **kwargs: Any) -> None:
                return None

        return urllib.request.build_opener(NoRedirectHandler())

    @staticmethod
    def _error(exc: urllib.error.HTTPError, secret: str) -> RuntimeError:
        body_text = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body_text)
            message = (parsed.get("error") or {}).get("message") or parsed.get("message")
        except json.JSONDecodeError:
            message = body_text[:200]
        safe_message = str(message or exc.reason)
        if secret:
            safe_message = safe_message.replace(secret, "[REDACTED]")
        return RuntimeError(f"HTTP {exc.code}: {safe_message}")

    @staticmethod
    def _read_response(
        response: Any,
        stream: bool,
        on_partial: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        if not stream:
            return json.loads(response.read().decode("utf-8"))

        def image_data(payload: Any) -> str:
            if isinstance(payload, dict):
                direct = payload.get("b64_json")
                if isinstance(direct, str) and direct:
                    return direct
                for key in ("data", "result", "image", "output", "response"):
                    nested = image_data(payload.get(key))
                    if nested:
                        return nested
            elif isinstance(payload, list):
                for item in payload:
                    nested = image_data(item)
                    if nested:
                        return nested
            return ""

        completed: dict[str, Any] | None = None
        completed_image = ""
        last_partial_image = ""
        partial_count = 0

        def process_event(payload: str, sse_event_type: str = "") -> None:
            nonlocal completed, completed_image, last_partial_image, partial_count
            event_name = sse_event_type.strip().lower()
            named_completion = event_name in {"completed", "complete", "done"} or event_name.endswith(
                (".completed", ".complete", ".done")
            )
            if not payload:
                return
            if payload == "[DONE]":
                if named_completion:
                    completed = {"type": event_name}
                return
            event = json.loads(payload)
            event_type = str(event.get("type") or event_name).strip().lower()
            encoded_image = image_data(event)
            if event_type.endswith(".partial_image"):
                partial_count += 1
                if encoded_image:
                    last_partial_image = encoded_image
                if encoded_image and on_partial is not None:
                    on_partial(encoded_image, partial_count)
                return
            is_completed = event_type in {"completed", "complete", "done"} or event_type.endswith(
                (".completed", ".complete", ".done")
            )
            is_plain_final = not event_type and encoded_image
            if is_completed:
                completed = event
                completed_image = encoded_image
            elif is_plain_final:
                completed = event
                completed_image = encoded_image

        pending_event_type = ""
        pending_data: list[str] = []

        def flush_pending() -> None:
            nonlocal pending_event_type, pending_data
            if pending_data:
                process_event("\n".join(pending_data), pending_event_type)
            pending_event_type = ""
            pending_data = []

        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                flush_pending()
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                pending_event_type = line[6:].strip()
                continue
            if line.startswith("data:"):
                pending_data.append(line[5:].lstrip())
                try:
                    flush_pending()
                except json.JSONDecodeError:
                    continue
                continue
            flush_pending()
            if line.lstrip().startswith("{"):
                process_event(line.strip())
        flush_pending()
        if completed is not None and not completed_image and last_partial_image:
            completed_image = last_partial_image
        if completed is None or not completed_image:
            raise RuntimeError("流式生图接口未返回最终图片")
        return {
            "data": [{"b64_json": completed_image}],
            "background": completed.get("background"),
            "output_format": completed.get("output_format"),
            "quality": completed.get("quality"),
            "size": completed.get("size"),
            "usage": completed.get("usage"),
            "partial_images_received": partial_count,
        }

    @classmethod
    def generate_image(
        cls,
        base_url: str,
        secret: str,
        fields: dict[str, Any],
        timeout: int = 600,
        on_partial: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/images/generations",
            data=json.dumps(fields, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "text/event-stream" if fields.get("stream") else "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with cls._opener().open(request, timeout=timeout) as response:
                return cls._read_response(
                    response,
                    bool(fields.get("stream")),
                    on_partial,
                )
        except urllib.error.HTTPError as exc:
            raise cls._error(exc, secret) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
            ) from None

    @staticmethod
    def edit_images(
        base_url: str,
        secret: str,
        image_paths: tuple[Path, ...],
        fields: dict[str, Any],
        timeout: int = 600,
        on_partial: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        boundary = f"----API_TOOLS_{uuid.uuid4().hex}"
        body = io.BytesIO()

        def write_part(headers: list[str], content: bytes) -> None:
            body.write(f"--{boundary}\r\n".encode("ascii"))
            body.write(("\r\n".join(headers) + "\r\n\r\n").encode("utf-8"))
            body.write(content)
            body.write(b"\r\n")

        for name, value in fields.items():
            field_value = str(value).lower() if isinstance(value, bool) else str(value)
            write_part(
                [f'Content-Disposition: form-data; name="{name}"'],
                field_value.encode("utf-8"),
            )
        for index, image_path in enumerate(image_paths, start=1):
            suffix = image_path.suffix.lower()
            write_part(
                [
                    f'Content-Disposition: form-data; name="image[]"; filename="image-{index}{suffix}"',
                    f"Content-Type: {CONTENT_TYPES[suffix]}",
                ],
                image_path.read_bytes(),
            )
        body.write(f"--{boundary}--\r\n".encode("ascii"))

        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/images/edits",
            data=body.getvalue(),
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "text/event-stream" if fields.get("stream") else "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with ImageGenerationClient._opener().open(request, timeout=timeout) as response:
                return ImageGenerationClient._read_response(
                    response,
                    bool(fields.get("stream")),
                    on_partial,
                )
        except urllib.error.HTTPError as exc:
            raise ImageGenerationClient._error(exc, secret) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
            ) from None


class ImageGenerationService:
    def __init__(self, client: ImageGenerationClient | None = None) -> None:
        self.client = client or ImageGenerationClient()

    def generate(
        self,
        base_url: str,
        secret: str,
        request: ImageGenerationRequest,
        output_dir: Path,
        on_partial: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        generation_id = uuid.uuid4().hex[:12]

        def persist_partial(image_data: str, partial_index: int) -> None:
            try:
                partial_bytes = base64.b64decode(image_data, validate=True)
                with Image.open(io.BytesIO(partial_bytes)) as partial_image:
                    partial_image.verify()
                partial_dir = output_dir / "partials"
                partial_dir.mkdir(parents=True, exist_ok=True)
                partial_path = partial_dir / f"{generation_id}-{partial_index}.png"
                partial_path.write_bytes(partial_bytes)
                if on_partial is not None:
                    on_partial(
                        {
                            "partialIndex": partial_index,
                            "partialTotal": request.partial_images,
                            "path": str(partial_path),
                            "uri": partial_path.as_uri(),
                            "previewUri": image_preview_data_url(
                                partial_bytes,
                                max_side=512,
                                optimize=False,
                            ),
                        }
                    )
            except (binascii.Error, OSError, ValueError):
                return

        if request.image_paths:
            response = self.client.edit_images(
                base_url,
                secret,
                request.image_paths,
                request.fields,
                on_partial=persist_partial,
            )
        else:
            response = self.client.generate_image(
                base_url,
                secret,
                request.fields,
                on_partial=persist_partial,
            )
        image_data = ((response.get("data") or [{}])[0] or {}).get("b64_json")
        if not image_data:
            raise RuntimeError("生图接口未返回图片数据")
        try:
            result_bytes = base64.b64decode(image_data, validate=True)
        except binascii.Error as exc:
            raise RuntimeError("生图接口返回了无效图片数据") from exc

        with Image.open(io.BytesIO(result_bytes)) as source_image:
            source_image.load()
            result_image = source_image.copy()
            width, height = result_image.size

        suffix = ".png" if request.output_format == "png" else ".jpg"
        output_path = output_dir / (
            f"image-generation-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}{suffix}"
        )
        output_buffer = io.BytesIO()
        if request.output_format == "png":
            result_image.save(output_buffer, format="PNG")
        else:
            if result_image.mode in {"RGBA", "LA"} or "transparency" in result_image.info:
                rgba_image = result_image.convert("RGBA")
                flattened = Image.new("RGB", rgba_image.size, "white")
                flattened.paste(rgba_image, mask=rgba_image.getchannel("A"))
                result_image = flattened
            else:
                result_image = result_image.convert("RGB")
            result_image.save(
                output_buffer,
                format="JPEG",
                quality=OUTPUT_PRESETS[request.output_preset][1],
                optimize=True,
            )
        output_bytes = output_buffer.getvalue()
        output_path.write_bytes(output_bytes)
        return {
            "ok": True,
            "path": str(output_path),
            "uri": output_path.as_uri(),
            "previewUri": image_preview_data_url(output_bytes),
            "width": width,
            "height": height,
            "sizeBytes": len(output_bytes),
            "format": request.output_format,
            "outputPreset": request.output_preset,
            "quality": response.get("quality") or request.fields["quality"],
            "requestedQuality": request.requested_quality,
            "requestedSize": request.requested_size,
            "actualSize": response.get("size") or f"{width}x{height}",
            "background": response.get("background") or request.fields["background"],
            "stream": request.stream,
            "partialImagesRequested": request.partial_images if request.stream else 0,
            "partialImagesReceived": int(response.get("partial_images_received") or 0),
            "referenceCount": len(request.image_paths),
            "operation": request.operation,
            "transportOperation": "edit" if request.image_paths else "generate",
        }