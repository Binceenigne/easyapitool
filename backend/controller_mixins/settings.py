from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class SettingsMixin:
    def delete_key(self, key_id: str) -> dict[str, Any]:
        self.store.delete_key(key_id)
        return {"ok": True, "state": self.get_state()}

    def refresh_now(self, trace_id: Any = None) -> dict[str, Any]:
        trace_id = str(trace_id or "").strip()[:80] or "backend-refresh"
        debug_started_at = time.perf_counter()
        debug_events: list[dict[str, Any]] = []

        def mark_debug(event: str, **details: Any) -> None:
            debug_events.append({
                "event": event,
                "elapsedMs": round((time.perf_counter() - debug_started_at) * 1000, 1),
                **details,
            })

        def with_debug(payload: dict[str, Any], outcome: str) -> dict[str, Any]:
            mark_debug(
                "response",
                outcome=outcome,
                manualLockLocked=self.manual_refresh_lock.locked(),
                refreshLockLocked=self.refresh_lock.locked(),
            )
            payload["debug"] = {
                "traceId": trace_id,
                "outcome": outcome,
                "durationMs": round((time.perf_counter() - debug_started_at) * 1000, 1),
                "events": debug_events,
            }
            return payload

        now = time.monotonic()
        cooldown = max(0.0, self.manual_refresh_available_at - now)
        mark_debug(
            "received",
            cooldownSeconds=round(cooldown, 3),
            manualLockLocked=self.manual_refresh_lock.locked(),
            refreshLockLocked=self.refresh_lock.locked(),
        )
        if cooldown > 0:
            return with_debug({
                "ok": False,
                "busy": False,
                "cooldownSeconds": cooldown,
                "error": "手动刷新冷却中",
            }, "cooldown")
        if not self.manual_refresh_lock.acquire(blocking=False):
            return with_debug({
                "ok": False,
                "busy": True,
                "cooldownSeconds": cooldown,
                "error": "手动刷新正在进行",
            }, "manual-lock-busy")
        mark_debug("manual-lock-acquired")
        try:
            if not self.refresh_lock.acquire(blocking=False):
                return with_debug({
                    "ok": False,
                    "busy": True,
                    "cooldownSeconds": 0,
                    "error": "后台刷新正在进行",
                }, "background-lock-busy")
            mark_debug("refresh-lock-acquired")
            self.manual_refresh_available_at = (
                now + MANUAL_REFRESH_COOLDOWN_SECONDS
            )
            try:
                refreshed: list[str] = []
                failed: list[str] = []
                records = self.store.list_key_records()
                mark_debug("keys-loaded", count=len(records))
                for index, record in enumerate(records, start=1):
                    mark_debug("key-refresh-started", index=index, total=len(records))
                    succeeded = self._refresh_key(record["id"])
                    mark_debug(
                        "key-refresh-finished",
                        index=index,
                        total=len(records),
                        succeeded=succeeded,
                    )
                    if succeeded:
                        refreshed.append(record["id"])
                    else:
                        failed.append(record["id"])
            finally:
                self.refresh_lock.release()
                mark_debug("refresh-lock-released")
            valid = bool(refreshed) and not failed
            return with_debug({
                "ok": valid,
                "valid": valid,
                "refreshed": refreshed,
                "failed": failed,
                "cooldownSeconds": max(
                    0.0, self.manual_refresh_available_at - time.monotonic()
                ),
                "state": self.get_state(),
                "error": "" if valid else "未获取到全部密钥的有效回复",
            }, "success" if valid else "invalid-response")
        finally:
            self.manual_refresh_lock.release()
            mark_debug("manual-lock-released")

    def update_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        clean = self.store.set_thresholds(thresholds)
        self.store.reset_limit_alerts()
        return {"ok": True, "thresholds": clean}

    def update_rate_limit_progress_mode(self, mode: Any) -> dict[str, Any]:
        clean = self.store.set_rate_limit_progress_mode(mode)
        return {"ok": True, "rateLimitProgressMode": clean}

    def update_refresh_intervals(
        self, foreground: Any, background: Any
    ) -> dict[str, Any]:
        intervals = self.store.set_refresh_intervals(foreground, background)
        self.foreground_interval = intervals["foreground"]
        self.background_interval = intervals["background"]
        self.next_refresh_at = time.time() + (
            self.foreground_interval if self.visible else self.background_interval
        )
        self.refresh_wakeup.set()
        return {"ok": True, "refreshIntervals": intervals, "state": self.get_state()}

    def update_app_preferences(
        self,
        update_frequency: Any,
        close_action: Any,
        startup_enabled: Any,
        title_bar_mode: Any = None,
        background_ui_mode: Any = None,
    ) -> dict[str, Any]:
        frequency = self.store.set_update_frequency(update_frequency)
        action = self.store.set_close_action(close_action)
        title_bar = (
            self.store.get_title_bar_mode()
            if title_bar_mode is None
            else self.store.set_title_bar_mode(title_bar_mode)
        )
        background_mode = (
            self.store.get_background_ui_mode()
            if background_ui_mode is None
            else self.store.set_background_ui_mode(background_ui_mode)
        )
        startup = set_startup_enabled(bool(startup_enabled))
        return {
            "ok": True,
            "updateFrequency": frequency,
            "closeAction": action,
            "backgroundUiMode": background_mode,
            "titleBarMode": title_bar,
            "startupEnabled": startup,
            "state": self.get_state(),
        }
