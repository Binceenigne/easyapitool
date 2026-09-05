from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class WorkersWindowMixin:
    def start_workers(self) -> None:
        trace_startup("webview_start_callback")
        if self.restart_ready_path:
            try:
                Path(self.restart_ready_path).write_text(str(os.getpid()), encoding="ascii")
            except OSError:
                pass
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is not None:
            native_form.VisibleChanged += self._on_native_visibility_changed
        self._set_window_corner(False)
        threading.Thread(target=self._refresh_loop, name="usage-refresh", daemon=True).start()
        threading.Thread(target=self._start_tray, name="tray-initializer", daemon=True).start()
        threading.Thread(
            target=self._initial_refresh,
            name="initial-refresh",
            daemon=True,
        ).start()
        self.check_for_updates(manual=False)
        trace_startup("startup_workers_scheduled")

    def _should_check_for_updates(self) -> bool:
        return True

    def _initial_refresh(self) -> None:
        trace_startup("initial_refresh_started")
        self.refresh_all(push_ui=False)
        if self.frontend_ready.wait(timeout=5):
            self.push_state_to_ui()
        trace_startup("initial_refresh_finished")

    def report_startup(self, stage: str, navigation_ms: Any = 0) -> dict[str, Any]:
        allowed_stages = {"frontend_interactive", "frontend_load"}
        if stage in allowed_stages:
            trace_startup(stage, navigationMs=safe_float(navigation_ms))
            if stage == "frontend_interactive":
                self.frontend_ready.set()
        return {"ok": True}

    def _on_page_loaded(self) -> None:
        trace_startup("webview_page_loaded")
        self._disable_native_zoom_control()
        if self.asset_cache.is_ready():
            self.frontend_ready.set()

    def _disable_native_zoom_control(self) -> bool:
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is None:
            return False

        def disable_zoom() -> None:
            native_webview = getattr(native_form, "webview", None)
            core_webview = getattr(native_webview, "CoreWebView2", None) if native_webview else None
            if core_webview is not None:
                core_webview.Settings.IsZoomControlEnabled = False

        try:
            if native_form.InvokeRequired:
                from System import Action

                native_form.BeginInvoke(Action(disable_zoom))
            else:
                disable_zoom()
            return True
        except Exception:
            return False

    def set_ui_visible(self, visible: Any) -> dict[str, Any]:
        self.visible = bool(visible)
        self.ui_visibility_token += 1
        self.refresh_wakeup.set()
        return {
            "ok": True,
            "visible": self.visible,
            "visibilityToken": self.ui_visibility_token,
        }

    def set_always_on_top(self, enabled: Any) -> dict[str, Any]:
        self.always_on_top = self.store.set_always_on_top(enabled)
        return {"ok": True, "alwaysOnTop": self.always_on_top}

    def notify_ui_hidden(self) -> dict[str, Any]:
        state = self.set_ui_visible(False)
        state["backgroundUiMode"] = self.store.get_background_ui_mode()
        return state

    def claim_ui_release(self, visibility_token: Any) -> dict[str, Any]:
        try:
            token = int(visibility_token)
        except (TypeError, ValueError):
            token = -1
        release = (
            not self.visible
            and token == self.ui_visibility_token
            and self.store.get_background_ui_mode() == "delayed"
        )
        return {"ok": True, "release": release}

    def initialize_assets(self, retry: Any = False) -> dict[str, Any]:
        return self.asset_cache.start_install(retry=bool(retry))

    def get_asset_status(self) -> dict[str, Any]:
        return self.asset_cache.status()

    def complete_initialization(self) -> dict[str, Any]:
        if not self.asset_cache.is_ready():
            return {"ok": False, "error": "静态资源缓存尚未就绪"}
        if not self.window:
            return {"ok": False, "error": "应用窗口尚未就绪"}
        url = self.asset_cache.main_page.as_uri()
        self.window.load_url(url)
        return {"ok": True, "url": url}

    def _window_handle(self) -> int:
        if self.window:
            native_form = getattr(self.window, "native", None)
            if native_form is not None:
                return native_form.Handle.ToInt64()
        return int(user32.FindWindowW(None, WINDOW_TITLE) or 0)

    def _set_window_corner(self, maximized: bool) -> None:
        hwnd = self._window_handle()
        if not hwnd:
            return
        preference = ctypes.c_int(DWMWCP_DONOTROUND if maximized else DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )

    def _push_window_state(self) -> None:
        if not self.window or not self.visible:
            return
        try:
            self.window.evaluate_js(f"window.applyWindowState({str(self.maximized).lower()})")
        except Exception:
            pass

    def native_drag(self, direction: str) -> dict[str, Any]:
        hit_tests = {
            "move": HTCAPTION,
            "left": HTLEFT,
            "right": HTRIGHT,
            "top": HTTOP,
            "top-left": HTTOPLEFT,
            "top-right": HTTOPRIGHT,
            "bottom": HTBOTTOM,
            "bottom-left": HTBOTTOMLEFT,
            "bottom-right": HTBOTTOMRIGHT,
        }
        hit_test = hit_tests.get(direction)
        hwnd = self._window_handle()
        if not hwnd or hit_test is None:
            return {"ok": False}
        restore_before_move = direction == "move" and bool(user32.IsZoomed(hwnd))
        if restore_before_move:
            self.maximized = False

        def perform_drag() -> None:
            if restore_before_move:
                cursor = POINT()
                maximized_rect = wintypes.RECT()
                user32.GetCursorPos(ctypes.byref(cursor))
                user32.GetWindowRect(hwnd, ctypes.byref(maximized_rect))
                width = max(1, maximized_rect.right - maximized_rect.left)
                horizontal_ratio = min(1.0, max(0.0, (cursor.x - maximized_rect.left) / width))
                self.drag_restore_suppressed_until = time.monotonic() + 1.0
                user32.ShowWindow(hwnd, SW_RESTORE)
                restored_rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(restored_rect))
                restored_width = restored_rect.right - restored_rect.left
                user32.SetWindowPos(
                    hwnd,
                    None,
                    round(cursor.x - restored_width * horizontal_ratio),
                    cursor.y - 16,
                    0,
                    0,
                    SWP_NOSIZE | SWP_NOZORDER,
                )
                self.maximized = False
                self._set_window_corner(False)

            if not user32.IsZoomed(hwnd):
                user32.ReleaseCapture()
                user32.PostMessageW(hwnd, WM_NCLBUTTONDOWN, hit_test, 0)

        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is None:
            return {"ok": False}
        if native_form.InvokeRequired:
            from System import Action

            native_form.BeginInvoke(Action(perform_drag))
        else:
            perform_drag()
        return {"ok": True, "maximized": self.maximized}

    def open_devtools(self) -> dict[str, Any]:
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is None:
            return {"ok": False, "error": "开发者工具尚未就绪"}

        def open_window() -> None:
            native_webview = getattr(native_form, "webview", None)
            core_webview = getattr(native_webview, "CoreWebView2", None) if native_webview else None
            if core_webview is None:
                trace_startup("devtools_open_failed", error="CoreWebView2 is not ready")
                return
            core_webview.Settings.AreDevToolsEnabled = True
            core_webview.OpenDevToolsWindow()

        try:
            if native_form.InvokeRequired:
                from System import Action

                native_form.BeginInvoke(Action(open_window))
            else:
                open_window()
            return {"ok": True}
        except Exception as exc:
            trace_startup("devtools_open_failed", error=str(exc))
            return {"ok": False, "error": "无法打开开发者工具"}

    def _start_tray(self) -> None:
        trace_startup("tray_init_started")
        try:
            import pystray

            image = Image.open(self.icon_png).convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem("显示 DJYX_APITOOL", lambda _icon, _item: self.show_window(), default=True),
                pystray.MenuItem("立即刷新", lambda _icon, _item: self.request_refresh()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", lambda _icon, _item: self.exit_app()),
            )
            self.tray = pystray.Icon(APP_NAME, image, "DJYX_APITOOL", menu)
            trace_startup("tray_init_finished")
            self.tray.run()
        except Exception as exc:
            trace_startup("tray_init_failed", error=str(exc))

    def _refresh_loop(self) -> None:
        while not self.stopping.is_set():
            interval = self.foreground_interval if self.visible else self.background_interval
            self.next_refresh_at = time.time() + interval
            triggered = self.refresh_wakeup.wait(interval)
            self.refresh_wakeup.clear()
            if self.stopping.is_set():
                break
            if triggered:
                continue
            self.refresh_all(push_ui=self.visible)

    def request_refresh(self) -> None:
        threading.Thread(
            target=lambda: self.refresh_all(push_ui=self.visible),
            name="manual-refresh",
            daemon=True,
        ).start()

    def _on_minimized(self) -> None:
        self.visible = False
        self.refresh_wakeup.set()

    def _on_native_visibility_changed(self, sender: Any, _args: Any) -> None:
        self.visible = bool(sender.Visible)
        self.refresh_wakeup.set()
        if self.visible:
            threading.Timer(0.25, self.push_state_to_ui).start()

    def _on_maximized(self) -> None:
        self.visible = True
        self.maximized = True
        self._set_window_corner(True)
        self._push_window_state()
        self.refresh_wakeup.set()

    def _on_restored(self) -> None:
        was_maximized = self.maximized
        self.visible = True
        self.maximized = False
        self.refresh_wakeup.set()
        if time.monotonic() < getattr(self, "drag_restore_suppressed_until", 0.0):
            return
        if was_maximized:
            self._set_window_corner(False)
            self._push_window_state()
        self._schedule_window_size_save()

    def _on_resized(self, width: Any = None, height: Any = None) -> None:
        self._schedule_window_size_save(width, height)

    def _on_closing(self) -> bool | None:
        self._flush_window_size()
        if self.stopping.is_set():
            return None
        self._handle_close_request()
        return False

    def _handle_close_request(self, selection: str | None = None) -> str:
        self._flush_window_size()
        action = selection or self.store.get_close_action()
        if action == "exit":
            threading.Timer(0.05, self.exit_app).start()
        elif action == "tray":
            threading.Timer(0.01, self.hide_window).start()
        elif self.window:
            try:
                self.window.evaluate_js("window.openCloseActionModal();")
            except Exception:
                self.hide_window()
        return action
