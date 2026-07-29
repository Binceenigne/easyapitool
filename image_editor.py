from __future__ import annotations

import base64
import binascii
import io
import json
import shutil
import threading
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


def image_preview_bytes(image_bytes: bytes, max_side: int = 720) -> bytes:
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
    preview_image.save(preview_buffer, format="JPEG", quality=78, optimize=True)
    return preview_buffer.getvalue()


def image_preview_data_url(image_bytes: bytes, max_side: int = 720) -> str:
    encoded = base64.b64encode(image_preview_bytes(image_bytes, max_side)).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class ImageSessionStore:
    SCHEMA_VERSION = 1
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

    def _session_dir(self, session_id: str) -> Path:
        return self.root / self._safe_id(session_id)

    def _manifest_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "manifest.json"

    def _read_manifest(self, session_id: str) -> dict[str, Any] | None:
        manifest_path = self._manifest_path(session_id)
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        manifest["updatedAt"] = self._timestamp()
        manifest["roundCount"] = len(manifest.get("rounds") or [])
        manifest_path = self._manifest_path(str(manifest["sessionId"]))
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = manifest_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)

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
            self._update_item(clean_session_id, clean_set_id, item_index, item_data)
            return {
                **result,
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
                manifest = self._read_manifest(manifest_path.parent.name)
                if manifest is None:
                    continue
                session_dir = manifest_path.parent
                for round_data in manifest.get("rounds") or []:
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
                        preview_uri = f"data:image/jpeg;base64,{base64.b64encode(preview_path.read_bytes()).decode('ascii')}"
                        result = {
                            "ok": True,
                            "itemIndex": item_index,
                            "path": str(original_path),
                            "uri": original_path.as_uri(),
                            "previewPath": str(preview_path),
                            "previewUri": preview_uri,
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
                                "uri": preview_uri,
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
                            "createdAt": str(round_data.get("createdAt") or manifest.get("createdAt") or ""),
                            "status": str(round_data.get("status") or "completed"),
                            "items": items,
                        }
                    )
        restored.sort(key=lambda item: (item["createdAt"], item["roundNumber"]), reverse=True)
        return restored

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
            round_dir = self._session_dir(clean_session_id) / str(round_data.get("directory") or "")
            if round_dir.is_dir() and round_dir.parent == self._session_dir(clean_session_id):
                shutil.rmtree(round_dir)
            manifest["rounds"] = [
                item for item in manifest["rounds"] if item.get("setId") != clean_set_id
            ]
            if manifest["rounds"]:
                self._write_manifest(manifest)
            else:
                shutil.rmtree(self._session_dir(clean_session_id), ignore_errors=True)
            return True


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    image_paths: tuple[Path, ...]
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

        completed: dict[str, Any] | None = None
        partial_count = 0
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            event_type = str(event.get("type") or "")
            if event_type == "image_generation.partial_image":
                partial_count += 1
                partial_data = str(event.get("b64_json") or "")
                if partial_data and on_partial is not None:
                    on_partial(partial_data, partial_count)
            elif event_type == "image_generation.completed":
                completed = event
        if completed is None or not completed.get("b64_json"):
            raise RuntimeError("流式生图接口未返回最终图片")
        return {
            "data": [{"b64_json": completed["b64_json"]}],
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
            if on_partial is None:
                return
            try:
                partial_bytes = base64.b64decode(image_data, validate=True)
                with Image.open(io.BytesIO(partial_bytes)) as partial_image:
                    partial_image.verify()
                partial_dir = output_dir / "partials"
                partial_dir.mkdir(parents=True, exist_ok=True)
                partial_path = partial_dir / f"{generation_id}-{partial_index}.png"
                partial_path.write_bytes(partial_bytes)
                on_partial(
                    {
                        "partialIndex": partial_index,
                        "path": str(partial_path),
                        "uri": partial_path.as_uri(),
                        "previewUri": image_preview_data_url(partial_bytes),
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
        }