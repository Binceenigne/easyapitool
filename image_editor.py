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


@dataclass(frozen=True)
class ImageEditRequest:
    prompt: str
    image_paths: tuple[Path, ...]
    fields: dict[str, str]
    output_format: str
    requested_size: str


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def prepare_image_edit(
    prompt: str,
    image_paths: list[str],
    options: dict[str, Any] | None = None,
) -> ImageEditRequest:
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("请输入图片编辑要求")
    if len(clean_prompt) > 32_000:
        raise ValueError("图片编辑要求不能超过 32000 个字符")
    if not isinstance(image_paths, list) or not 1 <= len(image_paths) <= 16:
        raise ValueError("请选择 1 到 16 张参考图片")

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
    output_format = str(clean_options.get("outputFormat") or "png").lower()
    quality = str(clean_options.get("quality") or "auto").lower()
    size = str(clean_options.get("size") or "auto").lower()
    background = str(clean_options.get("background") or "auto").lower()
    moderation = str(clean_options.get("moderation") or "auto").lower()
    if output_format not in {"png", "jpeg", "webp"}:
        raise ValueError("无效的输出格式")
    if quality not in {"low", "medium", "high", "auto"}:
        raise ValueError("无效的图片质量")
    if size not in {"1024x1024", "1536x1024", "1024x1536", "auto"}:
        raise ValueError("无效的图片尺寸")
    if background not in {"opaque", "auto"}:
        raise ValueError("gpt-image-2 仅支持自动或不透明背景")
    if moderation not in {"low", "auto"}:
        raise ValueError("无效的审核级别")

    fields = {
        "model": "gpt-image-2",
        "prompt": clean_prompt,
        "quality": quality,
        "size": size,
        "output_format": output_format,
        "background": background,
        "moderation": moderation,
    }
    if output_format in {"jpeg", "webp"}:
        compression = int(_number(clean_options.get("outputCompression"), 90))
        if not 0 <= compression <= 100:
            raise ValueError("输出压缩率必须在 0 到 100 之间")
        fields["output_compression"] = str(compression)

    return ImageEditRequest(
        prompt=clean_prompt,
        image_paths=tuple(valid_paths),
        fields=fields,
        output_format=output_format,
        requested_size=size,
    )


class ImageEditClient:
    @staticmethod
    def edit_images(
        base_url: str,
        secret: str,
        image_paths: tuple[Path, ...],
        fields: dict[str, str],
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
            write_part(
                [f'Content-Disposition: form-data; name="{name}"'],
                value.encode("utf-8"),
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

        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: Any, **kwargs: Any) -> None:
                return None

        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/images/edits",
            data=body.getvalue(),
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(body_text)
                message = (parsed.get("error") or {}).get("message") or parsed.get("message")
            except json.JSONDecodeError:
                message = body_text[:200]
            safe_message = str(message or exc.reason)
            if secret:
                safe_message = safe_message.replace(secret, "[REDACTED]")
            raise RuntimeError(f"HTTP {exc.code}: {safe_message}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
            ) from None


class ImageEditorService:
    def __init__(self, client: ImageEditClient | None = None) -> None:
        self.client = client or ImageEditClient()

    def edit(
        self,
        base_url: str,
        secret: str,
        request: ImageEditRequest,
        output_dir: Path,
    ) -> dict[str, Any]:
        response = self.client.edit_images(
            base_url,
            secret,
            request.image_paths,
            request.fields,
        )
        image_data = ((response.get("data") or [{}])[0] or {}).get("b64_json")
        if not image_data:
            raise RuntimeError("图片编辑接口未返回图片数据")
        try:
            result_bytes = base64.b64decode(image_data, validate=True)
        except binascii.Error as exc:
            raise RuntimeError("图片编辑接口返回了无效图片数据") from exc

        actual_format = str(response.get("output_format") or request.output_format).lower()
        with Image.open(io.BytesIO(result_bytes)) as result_image:
            width, height = result_image.size
            verified_format = str(result_image.format or actual_format).lower()

        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = {"jpeg": ".jpg", "webp": ".webp"}.get(actual_format, ".png")
        output_path = output_dir / (
            f"image-edit-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}{suffix}"
        )
        output_path.write_bytes(result_bytes)
        return {
            "ok": True,
            "path": str(output_path),
            "uri": output_path.as_uri(),
            "width": width,
            "height": height,
            "sizeBytes": len(result_bytes),
            "format": verified_format,
            "quality": response.get("quality") or request.fields["quality"],
            "requestedSize": request.requested_size,
            "actualSize": response.get("size") or f"{width}x{height}",
        }