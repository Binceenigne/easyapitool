from __future__ import annotations

from .common import *
from . import platform as platform_module
from .platform import *
from .rpc import ControllerRpcClient, ControllerRpcServer
from .controller import AppController
from .web_api import WebApi

class BackgroundApp:
    def __init__(self, asset_cache: StaticAssetCache | None = None) -> None:
        self.rpc_address = rf"\\.\pipe\API_TOOLS_{os.getpid()}_{uuid.uuid4().hex}"
        self.rpc_authkey = os.urandom(32)
        self.ui_process: multiprocessing.Process | None = None
        self.ui_lock = threading.Lock()
        self.controller = AppController(
            asset_cache,
            ui_show_callback=self.show_ui,
            ui_hide_callback=self.hide_ui,
        )
        self.rpc_server = ControllerRpcServer(
            self.controller, self.rpc_address, self.rpc_authkey
        )
        self.show_event_handle = kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
        if not self.show_event_handle:
            raise ctypes.WinError()

    def start(self) -> None:
        self.rpc_server.start()
        self.controller.start_workers()
        threading.Thread(target=self._show_event_loop, name="show-window-event", daemon=True).start()
        self.show_ui()

    def _show_event_loop(self) -> None:
        while not self.controller.stopping.is_set():
            result = kernel32.WaitForSingleObject(self.show_event_handle, 500)
            if result == WAIT_OBJECT_0:
                self.show_ui()
            elif result != WAIT_TIMEOUT:
                break

    def _watch_ui_process(self, process: multiprocessing.Process) -> None:
        process.join()
        with self.ui_lock:
            if self.ui_process is process:
                self.ui_process = None
                self.hide_ui()
        trace_startup("ui_process_exited", exitCode=process.exitcode)

    def show_ui(self) -> None:
        with self.ui_lock:
            process = self.ui_process
            if process and process.is_alive():
                activate_ui_window()
                self.controller.set_ui_visible(True)
                return
            process = multiprocessing.Process(
                target=run_ui_process,
                args=(self.rpc_address, self.rpc_authkey),
                name="API_TOOLS_UI",
            )
            process.start()
            self.ui_process = process
            self.controller.set_ui_visible(True)
            threading.Thread(
                target=self._watch_ui_process,
                args=(process,),
                name="ui-process-monitor",
                daemon=True,
            ).start()
            trace_startup("ui_process_started", pid=process.pid)

    def hide_ui(self) -> None:
        self.controller.set_ui_visible(False)

    def stop(self) -> None:
        self.controller.stopping.set()
        self.controller.refresh_wakeup.set()
        self.rpc_server.stop()
        if self.controller.tray:
            self.controller.tray.stop()
        with self.ui_lock:
            process = self.ui_process
            if process and process.is_alive():
                process.terminate()
                process.join(timeout=3)
        if self.show_event_handle:
            kernel32.CloseHandle(self.show_event_handle)
            self.show_event_handle = None


