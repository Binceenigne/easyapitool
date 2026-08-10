from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class QuotaStateMixin:
    def refresh_all(self, push_ui: bool = False) -> dict[str, Any]:
        refreshed: list[str] = []
        failed: list[str] = []
        with self.refresh_lock:
            for record in self.store.list_key_records():
                if self._refresh_key(record["id"]):
                    refreshed.append(record["id"])
                else:
                    failed.append(record["id"])
            if push_ui:
                self.push_state_to_ui()
        return {"refreshed": refreshed, "failed": failed}

    def _refresh_key(self, key_id: str) -> bool:
        record = self.store.get_key_record(key_id)
        if record is None:
            return False
        try:
            previous_payload = self.store.latest_payload(key_id)
            payload, models = self.client.fetch(record["base_url"], self.store.get_secret(key_id))
            if models is None:
                models = list((previous_payload or {}).get("_models") or [])
            payload["_models"] = models
            payload["_models_count"] = len(models)
            changed_names = annotate_limit_changes(payload, previous_payload)
            alert_metrics = {
                "quota": "总额度",
                "5h": "5h 限额",
                "1d": "1d 限额",
                "7d": "7d 限额",
            }
            current_limits = limit_definitions(payload)
            self.store.reset_alert_metrics(
                key_id,
                {
                    alert_metrics[name]
                    for name in changed_names
                    if current_limits[name] <= 0
                },
            )
            self.store.save_snapshot(key_id, payload)
            self._notify_limit_changes(record["name"], payload, changed_names)
            self._check_alerts(key_id, record["name"], payload)
            return True
        except Exception as exc:
            self.store.set_error(key_id, str(exc))
            return False

    def _notify_limit_changes(
        self, key_name: str, payload: dict[str, Any], changed_names: set[str]
    ) -> None:
        labels = {
            "quota": "总额度上限",
            "5h": "5h 速率限制",
            "1d": "1d 速率限制",
            "7d": "7d 速率限制",
        }
        changes = payload.get("_limit_changes") or {}
        for limit_name in ("quota", "5h", "1d", "7d"):
            if limit_name not in changed_names:
                continue
            change = changes.get(limit_name) or {}
            previous = safe_float(change.get("previous"))
            current = safe_float(change.get("current"))
            previous_text = f"{previous:g} USD"
            current_text = f"{current:g} USD"
            if previous <= 0 < current:
                message = f"您的{labels[limit_name]}已新增为 {current_text}"
                severity = 1
            elif current <= 0 < previous:
                message = f"您的{labels[limit_name]}已取消，原限制为 {previous_text}"
                severity = 2
            elif current > previous:
                message = (
                    f"您的{labels[limit_name]}已从 {previous_text} 提高到 {current_text}"
                )
                severity = 0
            else:
                message = (
                    f"您的{labels[limit_name]}已从 {previous_text} 降低到 {current_text}"
                )
                severity = 2
            self.notify(f"{key_name} · 限制调整", message, severity=severity)

    def _check_alerts(self, key_id: str, name: str, payload: dict[str, Any]) -> None:
        thresholds = self.store.get_thresholds()
        metrics: dict[str, float] = {}
        quota = payload.get("quota") or {}
        if safe_float(quota.get("limit")) > 0:
            metrics["总额度"] = 100 * safe_float(quota.get("remaining")) / safe_float(quota.get("limit"))
        for item in payload.get("rate_limits") or []:
            limit = safe_float(item.get("limit"))
            if limit > 0:
                metrics[f"{item.get('window')} 限额"] = 100 * safe_float(item.get("remaining")) / limit
        for metric, percentage in metrics.items():
            if percentage <= thresholds["critical"]:
                severity = 3
            elif percentage <= thresholds["danger"]:
                severity = 2
            elif percentage <= thresholds["warn"]:
                severity = 1
            else:
                severity = 0
            previous = self.store.alert_severity(key_id, metric)
            if severity > previous:
                labels = {
                    1: f"{thresholds['warn']:g}% 预警",
                    2: f"{thresholds['danger']:g}% 危险",
                    3: f"{thresholds['critical']:g}% 严重",
                }
                self.notify(
                    f"{name} · {labels[severity]}",
                    f"{metric}仅剩 {percentage:.2f}%，请及时检查额度。",
                    severity=severity,
                )
            if severity != previous:
                self.store.set_alert_severity(key_id, metric, severity)

        rates_method = getattr(self.store, "rates", None)
        if not callable(rates_method):
            return
        rates = rates_method(key_id) or {}
        intervals = rates.get("intervals") or {}
        for interval_name, seconds in (("10m", 600), ("1h", 3600)):
            interval = intervals.get(interval_name) or {}
            metric = f"{interval_name} 负载"
            if interval.get("status") != "recorded" or interval.get("value") is None:
                continue
            load = interval_load_components(payload, safe_float(interval.get("value")))
            pressure = load["overall"]
            severity = 2 if pressure >= 85 else 1 if pressure >= 65 else 0
            previous = self.store.alert_severity(key_id, metric)
            if severity > previous:
                level = "极高负载" if severity == 2 else "高负载"
                self.notify(
                    f"{name} · {load['source']}{level}",
                    f"最近 {interval_name} 用量 ${safe_float(interval.get('value')):.4f}，综合负载 {pressure:.0f}%"
                    f"（额度 {load['quotaPercent']:.2f}% / 速率 {load['ratePercent']:.2f}%）。",
                    severity=severity,
                )
            if severity != previous:
                self.store.set_alert_severity(key_id, metric, severity)

    def notify(self, title: str, message: str, severity: int = 0) -> None:
        try:
            icon_names = {
                1: "api_tools_warn.png",
                2: "api_tools_danger.png",
                3: "api_tools_critical.png",
            }
            icon_name = icon_names.get(int(severity), "api_tools_normal.png")
            notification = Notification(
                app_id="API_TOOLS 密钥监控",
                title=title,
                msg=message,
                icon=str(resource_path(f"resources/icons/{icon_name}")),
                duration="long",
            )
            notification.set_audio(audio.Default, loop=False)
            notification.show()
        except Exception:
            pass

    def _masked_value(self, secret: str) -> str:
        if len(secret) <= 8:
            return "*" * len(secret)
        return f"{secret[:4]}...{secret[-4:]}"

    def _normalize(self, record: sqlite3.Row, payload: dict[str, Any] | None) -> dict[str, Any]:
        payload = payload or {}
        quota = payload.get("quota") or {}
        windows = {str(item.get("window")): item for item in payload.get("rate_limits") or []}
        usage = payload.get("usage") or {}
        today = usage.get("today") or {}
        total = usage.get("total") or {}
        limit = safe_float(quota.get("limit"))
        remaining = safe_float(quota.get("remaining"), safe_float(payload.get("remaining")))
        used = safe_float(quota.get("used"), max(0.0, limit - remaining))
        if limit <= 0 and payload.get("balance") is not None:
            limit = safe_float(payload.get("balance")) + safe_float(total.get("cost"))
            remaining = safe_float(payload.get("balance"))
            used = safe_float(total.get("cost"))

        def window_data(name: str) -> dict[str, Any]:
            item = windows.get(name) or {}
            change = (payload.get("_limit_changes") or {}).get(name)
            window_limit = max(0.0, safe_float(item.get("limit")))
            window_used = max(0.0, safe_float(item.get("used")))
            remaining_value = item.get("remaining")
            if remaining_value is None:
                window_remaining = max(0.0, window_limit - window_used)
            else:
                window_remaining = min(
                    window_limit,
                    max(0.0, safe_float(remaining_value)),
                )
            return {
                "limit": window_limit,
                "used": window_used,
                "remaining": window_remaining,
                "resetTime": item.get("reset_at"),
                "windowStart": item.get("window_start"),
                "limitChange": change,
            }

        expires = payload.get("expires_at") or ((payload.get("subscription") or {}).get("expires_at"))
        return {
            "id": record["id"],
            "name": record["name"],
            "value": self._masked_value(self.store.get_secret(record["id"])),
            "status": payload.get("status") or ("active" if payload.get("isValid") else "error"),
            "mode": payload.get("mode") or "unknown",
            "planName": payload.get("planName") or "",
            "expireDateStr": expires or "",
            "expireTimestamp": (parse_timestamp(expires) or 0) * 1000,
            "totalQuota": limit,
            "usedQuota": used,
            "remainingQuota": remaining,
            "quotaLimitChange": (payload.get("_limit_changes") or {}).get("quota"),
            "win5h": window_data("5h"),
            "win1d": window_data("1d"),
            "win7d": window_data("7d"),
            "todayRequests": int(today.get("requests") or 0),
            "totalRequests": int(total.get("requests") or 0),
            "todayCost": safe_float(today.get("cost")),
            "totalCost": safe_float(total.get("cost")),
            "modelsCount": int(payload.get("_models_count") or 0),
            "models": [str(model) for model in payload.get("_models") or [] if str(model).strip()],
            "rates": self.store.rates(record["id"]),
            "lastError": record["last_error"],
        }

    def get_state(self) -> dict[str, Any]:
        keys = [self._normalize(record, self.store.latest_payload(record["id"])) for record in self.store.list_key_records()]
        get_window_size = getattr(self.store, "get_window_size", None)
        window_size = (
            get_window_size()
            if callable(get_window_size)
            else normalize_window_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        )
        return {
            "keys": keys,
            "thresholds": self.store.get_thresholds(),
            "rateLimitProgressMode": self.store.get_rate_limit_progress_mode(),
            "appVersion": APP_VERSION,
            "githubRepository": GITHUB_REPOSITORY,
            "releaseBranch": RELEASE_BRANCH,
            "releaseTagPrefix": RELEASE_TAG_PREFIX,
            "updateFrequency": self.store.get_update_frequency(),
            "ignoredUpdateVersion": (
                self.store.get_ignored_update_version()
                if hasattr(self.store, "get_ignored_update_version")
                else ""
            ),
            "closeAction": self.store.get_close_action(),
            "alwaysOnTop": bool(getattr(self, "always_on_top", False)),
            "windowSize": window_size,
            "backgroundUiMode": self.store.get_background_ui_mode(),
            "titleBarMode": self.store.get_title_bar_mode(),
            "activeTitleBarMode": self.active_title_bar_mode,
            "startupEnabled": startup_is_enabled(),
            "update": dict(self.update_state),
            "refreshIntervals": {
                "foreground": self.foreground_interval,
                "background": self.background_interval,
            },
            "isForeground": self.visible,
            "nextRefreshSeconds": max(0, int(self.next_refresh_at - time.time())),
            "databasePath": str(self.store.path),
        }

    def add_key(self, name: str, value: str) -> dict[str, Any]:
        name = (name or "").strip()
        value = (value or "").strip()
        if not name or not value:
            return {"ok": False, "error": "昵称和密钥不能为空"}
        base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        key_id = self.store.add_key(name, value, base_url)
        with self.refresh_lock:
            self._refresh_key(key_id)
        record = self.store.get_key_record(key_id)
        if record and record["last_error"]:
            error = record["last_error"]
            self.store.delete_key(key_id)
            return {"ok": False, "error": error}
        return {"ok": True, "activeKeyId": key_id, "state": self.get_state()}
