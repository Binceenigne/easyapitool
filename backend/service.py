from __future__ import annotations

import io
import hmac
import os
import secrets
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .client import EasyClinClient
from .common import APP_NAME, DEFAULT_BASE_URL
from .headless import HeadlessController, MemoryCredentialStore
from .image_editor import ImageGenerationService, image_preview_bytes
from .web_search import WebSearchService


MAX_REFERENCE_COUNT = 16
MAX_REFERENCE_BYTES = 50 * 1024 * 1024
RESOURCE_TTL_SECONDS = 30 * 60
PAIRING_CODE_FILENAME = "pairing-code"
SUPPORTED_UPLOAD_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class ServiceConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceConfig:
    work_root: Path
    bearer_token: str
    base_url: str = DEFAULT_BASE_URL
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_environment(cls) -> "ServiceConfig":
        work_root = Path(
            os.environ.get("API_TOOLS_WORK_DIR", "/run/easyapitool")
        ).expanduser()
        bearer_token = os.environ.get("API_TOOLS_SERVICE_TOKEN", "").strip()
        if not bearer_token:
            raise ServiceConfigurationError("必须配置 API_TOOLS_SERVICE_TOKEN")
        try:
            port = int(os.environ.get("API_TOOLS_SERVICE_PORT", "8765"))
        except ValueError as exc:
            raise ServiceConfigurationError("API_TOOLS_SERVICE_PORT 无效") from exc
        if not 1024 <= port <= 65535:
            raise ServiceConfigurationError("API_TOOLS_SERVICE_PORT 超出范围")
        return cls(
            work_root=work_root.resolve(),
            bearer_token=bearer_token,
            base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            host=os.environ.get("API_TOOLS_SERVICE_HOST", "127.0.0.1"),
            port=port,
        )


@dataclass
class ResourceRecord:
    resource_id: str
    owner: str
    path: Path
    content_type: str
    created_at: float
    expires_at: float


class EventBus:
    def __init__(self, max_events: int = 2000) -> None:
        self._condition = threading.Condition()
        self._events: list[dict[str, Any]] = []
        self._next_id = 1
        self._max_events = max_events

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._condition:
            payload = {"eventId": self._next_id, **event}
            self._next_id += 1
            self._events.append(payload)
            if len(self._events) > self._max_events:
                del self._events[: len(self._events) - self._max_events]
            self._condition.notify_all()
            return dict(payload)

    def wait_since(self, cursor: int, timeout: float = 15.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                events = [
                    event for event in self._events if int(event["eventId"]) > cursor
                ]
                if events:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)


