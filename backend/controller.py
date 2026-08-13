from __future__ import annotations

from .common import *
from .platform import *
from .usage import *
from .web_search import WebSearchService
from .store import Store
from .client import EasyClinClient
from .controller_mixins.window_state import WindowStateMixin
from .controller_mixins.image_files import ImageFilesMixin
from .controller_mixins.workers_window import WorkersWindowMixin
from .controller_mixins.updates import UpdateMixin
from .controller_mixins.quota_state import QuotaStateMixin
from .controller_mixins.prompt import PromptMixin
from .controller_mixins.image_reasoning import ImageReasoningMixin, ImageTaskContext
from .controller_mixins.settings import SettingsMixin
from .controller_mixins.window_commands import WindowCommandsMixin

class AppController(WindowStateMixin, ImageFilesMixin, WorkersWindowMixin, UpdateMixin, QuotaStateMixin, PromptMixin, ImageReasoningMixin, SettingsMixin, WindowCommandsMixin):
    def __init__(
        self,
        asset_cache: StaticAssetCache | None = None,
        ui_show_callback: Any = None,
        ui_hide_callback: Any = None,
    ) -> None:
        trace_startup("controller_init_started")
        self.asset_cache = asset_cache or StaticAssetCache()
        self.store = Store(app_data_dir() / "api_tools.db")
        self.restart_ready_path = os.environ.pop(RESTART_READY_ENV, "").strip()
        self.active_title_bar_mode = self.store.get_title_bar_mode()
        self.client = EasyClinClient()
        self.image_generator = ImageGenerationService()
        self.web_search = WebSearchService()
        self.store.import_environment_key()
        self.window: webview.Window | None = None
        self.tray: Any = None
        self.ui_show_callback = ui_show_callback
        self.ui_hide_callback = ui_hide_callback
        self.visible = True
        self.ui_visibility_token = 0
        self.always_on_top = self.store.get_always_on_top()
        self.maximized = False
        saved_window_size = self.store.get_window_size()
        self._last_saved_window_size = (
            saved_window_size["width"],
            saved_window_size["height"],
        )
        self._window_size_lock = threading.Lock()
        self._pending_window_size: tuple[int, int] | None = None
        self._window_size_timer: threading.Timer | None = None
        self.drag_restore_suppressed_until = 0.0
        self.stopping = threading.Event()
        self.frontend_ready = threading.Event()
        self.refresh_wakeup = threading.Event()
        self.refresh_lock = threading.Lock()
        self.manual_refresh_lock = threading.Lock()
        self.manual_refresh_available_at = 0.0
        self.update_lock = threading.Lock()
        self.image_stream_debug_lock = threading.Lock()
        self.active_image_sets: set[str] = set()
        self.image_session_activity_lock = threading.Lock()
        self.active_image_sessions: set[str] = set()
        self.image_task_lock = threading.Lock()
        self.image_tasks: dict[str, ImageTaskContext] = {}
        full_release_notes = bundled_changelog()
        self.update_state: dict[str, Any] = {
            "status": "idle",
            "percent": 0,
            "message": "尚未检查更新",
            "currentVersion": APP_VERSION,
            "latestVersion": APP_VERSION,
            "releaseNotes": changelog_for_update(
                full_release_notes, APP_VERSION, APP_VERSION
            ),
            "fullReleaseNotes": full_release_notes,
            "available": False,
            "showPrompt": False,
        }
        intervals = self.store.get_refresh_intervals()
        self.foreground_interval = intervals["foreground"]
        self.background_interval = intervals["background"]
        self.next_refresh_at = time.time() + self.foreground_interval
        self.icon_png = resource_path("resources/api_tools_icon.png")
        trace_startup("controller_init_finished")
