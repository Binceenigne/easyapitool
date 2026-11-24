from __future__ import annotations

from .agent_metadata import IMAGE_AGENT_METADATA_FIELDS

import base64
import binascii
import hashlib
import http.client
import io
import inspect
import json
import math
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .network_log import log_network_error, new_network_request_id
from .transport import open_request
from mirra_agents.retry import ProviderResponseError, safe_text


SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
OUTPUT_IMAGE_SUFFIXES = SUPPORTED_IMAGE_SUFFIXES | {".svg", ".gif"}
CONTENT_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}
OUTPUT_PRESETS = {
    "lossless": "png",
    "large": "jpeg",
    "medium": "jpeg",
    "small": "jpeg",
}
IMAGE_MODEL_IDS = {
    "gpt-image-2", "gpt-image-2.5", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst",
    "gpt-image-2.6-flare", "gpt-image-2.6-sunburst",
}
IMAGE_SIZES_BY_RATIO = {
    "9:16": "720x1280", "2:3": "768x1152", "3:4": "768x1024", "1:1": "1024x1024",
    "4:3": "1024x768", "3:2": "1152x768", "16:9": "1280x720", "21:9": "1344x576",
}
IMAGE_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}


def svg_size(image_bytes: bytes) -> tuple[int, int] | None:
    data = image_bytes.removeprefix(b"\xef\xbb\xbf").lstrip()
    if not data.startswith(b"<"):
        return None
    if len(data) > 50 * 1024 * 1024 or re.search(rb"<!(?:DOCTYPE|ENTITY)\b|<\?(?!xml\s)", data, re.IGNORECASE):
        raise ValueError("SVG 内容无效或超过 50 MB")
    try:
        root = ET.fromstring(data.decode("utf-8"))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise ValueError("SVG 内容无效") from exc
    if root.tag not in {"svg", "{http://www.w3.org/2000/svg}svg"}:
        raise ValueError("SVG 根节点无效")
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() in {"script", "foreignobject", "animate", "animatemotion", "animatetransform", "set"}:
            raise ValueError("SVG 含有无法预览的内容")
        for attr, value in element.attrib.items():
            name = attr.rsplit("}", 1)[-1].lower()
            if name.startswith("on") or (name == "href" and not value.strip().startswith("#")):
                raise ValueError("SVG 含有无法预览的内容")
    def pixels(value: str) -> int:
        try:
            numeric = float(value.removesuffix("px"))
        except ValueError:
            return 0
        return round(numeric) if math.isfinite(numeric) and 0 < numeric <= 100_000 else 0

    width, height = pixels(root.get("width", "")), pixels(root.get("height", ""))
    view_box = re.split(r"[\s,]+", root.get("viewBox", "").strip())
    if len(view_box) == 4:
        width, height = width or pixels(view_box[2]), height or pixels(view_box[3])
    return width, height


def image_preview_bytes(
    image_bytes: bytes,
    max_side: int = 720,
    optimize: bool = True,
) -> bytes:
    if svg_size(image_bytes) is not None:
        return image_bytes
    with Image.open(io.BytesIO(image_bytes)) as source_image:
        if source_image.format == "GIF":
            return image_bytes
        source_image.load()
        preview_image = source_image.copy()
    preview_image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    has_alpha = preview_image.mode in {"RGBA", "LA"} or "transparency" in preview_image.info
    if has_alpha:
        preview_image = preview_image.convert("RGBA")
    else:
        preview_image = preview_image.convert("RGB")
    preview_buffer = io.BytesIO()
    preview_image.save(
        preview_buffer,
        format="PNG" if has_alpha else "JPEG",
        quality=None if has_alpha else 78,
        optimize=optimize,
    )
    return preview_buffer.getvalue()


def image_preview_data_url(
    image_bytes: bytes,
    max_side: int = 720,
    optimize: bool = True,
) -> str:
    return image_data_url(image_preview_bytes(image_bytes, max_side, optimize))


def reference_preview_bytes(image_bytes: bytes, max_side: int = 1024) -> bytes:
    """A static raster for model input, distinct from animated/vector previews."""
    maximum = min(4096, max(16, int(max_side)))
    dimensions = svg_size(image_bytes)
    if dimensions is not None:
        import resvg_py
        width, height = dimensions
        edge = min(maximum, max(width, height)) if max(width, height) else maximum
        try:
            image_bytes = resvg_py.svg_to_bytes(
                svg_string=image_bytes.decode("utf-8-sig"),
                width=edge if width >= height else None,
                height=edge if height > width else None,
            )
        except Exception as exc:
            raise ValueError("SVG 无法转换为静态参考图") from exc
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.seek(0)
        if dimensions is None and image.format != "GIF":
            return image_preview_bytes(image_bytes, maximum)
        frame = image.convert("RGBA")
    frame.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG")
    return buffer.getvalue()


def reference_preview_data_url(image_bytes: bytes, max_side: int = 1024) -> str:
    return image_data_url(reference_preview_bytes(image_bytes, max_side))


