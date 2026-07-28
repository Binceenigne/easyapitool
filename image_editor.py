from __future__ import annotations

import base64
import binascii
import io
import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
    def _read_response(response: Any, stream: bool) -> dict[str, Any]:
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
                return cls._read_response(response, bool(fields.get("stream")))
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
                return ImageGenerationClient._read_response(response, bool(fields.get("stream")))
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
    ) -> dict[str, Any]:
        if request.image_paths:
            response = self.client.edit_images(
                base_url,
                secret,
                request.image_paths,
                request.fields,
            )
        else:
            response = self.client.generate_image(base_url, secret, request.fields)
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

        output_dir.mkdir(parents=True, exist_ok=True)
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