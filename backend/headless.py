from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .client import EasyClinClient
from .controller_mixins.image_files import ImageFilesMixin
from .controller_mixins.image_reasoning import ImageReasoningMixin
from .controller_mixins.prompt import PromptMixin
from .image_editor import ImageGenerationService
from .web_search import WebSearchService


class MemoryCredentialStore:
    """Ephemeral key records. Android re-registers its secure keys after restart."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._secrets: dict[str, str] = {}
        self._lock = threading.RLock()

    def add_key(
        self,
        name: str,
        secret: str,
        base_url: str,
        key_id: str | None = None,
    ) -> str:
        clean_id = str(key_id or uuid.uuid4().hex).strip()
        if not clean_id:
            raise ValueError("密钥编号不能为空")
        now = time.time()
        with self._lock:
            self._records[clean_id] = {
                "id": clean_id,
                "name": str(name),
                "base_url": str(base_url).rstrip("/"),
                "created_at": now,
                "updated_at": now,
                "last_error": None,
            }
            self._secrets[clean_id] = str(secret)
        return clean_id

    def list_key_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self._records.values()]

    def get_key_record(self, key_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(str(key_id or ""))
            return dict(record) if record else None

    def get_secret(self, key_id: str) -> str:
        with self._lock:
            secret = self._secrets.get(str(key_id or ""))
        if secret is None:
            raise KeyError("密钥不存在")
        return secret

    def delete_key(self, key_id: str) -> None:
        with self._lock:
            self._records.pop(str(key_id or ""), None)
            self._secrets.pop(str(key_id or ""), None)


class HeadlessController(ImageFilesMixin, PromptMixin, ImageReasoningMixin):
    """Compose the image domain without desktop services or server persistence."""

    def __init__(
        self,
        *,
        credential_store: MemoryCredentialStore,
        data_root: Path,
        pictures_root: Path,
        client: EasyClinClient | None = None,
        image_generator: ImageGenerationService | None = None,
        web_search: WebSearchService | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = credential_store
        self.data_root = data_root.resolve()
        self.pictures_root = pictures_root.resolve()
        self.client = client or EasyClinClient()
        self.image_generator = image_generator or ImageGenerationService()
        self.web_search = web_search or WebSearchService()
        self.event_callback = event_callback
        self.window = None
        self.visible = True
        self.image_stream_debug_lock = threading.Lock()
        self.image_session_activity_lock = threading.Lock()
        self.active_image_sessions: set[str] = set()
        self.active_image_sets: set[str] = set()
        self.image_task_lock = threading.Lock()
        self.image_tasks: dict[str, Any] = {}

    def notify(self, title: str, message: str, severity: int = 0) -> None:
        self.emit_event(
            {
                "type": "notification",
                "title": str(title),
                "message": str(message),
                "severity": int(severity),
            }
        )

    def emit_event(self, event: dict[str, Any]) -> None:
        if self.event_callback is not None:
            try:
                self.event_callback(dict(event))
            except Exception:
                pass

    def push_state_to_ui(self) -> None:
        return

    def add_key(
        self,
        name: str,
        value: str,
        base_url: str,
        key_id: str | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        clean_value = str(value or "").strip()
        clean_base_url = str(base_url or "").strip().rstrip("/")
        if not clean_name or not clean_value:
            return {"ok": False, "error": "昵称和密钥不能为空"}
        if not clean_base_url.startswith(("http://", "https://")):
            return {"ok": False, "error": "服务地址必须使用 HTTP 或 HTTPS"}
        clean_key_id = self.store.add_key(
            clean_name,
            clean_value,
            clean_base_url,
            key_id,
        )
        return {"ok": True, "keyId": clean_key_id}

    def delete_key(self, key_id: str) -> dict[str, Any]:
        self.store.delete_key(str(key_id or ""))
        return {"ok": True, "keyId": str(key_id or "")}