def reference_raster_path(source: Path) -> Path:
    if source.suffix.lower() not in {".gif", ".svg"}:
        return source
    data = source.read_bytes()
    cache_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "API_TOOLS" / "image-references" / "raster-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / (hashlib.sha256(data).hexdigest() + ".png")
    if not target.is_file():
        raster = reference_preview_bytes(data, 4096)
        temporary = cache_root / ("." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(raster)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def image_data_url(image_bytes: bytes) -> str:
    mime_type = CONTENT_TYPES[image_file_suffix(image_bytes)]
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def image_file_suffix(image_bytes: bytes) -> str:
    if svg_size(image_bytes) is not None:
        return ".svg"
    with Image.open(io.BytesIO(image_bytes)) as image:
        return IMAGE_EXTENSIONS.get(image.format, ".jpg")


class ImageSessionStore:
    SCHEMA_VERSION = 4
    _manifest_lock = threading.RLock()
    _directory_cache: dict[tuple[str, str], Path] = {}

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
        call_count = nonnegative_int("callCount")
        costed_call_count = nonnegative_int("costedCallCount")
        estimated_call_count = min(costed_call_count, nonnegative_int("estimatedCallCount"))
        cost_source = str(source.get("costSource") or "")
        has_cost = (
            bool(source.get("hasCost"))
            and cost_source in {"response", "estimate", "mixed"}
            and call_count > 0
            and costed_call_count == call_count
        )
        return {
            "inputTokens": nonnegative_int("inputTokens"),
            "outputTokens": nonnegative_int("outputTokens"),
            "totalTokens": nonnegative_int("totalTokens"),
            "callCount": call_count,
            "costUsd": cost_usd if has_cost else 0.0,
            "costedCallCount": costed_call_count,
            "estimatedCallCount": estimated_call_count,
            "costSource": cost_source if has_cost else "",
            "hasTokenUsage": bool(source.get("hasTokenUsage")),
            "hasCost": has_cost,
        }

    def _session_dir(self, session_id: str) -> Path:
        clean = self._safe_id(session_id)
        key = (str(self.root.resolve()), clean)
        with self._manifest_lock:
            cached = self._directory_cache.get(key)
            if cached is not None and cached.is_dir():
                return self._within_root(cached)
            legacy = self.root / clean
            if legacy.is_dir():
                legacy = self._within_root(legacy)
                try:
                    identity = json.loads((legacy / "manifest.json").read_text(encoding="utf-8"))
                    if isinstance(identity, dict) and identity.get("sessionId") == clean:
                        self._directory_cache[key] = legacy
                        return legacy
                except (OSError, ValueError, TypeError):
                    pass
            for manifest_path in self.root.glob("*/manifest.json"):
                try:
                    folder = self._within_root(manifest_path.parent)
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        continue
                    found_id = str(data.get("sessionId") or "")
                except (OSError, ValueError, TypeError):
                    continue
                if found_id:
                    self._directory_cache[(key[0], found_id)] = folder
                if found_id == clean:
                    return folder
            return self._unique_folder(self.root, clean) if legacy.exists() else self._within_root(legacy)

    def _within_root(self, path: Path) -> Path:
        resolved, root = path.resolve(), self.root.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise ValueError("项目路径超出图片会话目录")
        return resolved

    @staticmethod
    def _prompt_folder(prompt: Any, maximum: int = 40) -> str:
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(prompt or ""))
        text = re.sub(r"\s+", " ", text).strip(" .")
        # Bound UTF-16 code units as well as Python characters for Windows paths.
        text = text.encode("utf-16-le")[:maximum * 2].decode("utf-16-le", "ignore").rstrip(" .")
        if not text:
            return "未命名创作"
        if re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", text):
            text = "创作 " + text
        return text

    def _unique_folder(self, parent: Path, name: str, current: Path | None = None) -> Path:
        for index in range(1, 100_001):
            target = self._within_root(parent / (name if index == 1 else f"{name} ({index})"))
            if current is not None and target == current.resolve():
                return target
            if not target.exists():
                return target
        raise OSError("同名项目过多，请整理图片目录后重试")

    def _ensure_layout(self, manifest: dict[str, Any]) -> bool:
        rounds = manifest.get("rounds") or []
        if not rounds:
            return False
        session_id = str(manifest["sessionId"])
        current = self._session_dir(session_id)
        changed = False
        if not manifest.get("folderName"):
            title = (rounds[0].get("options") or {}).get("originalPrompt") or rounds[0].get("prompt")
            target = self._unique_folder(self.root, self._prompt_folder(title), current)
            current.mkdir(parents=True, exist_ok=True)
            if current != target:
                # Both absolute paths have been checked against this exact root.
                current.rename(target)
                current = target
            self._directory_cache[(str(self.root.resolve()), session_id)] = current
            manifest["folderName"] = current.name
            changed = True
        for index, round_data in enumerate(rounds, 1):
            directory = self._within_root(current / str(round_data.get("directory") or ""))
            if directory.parent != current:
                raise ValueError("图片集目录无效")
            if not round_data.get("folderName"):
                prompt = (round_data.get("options") or {}).get("originalPrompt") or round_data.get("prompt")
                name = f"第{index}组 {self._prompt_folder(prompt, 24)}"
                set_id = str(round_data.get("setId") or "")
                marker_name = ".mirra-project.json"
                if directory.is_dir():
                    marker = directory / marker_name
                    if not marker.exists():
                        marker.write_text(json.dumps({"setId": set_id}, ensure_ascii=False), encoding="utf-8")
                target = None
                for candidate in current.iterdir():
                    marker = candidate / marker_name
                    try:
                        candidate = self._within_root(candidate)
                        if candidate.is_dir() and json.loads(marker.read_text(encoding="utf-8")).get("setId") == set_id:
                            if candidate != directory:
                                target = self._within_root(candidate)
                                break
                    except (OSError, ValueError, TypeError):
                        continue
                target = target or self._unique_folder(current, name, directory)
                if directory.is_dir() and directory != target:
                    directory.rename(target)
                old_prefix = str(round_data.get("directory") or "") + "/"
                new_prefix = target.relative_to(current).as_posix() + "/"
                # All manifest media paths are relative to the session root.
                def relocate(value: Any) -> Any:
                    if isinstance(value, dict):
                        return {key: relocate(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [relocate(item) for item in value]
                    if isinstance(value, str) and value.startswith(old_prefix):
                        return new_prefix + value[len(old_prefix):]
                    return value
                for round_entry in rounds:
                    for field in ("items", "webReferences"):
                        round_entry[field] = relocate(round_entry.get(field) or [])
                manifest["assets"] = relocate(manifest.get("assets") or [])
                round_data["directory"] = target.relative_to(current).as_posix()
                round_data["folderName"] = target.name
                directory = target
                changed = True
            directory.mkdir(parents=True, exist_ok=True)
            marker = directory / ".mirra-project.json"
            if not marker.exists():
                marker.write_text(json.dumps({"setId": str(round_data.get("setId") or "")}, ensure_ascii=False), encoding="utf-8")
            output = self._within_root(directory / "Output")
            output.mkdir(exist_ok=True)
            mapping_path = directory / ".mirra-output-map.json"
            try:
                output_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
                if not isinstance(output_mapping, dict):
                    raise ValueError("成品迁移记录无效")
            except FileNotFoundError:
                output_mapping = {}
            for item in round_data.get("items") or []:
                if item.get("status") != "completed" or not item.get("original"):
                    continue
                source = self._within_root(current / str(item["original"]))
                if source.parent == output:
                    continue
                if source.suffix.lower() not in OUTPUT_IMAGE_SUFFIXES:
                    continue
                old = str(item["original"])
                mapped = output_mapping.get(old)
                target = self._within_root(directory / str(mapped.get("target") or "")) if isinstance(mapped, dict) else self._within_root(output / source.name)
                if target.parent != output:
                    raise ValueError("成品迁移目标无效")
                source_bytes = source.read_bytes() if source.is_file() else None
                if isinstance(mapped, dict):
                    expected_hash = str(mapped.get("sha256") or "")
                    current_bytes = source_bytes if source_bytes is not None else target.read_bytes()
                    if hashlib.sha256(current_bytes).hexdigest() != expected_hash:
                        raise OSError("成品迁移文件内容已改变")
                if source.exists():
                    if target.exists() and source_bytes != target.read_bytes():
                        if mapped is not None:
                            raise OSError("成品迁移遇到同名文件冲突")
                        target = self._within_root(output / f"{source.stem}-{hashlib.sha256(source_bytes).hexdigest()[:8]}{source.suffix}")
                    if target.exists() and source_bytes != target.read_bytes():
                        raise OSError("成品迁移遇到同名文件冲突")
                    if mapped is None:
                        output_mapping[old] = {"target": target.relative_to(directory).as_posix(), "sha256": hashlib.sha256(source_bytes).hexdigest()}
                        temporary = mapping_path.with_name(f".{mapping_path.name}.{uuid.uuid4().hex}.tmp")
                        try:
                            temporary.write_text(json.dumps(output_mapping, ensure_ascii=False), encoding="utf-8")
                            os.replace(temporary, mapping_path)
                        finally:
                            temporary.unlink(missing_ok=True)
                    if target.exists():
                        source.unlink()
                    else:
                        source.rename(target)
                elif mapped is None or not target.is_file():
                    continue
                item["original"] = target.relative_to(current).as_posix()
                for asset in manifest.get("assets") or []:
                    if asset.get("original") == old:
                        asset["original"] = item["original"]
                changed = True
        if manifest.get("layoutVersion") != 1:
            manifest["layoutVersion"] = 1
            changed = True
        return changed

    def _manifest_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "manifest.json"

    @staticmethod
    def _normalize_round_depths(manifest: dict[str, Any]) -> None:
        rounds = manifest.get("rounds")
        if not isinstance(rounds, list):
            raise RuntimeError("图片会话清单格式无效")
        round_by_id = {
            str(round_data.get("setId") or ""): round_data
            for round_data in rounds
            if isinstance(round_data, dict) and round_data.get("setId")
        }
        depths: dict[str, int] = {}
        for round_data in rounds:
            set_id = str(round_data.get("setId") or "")
            if not set_id or set_id in depths:
                continue
            trail: list[dict[str, Any]] = []
            seen: set[str] = set()
            pending = round_data
            while pending is not None:
                pending_id = str(pending.get("setId") or "")
                if pending_id in depths:
                    depth = depths[pending_id]
                    break
                if not pending_id or pending_id in seen:
                    raise RuntimeError("图片会话清单分支关系无效")
                seen.add(pending_id)
                trail.append(pending)
                parent_set_id = str(pending.get("parentSetId") or "")
                if not parent_set_id:
                    pending = None
                    continue
                pending = round_by_id.get(parent_set_id)
                if pending is None:
                    raise RuntimeError("图片会话清单续作来源不存在")
            else:
                depth = 0
            for trail_round in reversed(trail):
                depth += 1
                trail_id = str(trail_round.get("setId") or "")
                depths[trail_id] = depth
                trail_round["roundNumber"] = depth

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
        self._normalize_round_depths(payload)
        if payload.get("layoutVersion") != 1:
            if self._ensure_layout(payload):
                self._write_manifest(payload)
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
            clean_parent_set_id = self._safe_id(parent_set_id, "") if parent_set_id else ""
            parent_round = next(
                (
                    round_data
                    for round_data in manifest["rounds"]
                    if round_data.get("setId") == clean_parent_set_id
                ),
                None,
            )
            if clean_parent_set_id and parent_round is None:
                raise ValueError("续作来源轮次不存在")
            round_number = (
                int(parent_round.get("roundNumber") or 0) + 1
                if parent_round is not None
                else 1
            )
            directory_name = f"round-{round_number:03d}-{clean_set_id[:12]}"
            round_data = {
                "roundNumber": round_number,
                "setId": clean_set_id,
                "requestId": clean_set_id,
                "parentSetId": clean_parent_set_id,
                "prompt": str(prompt),
                "requestedCount": int(requested_count),
                "referenceCount": int(reference_count),
                "status": "running",
                "createdAt": self._timestamp(),
                "completedAt": "",
                "directory": directory_name,
                "options": {
                    "size": str(options.get("size") or "auto"),
                    "aspectRatio": str(options.get("aspectRatio") or "auto"),
                    "transparency": str(options.get("transparency") or "auto"),
                    "imageModel": str(options.get("imageModel") or "gpt-image-2"),
                    "quality": str(options.get("quality") or "medium"),
                    "output_format": str(options.get("output_format") or ""),
                    "complexity": str(options.get("complexity") or ""),
                    "resolution": str(options.get("resolution") or "sd"),
                    "frame_count": int(options.get("frame_count") or 9),
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
                    "reasoningDepth": str(options.get("reasoningDepth") or ""),
                    "reasoningSummary": str(options.get("reasoningSummary") or ""),
                    "reasoningDurationMs": max(0, int(options.get("reasoningDurationMs") or 0)),
                    "reasoningUsage": self._reasoning_usage(options.get("reasoningUsage")),
                    "originalPrompt": str(options.get("originalPrompt") or prompt),
                    "webSearchEnabled": bool(options.get("webSearchEnabled")),
                    "webSearchUsed": bool(options.get("webSearchUsed")),
                    "webSearchFailed": bool(options.get("webSearchFailed")),
                    "webSearchResultCount": int(options.get("webSearchResultCount") or 0),
                    "webReferenceCount": int(options.get("webReferenceCount") or 0),
                    **{name: options[name] for name in IMAGE_AGENT_METADATA_FIELDS if name in options},
                },
                "items": [
                    {"itemIndex": item_index, "status": "queued", "error": ""}
                    for item_index in range(int(requested_count))
                ],
            }
            manifest["rounds"].append(round_data)
            self._ensure_layout(manifest)
            (self._session_dir(clean_session_id) / str(round_data["directory"])).mkdir(parents=True, exist_ok=True)
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
        if suffix not in OUTPUT_IMAGE_SUFFIXES:
            suffix = ".png"
        session_dir = self._session_dir(session_id)
        asset_dir = session_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        original_path = asset_dir / f"{asset_id}{suffix}"
        preview_path = asset_dir / f"{asset_id}-preview{image_file_suffix(preview_bytes)}"
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

    def continuation_context(
        self,
        session_id: str,
        set_id: str,
        additional_asset_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
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
            branch_rounds: list[dict[str, Any]] = []
            pending = round_data
            seen: set[str] = set()
            while pending is not None:
                pending_id = str(pending.get("setId") or "")
                if not pending_id or pending_id in seen:
                    raise RuntimeError("图片会话清单分支关系无效")
                seen.add(pending_id)
                branch_rounds.append(pending)
                pending = round_by_id.get(str(pending.get("parentSetId") or ""))
            branch_rounds.reverse()
            session_dir = self._session_dir(clean_session_id)
            dirty = False
            for existing_round in branch_rounds:
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
            for branch_round in branch_rounds:
                branch_id = str(branch_round.get("setId") or "")
                options = branch_round.get("options")
                if not isinstance(options, dict):
                    options = {}
                history.append(
                    {
                        "setId": branch_id,
                        "roundNumber": int(branch_round.get("roundNumber") or 0),
                        "userPrompt": strip_image_prompt_constraints(
                            options.get("originalPrompt") or branch_round.get("prompt") or ""
                        ),
                        "reasoningSummary": str(
                            options.get("reasoningSummary")
                            or options.get("continuationRationale")
                            or ""
                        ),
                        "operation": str(
                            options.get("operation")
                            or ("edit" if branch_round.get("referenceCount") else "generate")
                        ),
                        "inputAssetIds": list(options.get("inputAssetIds") or []),
                        "outputAssetIds": [
                            str(item.get("assetId") or "")
                            for item in branch_round.get("items") or []
                            if item.get("status") == "completed" and item.get("assetId")
                        ],
                    }
                )

            accessible_asset_ids = {
                str(asset_id)
                for asset_id in additional_asset_ids or ()
                if asset_id
            }
            for branch_round in branch_rounds:
                branch_options = branch_round.get("options")
                if not isinstance(branch_options, dict):
                    branch_options = {}
                for field in ("inputAssetIds", "selectedAssetIds"):
                    accessible_asset_ids.update(
                        str(asset_id)
                        for asset_id in branch_options.get(field) or []
                        if asset_id
                    )
                accessible_asset_ids.update(
                    str(item.get("assetId") or "")
                    for item in branch_round.get("items") or []
                    if item.get("assetId")
                )
                accessible_asset_ids.update(
                    str(reference.get("assetId") or "")
                    for reference in branch_round.get("webReferences") or []
                    if reference.get("assetId")
                )
            accessible_asset_ids.discard("")
            branch_set_ids = {
                str(branch_round.get("setId") or "")
                for branch_round in branch_rounds
            }
            for asset in manifest.get("assets") or []:
                if branch_set_ids.intersection(
                    str(source_set_id)
                    for source_set_id in asset.get("sourceSetIds") or []
                ):
                    accessible_asset_ids.add(str(asset.get("assetId") or ""))

            assets: list[dict[str, Any]] = []
            for asset in manifest.get("assets") or []:
                asset_id = str(asset.get("assetId") or "")
                if asset_id not in accessible_asset_ids:
                    continue
                original_path = session_dir / str(asset.get("original") or "")
                preview_path = session_dir / str(asset.get("preview") or "")
                if not original_path.is_file():
                    continue
                assets.append(
                    {
                        "assetId": asset_id,
                        "description": str(asset.get("description") or ""),
                        "describedAt": str(asset.get("describedAt") or ""),
                        "sourceSetIds": [
                            str(source_set_id)
                            for source_set_id in asset.get("sourceSetIds") or []
                            if str(source_set_id) in branch_set_ids
                        ],
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
            output_dir = self._within_root(round_dir / "Output")
            output_dir.mkdir(exist_ok=True)
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in OUTPUT_IMAGE_SUFFIXES else ".png"
            original_path = output_dir / f"image-{item_index + 1:02d}{suffix}"
            shutil.copy2(source_path, original_path)
            original_bytes = original_path.read_bytes()
            preview_bytes = image_preview_bytes(original_bytes)
            preview_path = round_dir / f"image-{item_index + 1:02d}-preview{image_file_suffix(preview_bytes)}"
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
                "previewUri": image_data_url(preview_bytes),
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
                preview_path = round_dir / f"web-reference-{index:02d}-preview{image_file_suffix(preview_bytes)}"
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
                        "previewUri": image_data_url(preview_bytes),
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
                    folder = self._within_root(manifest_path.parent)
                    identity = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(identity, dict):
                        continue
                    session_id = str(identity.get("sessionId") or "")
                    if not session_id or self._safe_id(session_id) != session_id:
                        continue
                    self._directory_cache[(str(self.root.resolve()), session_id)] = folder
                    manifest = self._read_manifest(session_id)
                    if manifest is not None and manifest.get("layoutVersion") != 1 and not any(
                        item.get("status") == "running" for item in manifest.get("rounds") or []
                    ):
                        if self._ensure_layout(manifest):
                            self._write_manifest(manifest)
                except (OSError, RuntimeError, ValueError, TypeError):
                    continue
                if manifest is None:
                    continue
                session_dir = self._session_dir(session_id)
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
                        if not original_path.is_file():
                            continue
                        if not preview_path.is_file():
                            preview_path = original_path
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
                        if not original_path.is_file():
                            continue
                        if not preview_path.is_file():
                            preview_path = original_path
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
                            "prompt": strip_image_prompt_constraints(round_data.get("prompt") or ""),
                            "originalPrompt": strip_image_prompt_constraints(options.get("originalPrompt") or round_data.get("prompt") or ""),
                            "imageModel": str(options.get("imageModel") or "gpt-image-2"),
                            "aspectRatio": str(options.get("aspectRatio") or "auto"),
                            "transparency": str(options.get("transparency") or "auto"),
                            "quality": str(options.get("quality") or "medium"),
                            "outputFormat": str(options.get("output_format") or next((item.get("format") for item in round_data.get("items") or [] if item.get("format")), "png")),
                            "complexity": str(options.get("complexity") or "auto"),
                            "resolution": str(options.get("resolution") or "sd"),
                            "frameCount": int(options.get("frame_count") or 9),
                            "operation": str(options.get("operation") or ("edit" if round_data.get("referenceCount") else "generate")),
                            "continuation": bool(options.get("continuation")),
                            "continuationRationale": str(options.get("continuationRationale") or ""),
                            "selectedAssetIds": list(options.get("selectedAssetIds") or []),
                            "inputAssetIds": list(options.get("inputAssetIds") or []),
                            "reasoningMode": reasoning_mode,
                            "reasoningModel": str(options.get("reasoningModel") or ""),
                            "reasoningEffort": str(options.get("reasoningEffort") or ""),
                            "reasoningDepth": str(options.get("reasoningDepth") or options.get("reasoningEffort") or ""),
                            "reasoningSummary": reasoning_summary,
                            "reasoningDurationMs": max(0, int(options.get("reasoningDurationMs") or 0)),
                            "reasoningUsage": self._reasoning_usage(options.get("reasoningUsage")),
                            "reasoningStatus": "completed" if reasoning_summary else "idle",
                            "effectivePrompt": strip_image_prompt_constraints(round_data.get("prompt") or "") if reasoning_mode != "instant" or reasoning_summary else "",
                            "webSearchEnabled": bool(options.get("webSearchEnabled")),
                            "webSearchUsed": bool(options.get("webSearchUsed")),
                            "webSearchFailed": bool(options.get("webSearchFailed")),
                            "webSearchResultCount": int(options.get("webSearchResultCount") or 0),
                            "webReferenceCount": len(web_references),
                            "webReferences": web_references,
                            **{name: options[name] for name in IMAGE_AGENT_METADATA_FIELDS if name in options},
                            "createdAt": str(round_data.get("createdAt") or manifest.get("createdAt") or ""),
                            "status": str(round_data.get("status") or "completed"),
                            "items": items,
                        }
                    )
        restored.sort(key=lambda item: (item["createdAt"], item["roundNumber"]), reverse=True)
        return restored

    def original_paths_for_set(self, session_id: str, set_id: str) -> list[dict[str, Any]]:
        clean_session_id = self._safe_id(session_id)
        clean_set_id = self._safe_id(set_id)
        with self._manifest_lock:
            manifest = self._read_manifest(clean_session_id)
            if manifest is None:
                return []
            round_data = next(
                (item for item in manifest.get("rounds") or [] if item.get("setId") == clean_set_id),
                None,
            )
            if round_data is None:
                return []
            session_dir = self._session_dir(clean_session_id).resolve()
            round_dir = (session_dir / str(round_data.get("directory") or "")).resolve()
            if not round_dir.is_relative_to(session_dir) or not round_dir.is_dir():
                return []
            originals: list[dict[str, Any]] = []
            for item_data in round_data.get("items") or []:
                if item_data.get("status") != "completed":
                    continue
                original_path = (session_dir / str(item_data.get("original") or "")).resolve()
                if not original_path.is_file() or not original_path.is_relative_to(round_dir):
                    continue
                originals.append(
                    {
                        "itemIndex": int(item_data.get("itemIndex") or 0),
                        "path": str(original_path),
                        "suffix": original_path.suffix.lower(),
                    }
                )
            return originals

    def folder_for_set(self, session_id: str, set_id: str = "") -> Path:
        with self._manifest_lock:
            manifest = self._read_manifest(self._safe_id(session_id))
            if manifest is None:
                raise ValueError("图片项目不存在或尚未保存")
            session = self._within_root(self._session_dir(str(manifest["sessionId"])))
            if not set_id:
                return session
            clean_set_id = self._safe_id(set_id)
            project = next((item for item in manifest.get("rounds") or [] if item.get("setId") == clean_set_id), None)
            if project is None:
                raise ValueError("图片集不存在")
            folder = self._within_root(session / str(project.get("directory") or ""))
            if folder.parent != session or not folder.is_dir():
                raise ValueError("图片集目录无效")
            return folder

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

    def _cleanup_committed_paths(self, paths: list[Path]) -> None:
        for path in paths:
            try:
                path = self._within_root(path)
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
    transparency: str
    requested_quality: str
    stream: bool
    partial_images: int


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def strip_image_prompt_constraints(prompt: str) -> str:
    clean_prompt = str(prompt or "").strip()
    legacy_suffix = re.compile(
        r"(?:\s*\[内部输出约束\][\s\S]*?\[/内部输出约束\]|"
        r"\s*以以下比例要求为准：强制生成比例为\d+:\d+的图片|"
        r"\s*以以下透明度要求为准：强制生成(?:不透明|透明)背景的图片)$"
    )
    while legacy_suffix.search(clean_prompt):
        clean_prompt = legacy_suffix.sub("", clean_prompt).rstrip()
    return clean_prompt


def prepare_image_generation(
    prompt: str,
    image_paths: list[str],
    options: dict[str, Any] | None = None,
    *,
    app_models: dict[str, Any] | None = None,
) -> ImageGenerationRequest:
    clean_prompt = strip_image_prompt_constraints(prompt)
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
        if image_path.suffix.lower() not in OUTPUT_IMAGE_SUFFIXES:
            raise ValueError(f"不支持的图片格式：{image_path.name}")
        if image_path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError(f"参考图片不能超过 50 MB：{image_path.name}")
        try:
            image_path = reference_raster_path(image_path)
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
    quality = str(clean_options.get("quality") or "medium").lower()
    size = str(clean_options.get("size") or "auto").lower()
    ratio = str(clean_options.get("aspectRatio") or "auto")
    if ratio != "auto" and ratio not in IMAGE_SIZES_BY_RATIO:
        raise ValueError("无效的图片比例")
    if size == "auto":
        size = IMAGE_SIZES_BY_RATIO.get(ratio, "auto")
    transparency = str(clean_options.get("background") or clean_options.get("transparency") or "auto").lower()
    complexity = str(clean_options.get("complexity") or "").lower()
    moderation = str(clean_options.get("moderation") or "low").lower()
    stream = bool(clean_options.get("stream"))
    partial_images = int(_number(clean_options.get("partial_images", clean_options.get("partialImages")), 0))
    if output_preset not in OUTPUT_PRESETS:
        raise ValueError("无效的输出大小")
    fallback_format = OUTPUT_PRESETS[output_preset]
    if transparency == "transparent" and fallback_format == "jpeg":
        fallback_format = "png"
    output_format = str(clean_options.get("output_format") or fallback_format).lower()
    formats = {"png", "jpeg", "webp", "gif"}
    svg = (app_models or {}).get("svg") or {}
    if svg.get("enabled") is True:
        formats.add("svg")
    if output_format not in formats:
        raise ValueError("所选输出格式未开放")
    if output_format == "svg":
        if operation != "generate" or valid_paths or clean_options.get("continuation") or _number(clean_options.get("imageCount"), 1) != 1:
            raise ValueError("SVG 仅支持单张无参考图生成")
    if output_format in {"svg", "gif"}:
        stream = False
        partial_images = 0
    if transparency == "transparent" and output_format not in {"png", "webp"}:
        raise ValueError("透明背景需要 PNG 或 WebP")
    compression = clean_options.get("output_compression")
    if compression is not None and (type(compression) is not int or not 0 <= compression <= 100 or output_format not in {"jpeg", "webp"}):
        raise ValueError("压缩质量仅适用于 JPEG 和 WebP，范围为 0 至 100")
    frames = {}
    for name, minimum, maximum, message in (
        ("frame_count", 2, 16, "GIF 帧数须为 2 至 16"),
        ("frame_duration_ms", 20, 2000, "GIF 每帧时长须为 20 至 2000 毫秒"),
    ):
        value = clean_options.get(name)
        if value is None:
            continue
        if output_format != "gif":
            raise ValueError("帧数和每帧时长仅适用于 GIF")
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(message)
        frames[name] = value
    if quality not in {"low", "medium", "high", "xhigh", "max", "auto"}:
        raise ValueError("无效的图片质量")
    if complexity not in {"", "low", "medium", "high"}:
        raise ValueError("无效的图片复杂度")
    if transparency not in {"auto", "opaque", "transparent"}:
        raise ValueError("无效的透明度模式")
    if size != "auto":
        size_match = re.fullmatch(r"([1-9]\d{0,3})x([1-9]\d{0,3})", size)
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
    if moderation not in {"low", "auto"}:
        raise ValueError("无效的审核级别")
    if not 0 <= partial_images <= 3:
        raise ValueError("流式预览图数量必须在 0 到 3 之间")

    profile = app_models or {}
    default_model = str(profile.get("defaultImage") or "gpt-image-2")
    allowed_models = profile.get("imageModels") or IMAGE_MODEL_IDS
    image_model = str(clean_options.get("imageModel") or default_model).strip()
    if image_model not in allowed_models:
        raise ValueError("所选图片模型未开放，请刷新配置后重新选择")
    fields = {
        "model": image_model,
        "prompt": clean_prompt,
        "size": size,
        "quality": quality,
        "output_format": output_format,
        "background": transparency,
        "moderation": moderation,
        "stream": stream,
        **frames,
    }
    if output_format == "svg":
        author = {}
        if svg.get("model"):
            author["model"] = svg["model"]
        if svg.get("effort"):
            author["reasoning"] = {"effort": svg["effort"]}
        if author:
            fields["svg"] = author
    if complexity:
        fields["complexity"] = complexity
    if output_format in {"jpeg", "webp"}:
        fields["output_compression"] = 75
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
        transparency=transparency,
        requested_quality=quality,
        stream=stream,
        partial_images=partial_images,
    )


class ImageResponseError(ProviderResponseError):
    def __init__(self, message: str) -> None:
        super().__init__("incomplete_stream", message)


class ImageGenerationClient:
    @staticmethod
    def _opener() -> urllib.request.OpenerDirector:
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: Any, **kwargs: Any) -> None:
                return None

        return urllib.request.build_opener(NoRedirectHandler())

    @staticmethod
    def _error(
        exc: urllib.error.HTTPError,
        secret: str,
        body_text: str | None = None,
    ) -> RuntimeError:
        if body_text is None:
            body_text = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body_text)
            problem = parsed.get("error") or {}
            message = problem.get("message") or parsed.get("message")
            code = str(problem.get("code") or problem.get("type") or "upstream_error")
        except (ValueError, AttributeError, TypeError):
            message = body_text[:200]
            code = "upstream_error"
        headers = exc.headers or {}
        return ProviderResponseError(code, f"HTTP {exc.code}: {safe_text(message or exc.reason, (secret,))}",
            status=exc.code, headers=headers,
            request_id=headers.get("x-request-id") or headers.get("x-mirra-diagnostic-id"))

    @staticmethod
    def _read_response(
        response: Any,
        stream: bool,
        on_partial: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        headers = getattr(response, "headers", None) or {}
        content_type = headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if content_type == "application/json" or (not stream and content_type != "text/event-stream"):
            try:
                result = json.load(response)
            except (ValueError, http.client.IncompleteRead, OSError) as exc:
                raise ImageResponseError("上游响应中断或缺损，未收到完整图片") from exc
            if not isinstance(result, dict):
                raise ImageResponseError("上游图片响应格式无效")
            return result

        def image_data(payload: Any) -> str:
            if isinstance(payload, dict):
                for key in ("b64_json", "partial_image_b64"):
                    direct = payload.get(key)
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

        def process_event(event: Any, sse_event_type: str) -> None:
            nonlocal completed, completed_image, last_partial_image, partial_count
            event_name = sse_event_type.strip().lower()
            named_completion = event_name in {"completed", "complete", "done"} or event_name.endswith(
                (".completed", ".complete", ".done")
            )
            if event == "[DONE]":
                if named_completion:
                    completed = {"type": event_name}
                return
            if not isinstance(event, dict):
                return
            event_type = str(event.get("type") or event_name).strip().lower()
            if event_type == "error" or event_type.endswith((".error", ".failed")) or event.get("error"):
                problem = event.get("error") or (event.get("response") or {}).get("error") or event
                if not isinstance(problem, dict):
                    problem = {"message": str(problem)}
                raise ProviderResponseError(str(problem.get("code") or "upstream_error"),
                    str(problem.get("message") or "上游生图服务报告了生成失败"),
                    status=problem.get("status"), headers=headers,
                    request_id=event.get("request_id") or headers.get("x-request-id") or headers.get("x-mirra-diagnostic-id"))
            encoded_image = image_data(event)
            if event_type.endswith(".partial_image"):
                if encoded_image and completed is None:
                    partial_count += 1
                    last_partial_image = encoded_image
                    if on_partial is not None:
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
        pending_data: list[bytes] = []
        malformed_event = False
        transport_error = None

        def flush_pending(*, boundary: bool) -> None:
            nonlocal pending_event_type, malformed_event
            if not pending_data:
                if boundary:
                    pending_event_type = ""
                return
            payload = b"\n".join(pending_data)
            try:
                event = "[DONE]" if payload.strip() == b"[DONE]" else json.loads(payload)
            except ValueError:
                if not boundary:
                    return
                malformed_event = True
            else:
                process_event(event, pending_event_type)
            pending_data.clear()
            pending_event_type = ""

        try:
            for raw_line in response:
                line = raw_line.rstrip(b"\r\n")
                if not line:
                    flush_pending(boundary=True)
                elif line.startswith(b"event:"):
                    flush_pending(boundary=True)
                    pending_event_type = line[6:].strip().decode("utf-8", "replace")
                elif line.startswith(b"data:"):
                    pending_data.append(line[5:].lstrip())
                    flush_pending(boundary=False)
                elif line.lstrip().startswith(b"{"):
                    flush_pending(boundary=True)
                    pending_data.append(line.strip())
                    flush_pending(boundary=True)
        except (http.client.IncompleteRead, OSError) as exc:
            transport_error = exc
        flush_pending(boundary=True)
        if completed is None or not completed_image:
            if transport_error is not None or malformed_event:
                raise ImageResponseError("上游图片流中断或缺损，未收到完整成图") from transport_error
            raise ImageResponseError("流式生图接口未返回最终图片")
        return {
            "data": [{"b64_json": completed_image}],
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
        task_context: Any = None,
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/images/generations"
        request_id = new_network_request_id()
        request_started_at = time.perf_counter()
        body = json.dumps(fields, ensure_ascii=False).encode("utf-8")
        request_details = {
            "operation": "generation",
            "model": str(fields.get("model") or ""),
            "size": str(fields.get("size") or ""),
            "quality": str(fields.get("quality") or ""),
            "outputFormat": str(fields.get("output_format") or ""),
            "moderation": str(fields.get("moderation") or ""),
            "stream": bool(fields.get("stream")),
            "partialImages": int(fields.get("partial_images") or 0),
            "requestBytes": len(body),
            "timeoutSeconds": timeout,
        }
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "text/event-stream" if fields.get("stream") else "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "DJYX_APITOOL/image-generation",
            },
            method="POST",
        )
        try:
            response_context = open_request(request, timeout=timeout, opener=cls._opener().open)
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", "replace")
            log_network_error(
                "image_generation_http_error",
                request_id=request_id,
                method="POST",
                url=url,
                error=exc,
                status=exc.code,
                elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                attempt=1,
                response_headers=exc.headers,
                response_body=response_body,
                details=request_details,
                secrets=(secret,),
            )
            error = cls._error(exc, secret, response_body)
            error.request_id = error.request_id or request_id
            raise error from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log_network_error(
                "image_generation_connect_error",
                request_id=request_id,
                method="POST",
                url=url,
                error=exc,
                elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                attempt=1,
                details=request_details,
                secrets=(secret,),
            )
            raise RuntimeError(
                f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
                f"（诊断 ID: {request_id}）"
            ) from None
        response_headers = getattr(response_context, "headers", None)
        response_status = getattr(response_context, "status", None)
        try:
            with response_context as response:
                if task_context is not None:
                    task_context.add_response(response)
                try:
                    result = cls._read_response(response, bool(fields.get("stream")), on_partial)
                    if task_context is not None:
                        task_context.check_cancelled()
                    return result
                finally:
                    if task_context is not None:
                        task_context.remove_response(response)
        except Exception as exc:
            if task_context is not None:
                task_context.check_cancelled()
            log_network_error(
                "image_generation_stream_error",
                request_id=request_id,
                method="POST",
                url=url,
                error=exc,
                status=response_status,
                elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                attempt=1,
                response_headers=response_headers,
                details=request_details,
                secrets=(secret,),
            )
            if isinstance(exc, ProviderResponseError):
                exc.request_id = exc.request_id or request_id
                exc.headers = exc.headers or response_headers or {}
                exc.status_code = exc.status_code or response_status
                raise
            if not isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
                raise
            raise RuntimeError(
                f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
                f"（诊断 ID: {request_id}）"
            ) from None

    @staticmethod
    def edit_images(
        base_url: str,
        secret: str,
        image_paths: tuple[Path, ...],
        fields: dict[str, Any],
        timeout: int = 600,
        on_partial: Callable[[str, int], None] | None = None,
        task_context: Any = None,
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/images/edits"
        request_id = new_network_request_id()
        request_started_at = time.perf_counter()
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

        request_body = body.getvalue()
        request_details = {
            "operation": "edit",
            "model": str(fields.get("model") or ""),
            "size": str(fields.get("size") or ""),
            "size": str(fields.get("size") or ""),
            "quality": str(fields.get("quality") or ""),
            "outputFormat": str(fields.get("output_format") or ""),
            "background": str(fields.get("background") or ""),
            "moderation": str(fields.get("moderation") or ""),
            "stream": bool(fields.get("stream")),
            "partialImages": int(fields.get("partial_images") or 0),
            "referenceCount": len(image_paths),
            "requestBytes": len(request_body),
            "timeoutSeconds": timeout,
        }
        request = urllib.request.Request(
            url,
            data=request_body,
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "text/event-stream" if fields.get("stream") else "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "DJYX_APITOOL/image-generation",
            },
            method="POST",
        )
        try:
            response_context = open_request(request, timeout=timeout, opener=ImageGenerationClient._opener().open)
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", "replace")
            log_network_error(
                "image_edit_http_error",
                request_id=request_id,
                method="POST",
                url=url,
                error=exc,
                status=exc.code,
                elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                attempt=1,
                response_headers=exc.headers,
                response_body=response_body,
                details=request_details,
                secrets=(secret,),
            )
            error = ImageGenerationClient._error(exc, secret, response_body)
            error.request_id = error.request_id or request_id
            raise error from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log_network_error(
                "image_edit_connect_error",
                request_id=request_id,
                method="POST",
                url=url,
                error=exc,
                elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                attempt=1,
                details=request_details,
                secrets=(secret,),
            )
            raise RuntimeError(
                f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
                f"（诊断 ID: {request_id}）"
            ) from None
        response_headers = getattr(response_context, "headers", None)
        response_status = getattr(response_context, "status", None)
        try:
            with response_context as response:
                if task_context is not None:
                    task_context.add_response(response)
                try:
                    result = ImageGenerationClient._read_response(response, bool(fields.get("stream")), on_partial)
                    if task_context is not None:
                        task_context.check_cancelled()
                    return result
                finally:
                    if task_context is not None:
                        task_context.remove_response(response)
        except Exception as exc:
            if task_context is not None:
                task_context.check_cancelled()
            log_network_error(
                "image_edit_stream_error",
                request_id=request_id,
                method="POST",
                url=url,
                error=exc,
                status=response_status,
                elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                attempt=1,
                response_headers=response_headers,
                details=request_details,
                secrets=(secret,),
            )
            if isinstance(exc, ProviderResponseError):
                exc.request_id = exc.request_id or request_id
                exc.headers = exc.headers or response_headers or {}
                exc.status_code = exc.status_code or response_status
                raise
            if not isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
                raise
            raise RuntimeError(
                f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
                f"（诊断 ID: {request_id}）"
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
        task_context: Any = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        generation_id = uuid.uuid4().hex[:12]
        fields = request.fields
        if base_url.rstrip("/") == "https://api.openai.com/v1" and fields["model"] == "gpt-image-2.5":
            fields = {**fields, "model": "gpt-image-2.5-flare"}

        def persist_partial(image_data: str, partial_index: int) -> None:
            try:
                partial_bytes = base64.b64decode(image_data, validate=True)
                with Image.open(io.BytesIO(partial_bytes)) as partial_image:
                    width, height = partial_image.size
                    suffix = IMAGE_EXTENSIONS.get(partial_image.format)
                    partial_image.verify()
                if suffix is None:
                    return
                partial_dir = output_dir / "partials"
                partial_dir.mkdir(parents=True, exist_ok=True)
                partial_path = partial_dir / f"{generation_id}-{partial_index}{suffix}"
                partial_path.write_bytes(partial_bytes)
                if on_partial is not None:
                    on_partial(
                        {
                            "partialIndex": partial_index,
                            "partialTotal": request.partial_images,
                            "path": str(partial_path),
                            "uri": partial_path.as_uri(),
                            "width": width,
                            "height": height,
                            "previewUri": image_preview_data_url(
                                partial_bytes,
                                max_side=512,
                                optimize=False,
                            ),
                        }
                    )
            except (binascii.Error, OSError, ValueError):
                return

        call_options: dict[str, Any] = {"on_partial": persist_partial}
        client_method = self.client.edit_images if request.image_paths else self.client.generate_image
        parameters = inspect.signature(client_method).parameters
        if task_context is not None and ("task_context" in parameters or any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())):
            call_options["task_context"] = task_context
        if task_context is not None:
            task_context.check_cancelled()
        if request.image_paths:
            response = self.client.edit_images(
                base_url,
                secret,
                request.image_paths,
                fields,
                **call_options,
            )
        else:
            response = self.client.generate_image(
                base_url,
                secret,
                fields,
                **call_options,
            )
        image_data = ((response.get("data") or [{}])[0] or {}).get("b64_json")
        if not image_data:
            raise RuntimeError("生图接口未返回图片数据")
        try:
            result_bytes = base64.b64decode(image_data, validate=True)
        except binascii.Error as exc:
            raise RuntimeError("生图接口返回了无效图片数据") from exc

        if request.output_format == "svg":
            dimensions = svg_size(result_bytes)
            if dimensions is None:
                raise RuntimeError("生图接口未返回 SVG 内容")
            width, height = dimensions
            suffix, image_format = ".svg", "svg"
        else:
            with Image.open(io.BytesIO(result_bytes)) as source_image:
                if request.output_format == "gif" and (source_image.format != "GIF" or source_image.n_frames < 2):
                    raise RuntimeError("生图接口未返回 GIF 动图")
                width, height = source_image.size
                suffix = IMAGE_EXTENSIONS.get(source_image.format)
                image_format = str(source_image.format).lower()
                source_image.verify()
        if suffix is None:
            raise RuntimeError("生图接口返回了不支持的图片格式")
        output_path = output_dir / (
            f"image-generation-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}{suffix}"
        )
        preview_uri = image_preview_data_url(result_bytes)
        output_path.write_bytes(result_bytes)
        return {
            "ok": True,
            "path": str(output_path),
            "uri": output_path.as_uri(),
            "previewUri": preview_uri,
            "width": width,
            "height": height,
            "sizeBytes": len(result_bytes),
            "format": image_format,
            "outputPreset": request.output_preset,
            "quality": response.get("quality") or request.fields["quality"],
            "requestedQuality": request.requested_quality,
            "requestedSize": request.requested_size,
            "actualSize": response.get("size") or f"{width}x{height}",
            "stream": request.stream,
            "partialImagesRequested": request.partial_images if request.stream else 0,
            "partialImagesReceived": int(response.get("partial_images_received") or 0),
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "svgUsage": response.get("svg_usage") if isinstance(response.get("svg_usage"), dict) else {},
            "referenceCount": len(request.image_paths),
            "operation": request.operation,
            "transportOperation": "edit" if request.image_paths else "generate",
        }
