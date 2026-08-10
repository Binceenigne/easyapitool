from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class WindowStateMixin:
    def bind_window(self, window: webview.Window) -> None:
        self.window = window
        window.events.loaded += self._on_page_loaded
        window.events.minimized += self._on_minimized
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored
        window.events.resized += self._on_resized
        window.events.closing += self._on_closing

    def set_window_size(self, width: Any, height: Any) -> dict[str, Any]:
        clean = normalize_window_size(width, height)
        size = (clean["width"], clean["height"])
        lock = getattr(self, "_window_size_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._window_size_lock = lock
        with lock:
            if size == getattr(
                self,
                "_last_saved_window_size",
                (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT),
            ):
                return {"ok": True, "windowSize": clean, "changed": False}
            saved = self.store.set_window_size(clean["width"], clean["height"])
            self._last_saved_window_size = (saved["width"], saved["height"])
        return {"ok": True, "windowSize": saved, "changed": True}

    def _window_is_normal(self) -> bool:
        if getattr(self, "maximized", False):
            return False
        try:
            hwnd = self._window_handle()
        except (AttributeError, OSError, RuntimeError):
            return True
        return not hwnd or (not user32.IsZoomed(hwnd) and not user32.IsIconic(hwnd))

    def _current_window_size(
        self, width: Any = None, height: Any = None
    ) -> tuple[int, int]:
        window = getattr(self, "window", None)
        if width is None:
            try:
                width = window.width if window else None
            except (AttributeError, OSError, RuntimeError):
                width = None
        if height is None:
            try:
                height = window.height if window else None
            except (AttributeError, OSError, RuntimeError):
                height = None
        if width is None or height is None:
            return getattr(
                self,
                "_last_saved_window_size",
                (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT),
            )
        clean = normalize_window_size(width, height)
        return clean["width"], clean["height"]

    def _schedule_window_size_save(
        self, width: Any = None, height: Any = None
    ) -> None:
        if not self._window_is_normal():
            return
        size = self._current_window_size(width, height)
        if size == getattr(
            self,
            "_last_saved_window_size",
            (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT),
        ):
            return
        lock = getattr(self, "_window_size_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._window_size_lock = lock
        with lock:
            self._pending_window_size = size
            timer = getattr(self, "_window_size_timer", None)
            if timer and timer.is_alive():
                return
            timer = threading.Timer(
                WINDOW_SIZE_SAVE_DELAY, self._persist_pending_window_size
            )
            timer.daemon = True
            self._window_size_timer = timer
            timer.start()

    def _persist_pending_window_size(self) -> None:
        lock = getattr(self, "_window_size_lock", None)
        if lock is None:
            return
        with lock:
            size = getattr(self, "_pending_window_size", None)
            self._pending_window_size = None
            self._window_size_timer = None
        if size is None or not self._window_is_normal():
            return
        try:
            self.set_window_size(*size)
        except Exception:
            pass

    def _flush_window_size(self) -> None:
        lock = getattr(self, "_window_size_lock", None)
        if lock is None:
            return
        with lock:
            size = getattr(self, "_pending_window_size", None)
            self._pending_window_size = None
            timer = getattr(self, "_window_size_timer", None)
            self._window_size_timer = None
        if timer:
            timer.cancel()
        if size is None:
            size = self._current_window_size()
        if not self._window_is_normal():
            return
        try:
            self.set_window_size(*size)
        except Exception:
            pass
