from __future__ import annotations

from .common import *
from .controller import AppController

class WebApi:
    def __init__(self, controller: AppController) -> None:
        self._controller = controller

    def get_state(self) -> dict[str, Any]:
        return self._controller.get_state()

    def initialize_assets(self, retry: Any = False) -> dict[str, Any]:
        return self._controller.initialize_assets(retry)

    def get_asset_status(self) -> dict[str, Any]:
        return self._controller.get_asset_status()

    def complete_initialization(self) -> dict[str, Any]:
        return self._controller.complete_initialization()

    def choose_edit_images(self) -> dict[str, Any]:
        return self._controller.choose_edit_images()

    def import_reference_image(self, data_url: str, name: str = "") -> dict[str, Any]:
        return self._controller.import_reference_image(data_url, name)

    def load_generated_image(self, source_path: str) -> dict[str, Any]:
        return self._controller.load_generated_image(source_path)

    def copy_generated_image(self, source_path: str) -> dict[str, Any]:
        return self._controller.copy_generated_image(source_path)

    def open_generated_pictures(self) -> dict[str, Any]:
        return self._controller.open_generated_pictures()

    def add_key(self, name: str, value: str) -> dict[str, Any]:
        return self._controller.add_key(name, value)

    def delete_key(self, key_id: str) -> dict[str, Any]:
        return self._controller.delete_key(key_id)

    def delete_image_set(self, session_id: str, set_id: str) -> dict[str, Any]:
        return self._controller.delete_image_set(session_id, set_id)

    def list_image_sets(self) -> dict[str, Any]:
        return self._controller.list_image_sets()

    def append_image_stream_debug(self, records: Any) -> dict[str, Any]:
        return self._controller.append_image_stream_debug(records)

    def generate_image(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._controller.generate_image(key_id, prompt, image_paths, options)

    def open_benchmark(self) -> dict[str, Any]:
        return {"ok": False, "error": "Benchmark 仅在桌面 UI 进程中可用"}

    def benchmark_run(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._controller.benchmark_run(key_id, prompt, image_paths, options)

    def cancel_image_generation(self, request_id: str) -> dict[str, Any]:
        return self._controller.cancel_image_generation(request_id)

    def polish_prompt(self, key_id: str, prompt: str) -> dict[str, Any]:
        return self._controller.polish_prompt(key_id, prompt)

    def refresh_now(self, trace_id: Any = None) -> dict[str, Any]:
        return self._controller.refresh_now(trace_id)

    def update_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        return self._controller.update_thresholds(thresholds)

    def update_rate_limit_progress_mode(self, mode: Any) -> dict[str, Any]:
        return self._controller.update_rate_limit_progress_mode(mode)

    def update_refresh_intervals(self, foreground: Any, background: Any) -> dict[str, Any]:
        return self._controller.update_refresh_intervals(foreground, background)

    def update_app_preferences(
        self,
        update_frequency: Any,
        close_action: Any,
        startup_enabled: Any,
        title_bar_mode: Any = None,
        background_ui_mode: Any = None,
    ) -> dict[str, Any]:
        return self._controller.update_app_preferences(
            update_frequency,
            close_action,
            startup_enabled,
            title_bar_mode,
            background_ui_mode,
        )

    def restart_app(self) -> dict[str, Any]:
        return self._controller.restart_app()

    def check_for_updates(self) -> dict[str, Any]:
        return self._controller.check_for_updates(manual=True)

    def download_update(self) -> dict[str, Any]:
        return self._controller.download_update()

    def defer_update_restart(self) -> dict[str, Any]:
        return self._controller.defer_update_restart()

    def restart_update(self) -> dict[str, Any]:
        return self._controller.restart_update()

    def dismiss_update_prompt(self) -> dict[str, Any]:
        return self._controller.dismiss_update_prompt()

    def ignore_update_version(self, version: Any) -> dict[str, Any]:
        return self._controller.ignore_update_version(version)

    def resolve_close_action(self, action: Any) -> dict[str, Any]:
        return self._controller.resolve_close_action(action)

    def window_action(self, action: str) -> dict[str, Any]:
        return self._controller.window_action(action)

    def set_always_on_top(self, enabled: Any) -> dict[str, Any]:
        return self._controller.set_always_on_top(enabled)

    def set_window_size(self, width: Any, height: Any) -> dict[str, Any]:
        return self._controller.set_window_size(width, height)

    def set_window_background(self, mode: Any) -> dict[str, Any]:
        return self._controller.set_window_background(mode)

    def native_drag(self, direction: str) -> dict[str, Any]:
        return self._controller.native_drag(direction)

    def open_devtools(self) -> dict[str, Any]:
        return self._controller.open_devtools()

    def report_startup(self, stage: str, navigation_ms: Any = 0) -> dict[str, Any]:
        return self._controller.report_startup(stage, navigation_ms)

    def save_edited_image(self, source_path: str) -> dict[str, Any]:
        return self._controller.save_edited_image(source_path)