class HeadlessService:
    """Stateless service: Android owns all durable files and SQLite indexes."""

    def __init__(
        self,
        config: ServiceConfig,
        *,
        credential_store: MemoryCredentialStore | None = None,
        client: EasyClinClient | None = None,
        image_generator: ImageGenerationService | None = None,
        web_search: WebSearchService | None = None,
        controller: HeadlessController | None = None,
    ) -> None:
        self.config = config
        self.instance_root = (
            config.work_root.resolve() / f"instance-{uuid.uuid4().hex}"
        )
        self.instance_root.mkdir(parents=True, exist_ok=True)
        self.upload_root = self.instance_root / "uploads"
        self.session_root = self.instance_root / "sessions"
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.credentials = credential_store or MemoryCredentialStore()
        self.client = client or EasyClinClient()
        self.image_generator = image_generator or ImageGenerationService()
        self.web_search = web_search or WebSearchService()
        self.events = EventBus()
        self.resources: dict[str, ResourceRecord] = {}
        self.resource_by_path: dict[tuple[str, str], str] = {}
        self.resource_lock = threading.RLock()
        self.tasks: dict[str, threading.Thread] = {}
        self.task_cancel: dict[str, threading.Event] = {}
        self.task_states: dict[str, dict[str, Any]] = {}
        self.task_owners: dict[str, str] = {}
        self.task_lock = threading.RLock()
        self.pair_lock = threading.RLock()
        self.controller = controller or HeadlessController(
            credential_store=self.credentials,
            data_root=self.instance_root,
            pictures_root=self.session_root,
            client=self.client,
            image_generator=self.image_generator,
            web_search=self.web_search,
            event_callback=self.publish_task_event,
        )

    def health(self) -> dict[str, Any]:
        return {"ok": True, "service": APP_NAME, "status": "alive"}

    def readiness(self) -> dict[str, Any]:
        self.cleanup_expired()
        checks = {
            "workRoot": self.config.work_root.is_dir(),
            "instanceRoot": self.instance_root.is_dir(),
            "credentialsInMemory": True,
        }
        return {
            "ok": all(checks.values()),
            "status": "ready" if all(checks.values()) else "not_ready",
            "checks": checks,
            "persistentServerDatabase": False,
        }

    def pair_device(self, pairing_code: str) -> dict[str, Any]:
        """Consume a root-generated pairing code and return the device token once."""
        candidate = str(pairing_code or "").strip()
        if not 16 <= len(candidate) <= 128:
            raise PermissionError("配对码无效")
        pairing_path = self.config.work_root / PAIRING_CODE_FILENAME
        with self.pair_lock:
            try:
                metadata = pairing_path.lstat()
                if not pairing_path.is_file() or pairing_path.is_symlink():
                    raise PermissionError("配对码无效")
                if os.name == "posix" and metadata.st_mode & 0o077:
                    raise PermissionError("配对码无效")
                expected = pairing_path.read_text(encoding="ascii").strip()
            except (OSError, UnicodeDecodeError):
                raise PermissionError("配对码无效") from None
            if not expected or not hmac.compare_digest(candidate, expected):
                raise PermissionError("配对码无效")
            try:
                pairing_path.unlink()
            except OSError:
                raise PermissionError("配对码无效") from None
        return {"ok": True, "bearerToken": self.config.bearer_token}

    def state(self) -> dict[str, Any]:
        keys: list[dict[str, Any]] = []
        for record in self.credentials.list_key_records():
            secret = self.credentials.get_secret(record["id"])
            masked = "*" * len(secret) if len(secret) <= 8 else f"{secret[:4]}...{secret[-4:]}"
            keys.append(
                {
                    "id": str(record["id"]),
                    "name": str(record["name"]),
                    "value": masked,
                    "baseUrl": str(record["base_url"]),
                    "lastError": None,
                }
            )
        with self.task_lock:
            active_tasks = list(self.tasks)
        return {
            "appName": APP_NAME,
            "keys": keys,
            "activeTasks": active_tasks,
            "storageMode": "android-filesystem-sqlite-index",
            "serverPersistence": False,
        }

    def add_key(
        self,
        owner: str,
        name: str,
        value: str,
        base_url: str | None = None,
        key_id: str | None = None,
    ) -> dict[str, Any]:
        result = self.controller.add_key(
            name,
            value,
            base_url or self.config.base_url,
            key_id,
        )
        if not result.get("ok"):
            return result
        return {**result, "state": self.state()}

    def delete_key(self, owner: str, key_id: str) -> dict[str, Any]:
        result = self.controller.delete_key(key_id)
        return {**result, "state": self.state()}

    @staticmethod
    def _safe_filename(name: str, suffix: str) -> str:
        stem = Path(name or "reference").stem[:48]
        clean = "".join(char for char in stem if char.isalnum() or char in "-_ ").strip()
        return (clean or "reference") + suffix

    @staticmethod
    def _resource_url(resource_id: str) -> str:
        return f"/api/v1/assets/{resource_id}"

    def _register_file_resource(
        self,
        owner: str,
        path: Path,
        content_type: str | None = None,
        prefix: str = "asset",
    ) -> ResourceRecord | None:
        try:
            resolved = path.resolve()
            if not resolved.is_file() or not resolved.is_relative_to(self.instance_root):
                return None
        except OSError:
            return None
        key = (owner, str(resolved))
        now = time.time()
        with self.resource_lock:
            existing_id = self.resource_by_path.get(key)
            existing = self.resources.get(existing_id or "")
            if existing is not None and existing.expires_at > now:
                return existing
            inferred_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }.get(resolved.suffix.lower(), "application/octet-stream")
            resource_id = f"{prefix}-{secrets.token_urlsafe(18)}"
            record = ResourceRecord(
                resource_id,
                owner,
                resolved,
                content_type or inferred_type,
                now,
                now + RESOURCE_TTL_SECONDS,
            )
            self.resources[resource_id] = record
            self.resource_by_path[key] = resource_id
            return record

    def upload_reference(
        self,
        owner: str,
        filename: str,
        content_type: str,
        body: bytes,
    ) -> dict[str, Any]:
        self.cleanup_expired()
        if content_type not in SUPPORTED_UPLOAD_TYPES:
            raise ValueError("仅支持 PNG、JPEG 或 WebP 图片")
        if len(body) > MAX_REFERENCE_BYTES:
            raise ValueError("图片超过 50 MB")
        try:
            with Image.open(io.BytesIO(body)) as image:
                image.verify()
        except (OSError, ValueError) as exc:
            raise ValueError("图片内容无效") from exc
        suffix = SUPPORTED_UPLOAD_TYPES[content_type]
        file_id = f"ref-{secrets.token_urlsafe(18)}"
        directory = self.upload_root / owner
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{file_id}{suffix}"
        preview_path = directory / f"{file_id}-preview.jpg"
        path.write_bytes(body)
        preview_path.write_bytes(image_preview_bytes(body))
        original = self._register_file_resource(owner, path, content_type, "ref")
        preview = self._register_file_resource(owner, preview_path, "image/jpeg", "preview")
        if original is None or preview is None:
            raise RuntimeError("无法登记临时图片资源")
        return {
            "resourceId": original.resource_id,
            "previewResourceId": preview.resource_id,
            "name": self._safe_filename(filename, suffix),
            "contentType": content_type,
            "sizeBytes": len(body),
            "downloadUrl": self._resource_url(original.resource_id),
            "previewUrl": self._resource_url(preview.resource_id),
        }

    def resource(self, owner: str, resource_id: str) -> ResourceRecord:
        self.cleanup_expired()
        with self.resource_lock:
            record = self.resources.get(str(resource_id or ""))
        if record is None or record.owner != owner:
            raise KeyError("资源不存在")
        try:
            valid = record.path.is_file() and record.path.resolve().is_relative_to(self.instance_root)
        except OSError:
            valid = False
        if not valid:
            raise KeyError("资源不存在")
        return record

    def resolve_asset(self, owner: str, resource_id: str) -> tuple[Path, str]:
        record = self.resource(owner, resource_id)
        return record.path, record.content_type

    def list_image_sets(self, owner: str) -> dict[str, Any]:
        return {"ok": True, "sets": [], "source": "android-local"}

    def _sanitize_payload(self, value: Any, owner: str) -> Any:
        if isinstance(value, dict):
            clean: dict[str, Any] = {}
            original: ResourceRecord | None = None
            preview: ResourceRecord | None = None
            for key, item in value.items():
                if key in {"path", "savedPath"}:
                    original = self._register_file_resource(owner, Path(str(item)))
                    continue
                if key == "previewPath":
                    preview = self._register_file_resource(
                        owner, Path(str(item)), "image/jpeg", "preview"
                    )
                    continue
                if key in {"uri", "previewUri", "dataUrl"}:
                    continue
                clean[str(key)] = self._sanitize_payload(item, owner)
            if original is not None:
                clean["resourceId"] = original.resource_id
                clean["downloadUrl"] = self._resource_url(original.resource_id)
            if preview is not None:
                clean["previewResourceId"] = preview.resource_id
                clean["previewUrl"] = self._resource_url(preview.resource_id)
            return clean
        if isinstance(value, list):
            return [self._sanitize_payload(item, owner) for item in value]
        return value

    def publish_task_event(self, event: dict[str, Any]) -> dict[str, Any]:
        request_id = str(event.get("requestId") or "")
        with self.task_lock:
            owner = self.task_owners.get(request_id, "paired-device")
        clean = self._sanitize_payload(event, owner)
        if request_id:
            with self.task_lock:
                self.task_states[request_id] = dict(clean)
        return self.events.publish(clean)

    def task_state(self, owner: str, request_id: str) -> dict[str, Any]:
        clean_id = str(request_id or "")
        with self.task_lock:
            if self.task_owners.get(clean_id) != owner:
                raise KeyError("任务不存在")
            state = self.task_states.get(clean_id)
        if state is None:
            raise KeyError("任务不存在")
        return dict(state)

    def create_generation(
        self,
        owner: str,
        key_id: str,
        prompt: str,
        reference_ids: list[str],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_key_id = str(key_id or "")
        if self.credentials.get_key_record(clean_key_id) is None:
            raise KeyError("请选择有效的 API Key")
        clean_ids = list(dict.fromkeys(str(item) for item in reference_ids))
        if len(clean_ids) > MAX_REFERENCE_COUNT:
            raise ValueError("参考图片不能超过 16 张")
        records = [self.resource(owner, resource_id) for resource_id in clean_ids]
        requested_id = str((options or {}).get("requestId") or "").strip()
        request_id = requested_id[:80] if requested_id else uuid.uuid4().hex
        if not request_id:
            request_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        clean_options = dict(options or {})
        # Android owns continuation history; the server runs each request independently.
        clean_options["continuation"] = False
        clean_options.pop("parentSetId", None)
        with self.task_lock:
            self.task_cancel[request_id] = cancel_event
            self.task_owners[request_id] = owner
        self.publish_task_event({"type": "task_created", "requestId": request_id})
        worker = threading.Thread(
            target=self._run_generation,
            args=(owner, request_id, clean_key_id, prompt, records, clean_options, cancel_event),
            name=f"service-image-{request_id[:8]}",
            daemon=True,
        )
        with self.task_lock:
            self.tasks[request_id] = worker
        worker.start()
        return {"ok": True, "requestId": request_id, "status": "queued"}

    def _run_generation(
        self,
        owner: str,
        request_id: str,
        key_id: str,
        prompt: str,
        records: list[ResourceRecord],
        options: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        try:
            self.publish_task_event({"type": "task_started", "requestId": request_id})
            if cancel_event.is_set():
                raise RuntimeError("生成已取消")
            result = self.controller.generate_image(
                key_id,
                prompt,
                [str(record.path) for record in records],
                {**options, "requestId": request_id, "sessionId": request_id},
                event_callback=self.publish_task_event,
            )
            self.publish_task_event(
                {"type": "task_result", "requestId": request_id, "result": result}
            )
        except Exception as exc:
            self.publish_task_event(
                {"type": "task_failed", "requestId": request_id, "error": str(exc)[:500]}
            )
        finally:
            with self.task_lock:
                self.tasks.pop(request_id, None)
                self.task_cancel.pop(request_id, None)

    def cancel_task(self, owner: str, request_id: str) -> dict[str, Any]:
        clean_id = str(request_id or "")
        with self.task_lock:
            if self.task_owners.get(clean_id) != owner:
                return {"ok": False, "error": "任务不存在或已经结束"}
            cancel_event = self.task_cancel.get(clean_id)
        if cancel_event is None:
            return {"ok": False, "error": "任务不存在或已经结束"}
        cancel_event.set()
        self.controller.cancel_image_generation(clean_id)
        self.publish_task_event({"type": "task_cancel_requested", "requestId": clean_id})
        return {"ok": True, "requestId": clean_id}

    def cleanup_expired(self) -> None:
        now = time.time()
        expired: list[ResourceRecord] = []
        with self.resource_lock:
            for resource_id, record in list(self.resources.items()):
                if record.expires_at <= now:
                    expired.append(record)
                    self.resources.pop(resource_id, None)
                    self.resource_by_path.pop((record.owner, str(record.path)), None)
        for record in expired:
            try:
                record.path.unlink(missing_ok=True)
            except OSError:
                pass

    def close(self) -> None:
        with self.task_lock:
            for event in self.task_cancel.values():
                event.set()
        shutil.rmtree(self.instance_root, ignore_errors=True)


def service_from_environment() -> HeadlessService:
    return HeadlessService(ServiceConfig.from_environment())
