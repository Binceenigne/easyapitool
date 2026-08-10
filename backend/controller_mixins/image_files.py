from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class ImageFilesMixin:
    def choose_edit_images(self) -> dict[str, Any]:
        if not self.window:
            return {"ok": False, "error": "应用窗口尚未就绪"}
        selected = self.window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=str(windows_pictures_dir()),
            allow_multiple=True,
            file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.webp)",),
        )
        selected_paths = [str(Path(path).resolve()) for path in selected or []]
        eligible_paths = [
            path
            for path in selected_paths
            if Path(path).is_file() and Path(path).stat().st_size <= 50 * 1024 * 1024
        ]
        oversized_count = len(selected_paths) - len(eligible_paths)
        paths = eligible_paths[:16]
        warnings = []
        if len(eligible_paths) > 16:
            warnings.append("参考图片最多 16 张，已仅导入前 16 张")
        if oversized_count:
            warnings.append(f"{oversized_count} 张图片超过 50 MB，未导入")
        return {
            "ok": True,
            "paths": paths,
            "warning": "；".join(warnings),
            "files": [
                {
                    "name": Path(path).name,
                    "sizeBytes": Path(path).stat().st_size,
                    "uri": Path(path).as_uri(),
                    "previewUri": image_preview_data_url(Path(path).read_bytes()),
                }
                for path in paths
                if Path(path).is_file()
            ],
        }

    def import_reference_image(self, data_url: str, name: str = "") -> dict[str, Any]:
        raw_data = str(data_url or "")
        if not raw_data.startswith("data:image/") or "," not in raw_data:
            return {"ok": False, "error": "只支持粘贴或拖入图片文件"}
        header, encoded = raw_data.split(",", 1)
        mime_type = header[5:].split(";", 1)[0].lower()
        suffixes = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }
        suffix = suffixes.get(mime_type)
        if suffix is None or ";base64" not in header.lower():
            return {"ok": False, "error": "仅支持 PNG、JPEG 或 WebP 图片"}
        if len(encoded) > 68 * 1024 * 1024:
            return {"ok": False, "error": "图片超过 50 MB，未导入"}
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return {"ok": False, "error": "图片数据无效"}
        if len(image_bytes) > 50 * 1024 * 1024:
            return {"ok": False, "error": "图片超过 50 MB，未导入"}
        try:
            with Image.open(io.BytesIO(image_bytes)) as source_image:
                source_image.verify()
        except (OSError, ValueError):
            return {"ok": False, "error": "图片内容无效"}

        reference_dir = app_data_dir() / "image-references"
        reference_dir.mkdir(parents=True, exist_ok=True)
        clean_stem = Path(str(name or "reference")).stem.strip()[:48] or "reference"
        safe_stem = "".join(character for character in clean_stem if character.isalnum() or character in "-_ ").strip() or "reference"
        output_path = reference_dir / f"{safe_stem}-{uuid.uuid4().hex[:8]}{suffix}"
        output_path.write_bytes(image_bytes)
        return {
            "ok": True,
            "path": str(output_path),
            "name": output_path.name,
            "sizeBytes": len(image_bytes),
            "uri": output_path.as_uri(),
            "previewUri": image_preview_data_url(image_bytes),
        }

    def load_generated_image(self, source_path: str) -> dict[str, Any]:
        source = Path(str(source_path or "")).resolve()
        allowed_roots = (
            (app_data_dir() / "image-generations").resolve(),
            generated_pictures_dir().resolve(),
        )
        if not source.is_file() or not any(source.is_relative_to(root) for root in allowed_roots):
            return {"ok": False, "error": "只能读取本应用生成的图片"}
        content_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        content_type = content_types.get(source.suffix.lower())
        if content_type is None or source.stat().st_size > 50 * 1024 * 1024:
            return {"ok": False, "error": "生成图片格式或大小无效"}
        try:
            image_bytes = source.read_bytes()
            with Image.open(io.BytesIO(image_bytes)) as generated_image:
                generated_image.verify()
        except (OSError, ValueError):
            return {"ok": False, "error": "生成图片内容无效"}
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return {
            "ok": True,
            "path": str(source),
            "dataUrl": f"data:{content_type};base64,{encoded}",
        }

    def copy_generated_image(self, source_path: str) -> dict[str, Any]:
        source = Path(str(source_path or "")).resolve()
        pictures_root = generated_pictures_dir().resolve()
        if not source.is_file() or not source.is_relative_to(pictures_root):
            return {"ok": False, "error": "只能复制本应用保存的最终图片"}
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return {"ok": False, "error": "最终图片格式无效"}
        try:
            copy_image_to_windows_clipboard(source)
        except (OSError, RuntimeError, ValueError) as exc:
            return {"ok": False, "error": f"复制图片失败：{exc}"}
        return {"ok": True, "path": str(source)}

    @staticmethod
    def _image_session_store() -> ImageSessionStore:
        return ImageSessionStore(generated_pictures_dir())

    def _image_session_activity_state(self) -> tuple[threading.Lock, set[str]]:
        lock = getattr(self, "image_session_activity_lock", None)
        if lock is None:
            lock = threading.Lock()
            self.image_session_activity_lock = lock
        active_sessions = getattr(self, "active_image_sessions", None)
        if active_sessions is None:
            active_sessions = set()
            self.active_image_sessions = active_sessions
        return lock, active_sessions

    def _reserve_image_session_activity(self, session_id: str) -> bool:
        lock, active_sessions = self._image_session_activity_state()
        clean_session_id = str(session_id or "")
        with lock:
            if clean_session_id in active_sessions:
                return False
            active_sessions.add(clean_session_id)
            return True

    def _release_image_session_activity(self, session_id: str) -> None:
        lock, active_sessions = self._image_session_activity_state()
        with lock:
            active_sessions.discard(str(session_id or ""))

    def _image_session_is_active(self, session_id: str) -> bool:
        lock, active_sessions = self._image_session_activity_state()
        with lock:
            return str(session_id or "") in active_sessions

    def list_image_sets(self) -> dict[str, Any]:
        try:
            sets = self._image_session_store().list_sets()
            active_sets = getattr(self, "active_image_sets", set())
            for image_set in sets:
                if image_set.get("status") == "running" and image_set.get("setId") not in active_sets:
                    image_set["status"] = "interrupted"
                    for item in image_set.get("items") or []:
                        if item.get("status") not in {"completed", "failed"}:
                            item["status"] = "failed"
                            item["error"] = "生成已中断"
            return {"ok": True, "sets": sets}
        except OSError as exc:
            return {"ok": False, "error": f"无法读取图片集历史：{exc}", "sets": []}

    def append_image_stream_debug(self, records: Any) -> dict[str, Any]:
        if not isinstance(records, list):
            return {"ok": False, "error": "流式图片日志格式无效"}

        sensitive_keys = {"src", "dataurl", "base64"}

        def sanitized(value: Any, depth: int = 0) -> Any:
            if depth > 8:
                return "[max-depth]"
            if isinstance(value, dict):
                clean: dict[str, Any] = {}
                for raw_key, raw_value in list(value.items())[:100]:
                    key = str(raw_key)
                    normalized_key = key.casefold().replace("_", "").replace("-", "")
                    if normalized_key.endswith("uri") or normalized_key in sensitive_keys:
                        continue
                    clean[key] = sanitized(raw_value, depth + 1)
                return clean
            if isinstance(value, (list, tuple)):
                return [sanitized(item, depth + 1) for item in value[:100]]
            if isinstance(value, str):
                if value.casefold().startswith("data:image/"):
                    return "[redacted-image-data]"
                return value[:8192]
            if value is None or isinstance(value, (bool, int)):
                return value
            if isinstance(value, float):
                return value if math.isfinite(value) else None
            return str(value)[:8192]

        lines: list[str] = []
        for record in records[:500]:
            if not isinstance(record, dict):
                continue
            encoded = json.dumps(
                sanitized(record), ensure_ascii=False, separators=(",", ":")
            )
            if len(encoded.encode("utf-8")) <= 128 * 1024:
                lines.append(encoded + "\n")

        log_path = app_data_dir() / "image-stream-blur.jsonl"
        if not lines:
            return {"ok": True, "written": 0, "path": str(log_path)}

        payload = "".join(lines)
        lock = getattr(self, "image_stream_debug_lock", None)
        if lock is None:
            lock = threading.Lock()
            self.image_stream_debug_lock = lock
        try:
            with lock:
                if (
                    log_path.is_file()
                    and log_path.stat().st_size + len(payload.encode("utf-8"))
                    > IMAGE_STREAM_DEBUG_LOG_MAX_BYTES
                ):
                    previous_path = log_path.with_name("image-stream-blur.previous.jsonl")
                    previous_path.unlink(missing_ok=True)
                    log_path.replace(previous_path)
                with log_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(payload)
        except OSError as exc:
            return {"ok": False, "error": f"写入流式图片日志失败：{exc}"}
        return {"ok": True, "written": len(lines), "path": str(log_path)}

    def delete_image_set(self, session_id: str, set_id: str) -> dict[str, Any]:
        active_sets = getattr(self, "active_image_sets", set())
        if str(set_id) in active_sets or self._image_session_is_active(session_id):
            return {"ok": False, "error": "图片会话仍在生成中，暂时不能删除"}
        try:
            deleted = self._image_session_store().delete_set(session_id, set_id)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"删除图片集失败：{exc}"}
        if not deleted:
            return {"ok": False, "error": "图片集不存在或已被删除"}
        return {"ok": True, "setId": str(set_id), "sessionId": str(session_id)}

    def save_edited_image(self, source_path: str) -> dict[str, Any]:
        if not self.window:
            return {"ok": False, "error": "应用窗口尚未就绪"}
        source = Path(str(source_path or "")).resolve()
        output_root = (app_data_dir() / "image-generations").resolve()
        pictures_root = generated_pictures_dir().resolve()
        if not source.is_file() or not any(
            source.is_relative_to(root) for root in (output_root, pictures_root)
        ):
            return {"ok": False, "error": "只能保存本应用生成的图片"}
        selected = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(windows_pictures_dir()),
            save_filename=source.name,
            file_types=("PNG 图片 (*.png)", "JPEG 图片 (*.jpg;*.jpeg)", "WebP 图片 (*.webp)"),
        )
        if not selected:
            return {"ok": True, "cancelled": True}
        destination = Path(selected[0]).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return {"ok": True, "path": str(destination)}

    def open_generated_pictures(self) -> dict[str, Any]:
        output_dir = generated_pictures_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(output_dir))
        except OSError as exc:
            return {"ok": False, "error": f"无法打开图片文件夹：{exc}"}
        return {"ok": True, "path": str(output_dir)}