class UiController(AppController):
    def __init__(self, rpc_client: ControllerRpcClient, asset_cache: StaticAssetCache) -> None:
        super().__init__(asset_cache)
        self.rpc_client = rpc_client
        self.release_timer: threading.Timer | None = None
        self.benchmark_window: webview.Window | None = None

    def _destroy_ui(self) -> None:
        self.stopping.set()
        if self.window:
            threading.Timer(0.01, self.window.destroy).start()

    def _release_if_still_hidden(self, visibility_token: int) -> None:
        try:
            result = self.rpc_client.call("claim_ui_release", visibility_token)
        except Exception:
            result = {"release": True}
        if result.get("release"):
            self._destroy_ui()

    def hide_window(self) -> None:
        AppController.hide_window(self)
        try:
            state = self.rpc_client.call("notify_ui_hidden")
        except Exception:
            self._destroy_ui()
            return
        mode = normalize_background_ui_mode(state.get("backgroundUiMode"))
        if mode == "active":
            return
        if mode == "low_power":
            self._destroy_ui()
            return
        visibility_token = int(state.get("visibilityToken") or -1)
        self.release_timer = threading.Timer(
            BACKGROUND_UI_RELEASE_DELAY,
            self._release_if_still_hidden,
            args=(visibility_token,),
        )
        self.release_timer.daemon = True
        self.release_timer.start()

    def exit_app(self) -> None:
        if self.release_timer:
            self.release_timer.cancel()
        self._flush_window_size()
        try:
            self.rpc_client.call("exit_app")
        finally:
            self.stopping.set()
            if self.window:
                self.window.destroy()

    def push_image_generation_event(self, event: dict[str, Any]) -> None:
        if not self.window:
            return
        payload = json.dumps(event, ensure_ascii=False)
        try:
            self.window.evaluate_js(f"window.applyImageGenerationEvent({payload});")
        except Exception:
            pass

    def push_benchmark_event(self, event: dict[str, Any]) -> None:
        if not self.benchmark_window:
            return
        payload = json.dumps(event, ensure_ascii=False)
        try:
            self.benchmark_window.evaluate_js(f"window.applyBenchmarkEvent({payload});")
        except Exception:
            pass

    def open_benchmark(self, js_api: Any) -> dict[str, Any]:
        if not self.asset_cache.is_ready():
            return {"ok": False, "error": "Benchmark 静态资源尚未就绪"}
        if self.benchmark_window is not None:
            try:
                self.benchmark_window.show()
                return {"ok": True, "reused": True}
            except Exception:
                self.benchmark_window = None
        benchmark_page = self.asset_cache.release_dir / "frontend/benchmark.html"
        if not benchmark_page.is_file():
            return {"ok": False, "error": "Benchmark 页面不存在"}
        try:
            benchmark_window = webview.create_window(
                "API_TOOLS Benchmark",
                url=benchmark_page.as_uri(),
                js_api=js_api,
                width=1500,
                height=960,
                min_size=(900, 640),
                resizable=True,
                background_color="#06121d",
            )
            if benchmark_window is None:
                return {"ok": False, "error": "无法创建 Benchmark 窗口"}
            self.benchmark_window = benchmark_window

            def clear_benchmark_window(*_args: Any) -> None:
                if self.benchmark_window is benchmark_window:
                    self.benchmark_window = None

            benchmark_window.events.closed += clear_benchmark_window
            return {"ok": True, "reused": False}
        except Exception as exc:
            trace_startup("benchmark_window_failed", error=str(exc))
            return {"ok": False, "error": f"无法打开 Benchmark：{exc}"}

    def restart_app(self) -> dict[str, Any]:
        self._flush_window_size()
        return self.rpc_client.call("restart_app")

    def restart_update(self) -> dict[str, Any]:
        return self.rpc_client.call("restart_update")

    def complete_initialization(self) -> dict[str, Any]:
        if not self.asset_cache.is_ready() or not self.window:
            return {"ok": False, "error": "静态资源缓存尚未就绪"}
        url = self.asset_cache.main_page.as_uri()
        self.window.load_url(url)
        return {"ok": True, "url": url}

    def set_always_on_top(self, enabled: Any) -> dict[str, Any]:
        clean = bool(enabled)
        hwnd = self._window_handle()
        if not hwnd:
            return {"ok": False, "error": "应用窗口尚未就绪"}
        insert_after = HWND_TOPMOST if clean else HWND_NOTOPMOST
        applied = user32.SetWindowPos(
            hwnd,
            insert_after,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        if not applied:
            return {"ok": False, "error": "无法修改窗口置顶状态"}
        result = self.rpc_client.call("set_always_on_top", clean)
        self.always_on_top = bool(result.get("alwaysOnTop"))
        return {"ok": True, "alwaysOnTop": self.always_on_top}

    def set_window_size(self, width: Any, height: Any) -> dict[str, Any]:
        result = self.rpc_client.call("set_window_size", width, height)
        window_size = result.get("windowSize") if isinstance(result, dict) else None
        if isinstance(window_size, dict):
            clean = normalize_window_size(
                window_size.get("width"), window_size.get("height")
            )
            self._last_saved_window_size = (clean["width"], clean["height"])
        return result


class RemoteWebApi(WebApi):
    def __init__(self, controller: UiController, rpc_client: ControllerRpcClient) -> None:
        super().__init__(controller)
        self._rpc_client = rpc_client

    def _remote(self, method: str, *arguments: Any) -> Any:
        return self._rpc_client.call(method, *arguments)

    def get_state(self) -> dict[str, Any]:
        state = self._remote("get_state")
        state["isForeground"] = True
        return state

    def initialize_assets(self, retry: Any = False) -> dict[str, Any]:
        return self._remote("initialize_assets", retry)

    def get_asset_status(self) -> dict[str, Any]:
        return self._remote("get_asset_status")

    def open_generated_pictures(self) -> dict[str, Any]:
        return self._remote("open_generated_pictures")

    def load_generated_image(self, source_path: str) -> dict[str, Any]:
        return self._remote("load_generated_image", source_path)

    def add_key(self, name: str, value: str) -> dict[str, Any]:
        return self._remote("add_key", name, value)

    def delete_key(self, key_id: str) -> dict[str, Any]:
        return self._remote("delete_key", key_id)

    def delete_image_set(self, session_id: str, set_id: str) -> dict[str, Any]:
        return self._remote("delete_image_set", session_id, set_id)

    def list_image_sets(self) -> dict[str, Any]:
        return self._remote("list_image_sets")

    def append_image_stream_debug(self, records: Any) -> dict[str, Any]:
        return self._remote("append_image_stream_debug", records)

    def generate_image(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._rpc_client.call_with_events(
            "generate_image",
            key_id,
            prompt,
            image_paths,
            options,
            on_event=self._controller.push_image_generation_event,
        )

    def open_benchmark(self) -> dict[str, Any]:
        return self._controller.open_benchmark(self)

    def benchmark_run(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._rpc_client.call_with_events(
            "benchmark_run",
            key_id,
            prompt,
            image_paths,
            options,
            on_event=self._controller.push_benchmark_event,
        )

    def cancel_image_generation(self, request_id: str) -> dict[str, Any]:
        return self._remote("cancel_image_generation", request_id)

    def polish_prompt(self, key_id: str, prompt: str) -> dict[str, Any]:
        return self._rpc_client.call_with_events(
            "polish_prompt",
            key_id,
            prompt,
            on_event=self._controller.push_image_generation_event,
        )

    def refresh_now(self, trace_id: Any = None) -> dict[str, Any]:
        return self._remote("refresh_now", trace_id)

    def update_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        return self._remote("update_thresholds", thresholds)

    def update_rate_limit_progress_mode(self, mode: Any) -> dict[str, Any]:
        return self._remote("update_rate_limit_progress_mode", mode)

    def update_refresh_intervals(self, foreground: Any, background: Any) -> dict[str, Any]:
        return self._remote("update_refresh_intervals", foreground, background)

    def update_app_preferences(
        self,
        update_frequency: Any,
        close_action: Any,
        startup_enabled: Any,
        title_bar_mode: Any = None,
        background_ui_mode: Any = None,
    ) -> dict[str, Any]:
        return self._remote(
            "update_app_preferences",
            update_frequency,
            close_action,
            startup_enabled,
            title_bar_mode,
            background_ui_mode,
        )

    def check_for_updates(self) -> dict[str, Any]:
        return self._remote("check_for_updates")

    def download_update(self) -> dict[str, Any]:
        return self._remote("download_update")

    def defer_update_restart(self) -> dict[str, Any]:
        return self._remote("defer_update_restart")

    def dismiss_update_prompt(self) -> dict[str, Any]:
        return self._remote("dismiss_update_prompt")

    def ignore_update_version(self, version: Any) -> dict[str, Any]:
        return self._remote("ignore_update_version", version)

    def report_startup(self, stage: str, navigation_ms: Any = 0) -> dict[str, Any]:
        return self._remote("report_startup", stage, navigation_ms)


def run_ui_process(rpc_address: str, rpc_authkey: bytes) -> None:
    rpc_client = ControllerRpcClient(rpc_address, rpc_authkey)
    asset_cache = StaticAssetCache()
    state = rpc_client.call("get_state")
    title_bar_mode = state.get("titleBarMode") or "default"
    frame_options = window_frame_options(title_bar_mode)
    minimum_size = window_min_size(title_bar_mode)
    saved_window_size = state.get("windowSize")
    window_size = normalize_window_size(
        saved_window_size.get("width") if isinstance(saved_window_size, dict) else None,
        saved_window_size.get("height") if isinstance(saved_window_size, dict) else None,
    )
    window_size["width"] = max(minimum_size[0], window_size["width"])
    window_size["height"] = max(minimum_size[1], window_size["height"])
    initial_page = (
        asset_cache.main_page if asset_cache.is_ready() else resource_path("frontend/initialize.html")
    )
    controller = UiController(rpc_client, asset_cache)
    controller.active_title_bar_mode = normalize_title_bar_mode(title_bar_mode)
    window = webview.create_window(
        WINDOW_TITLE,
        url=str(initial_page),
        js_api=RemoteWebApi(controller, rpc_client),
        width=window_size["width"],
        height=window_size["height"],
        min_size=minimum_size,
        resizable=True,
        frameless=frame_options["frameless"],
        easy_drag=frame_options["easy_drag"],
        shadow=True,
        on_top=bool(state.get("alwaysOnTop")),
        background_color="#0f172a",
    )
    if window is None:
        return
    controller.bind_window(window)
    webview.start(gui="edgechromium", icon=str(resource_path("resources/api_tools_icon.ico")))


def main() -> None:
    platform_module.startup_trace = StartupTrace(app_data_dir() / "startup.log")
    trace_startup(
        "python_ready",
        frozen=bool(getattr(sys, "frozen", False)),
        bundlePath=str(getattr(sys, "_MEIPASS", "source")),
    )
    mutex_handle = acquire_single_instance()
    if mutex_handle is None:
        trace_startup("existing_instance_activated")
        return
    background_app = BackgroundApp(StaticAssetCache())
    try:
        background_app.start()
        background_app.controller.stopping.wait()
    finally:
        background_app.stop()
        kernel32.CloseHandle(mutex_handle)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
