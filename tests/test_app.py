import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = app.Store(Path(self.temp.name) / "test.db")

    def tearDown(self):
        self.temp.cleanup()

    @patch("app.protect_secret", side_effect=lambda value: value)
    @patch("app.unprotect_secret", side_effect=lambda value: value)
    def test_snapshot_and_rates(self, _unprotect, _protect):
        key_id = self.store.add_key("test", "secret", "https://example.test/v1")
        payload = {
            "mode": "quota_limited",
            "isValid": True,
            "status": "active",
            "quota": {"limit": 200, "used": 50, "remaining": 150},
            "remaining": 150,
            "usage": {
                "today": {"cost": 12, "requests": 60},
                "total": {"cost": 50, "requests": 800},
            },
            "rate_limits": [
                {"window": "5h", "limit": 27, "used": 1, "remaining": 26},
                {"window": "1d", "limit": 44, "used": 12, "remaining": 32},
                {"window": "7d", "limit": 100, "used": 30, "remaining": 70},
            ],
            "daily_usage": [{"date": "2026-07-14", "cost": 12, "requests": 60}],
        }
        self.store.save_snapshot(key_id, payload)
        loaded = self.store.latest_payload(key_id)
        self.assertEqual(loaded["quota"]["remaining"], 150)
        self.assertEqual(self.store.rates(key_id)["avgDay"], 12)

    @patch("app.protect_secret", side_effect=lambda value: value)
    @patch("app.unprotect_secret", side_effect=lambda value: value)
    def test_first_nonzero_snapshot_is_unrecorded_then_unchanged_total_estimates_zero(
        self, _unprotect, _protect
    ):
        key_id = self.store.add_key("new", "secret", "https://example.test/v1")
        payload = {
            "usage": {
                "today": {"cost": 1, "requests": 1},
                "total": {"cost": 80, "requests": 40},
            }
        }
        first_sampled_at = datetime(2026, 7, 15, 10, 0, tzinfo=app.BUSINESS_TIMEZONE).timestamp()
        with patch("app.time.time", return_value=first_sampled_at):
            self.store.save_snapshot(key_id, payload)
            first_rates = self.store.rates(key_id)

        self.assertIsNone(first_rates["speed10m"])
        self.assertEqual(first_rates["intervals"]["10m"]["status"], "unrecorded")
        self.assertTrue(all(item["status"] == "unrecorded" for item in first_rates["hourly12h"]))

        with patch("app.time.time", return_value=first_sampled_at + 600):
            self.store.save_snapshot(key_id, payload)
            second_rates = self.store.rates(key_id)

        self.assertEqual(second_rates["speed10m"], 0)
        self.assertEqual(second_rates["intervals"]["10m"]["status"], "recorded")
        self.assertEqual(second_rates["speed1h"], 0)
        self.assertEqual(second_rates["intervals"]["1h"]["status"], "estimated")

    @patch("app.protect_secret", side_effect=lambda value: value)
    @patch("app.unprotect_secret", side_effect=lambda value: value)
    def test_complete_auto_refresh_window_is_recorded_without_extrapolation(
        self, _unprotect, _protect
    ):
        key_id = self.store.add_key("auto", "secret", "https://example.test/v1")
        started_at = datetime(2026, 7, 15, 10, 0, tzinfo=app.BUSINESS_TIMEZONE).timestamp()
        for minute in range(12):
            payload = {
                "usage": {
                    "today": {"cost": minute * 0.02, "requests": minute},
                    "total": {"cost": 50 + minute * 0.02, "requests": minute},
                }
            }
            with patch("app.time.time", return_value=started_at + minute * 60):
                self.store.save_snapshot(key_id, payload)

        with patch("app.time.time", return_value=started_at + 11 * 60):
            rates = self.store.rates(key_id)

        self.assertEqual(rates["intervals"]["10m"]["status"], "recorded")
        self.assertAlmostEqual(rates["intervals"]["10m"]["value"], 0.2, places=7)
        self.assertEqual(rates["intervals"]["10m"]["observedSeconds"], 600)

    @patch("app.protect_secret", side_effect=lambda value: value)
    @patch("app.unprotect_secret", side_effect=lambda value: value)
    def test_snapshot_rejects_missing_total_cost(self, _unprotect, _protect):
        key_id = self.store.add_key("invalid", "secret", "https://example.test/v1")

        with self.assertRaisesRegex(ValueError, "累计用量"):
            self.store.save_snapshot(key_id, {"usage": {"today": {"cost": 1}}})

        self.assertIsNone(self.store.latest_payload(key_id))

    @patch("app.protect_secret", side_effect=lambda value: value)
    @patch("app.unprotect_secret", side_effect=lambda value: value)
    def test_store_initialization_removes_legacy_missing_total_snapshot(
        self, _unprotect, _protect
    ):
        key_id = self.store.add_key("legacy", "secret", "https://example.test/v1")
        now = datetime(2026, 7, 15, 10, 0, tzinfo=app.BUSINESS_TIMEZONE).timestamp()
        with self.store.lock, self.store.connect() as db:
            db.execute(
                """INSERT INTO usage_snapshots(
                    key_id,sampled_at,total_cost,payload_json
                ) VALUES(?,?,?,?)""",
                (key_id, now, 0, json.dumps({"usage": {"today": {"cost": 1}}})),
            )

        reloaded = app.Store(self.store.path)

        self.assertIsNone(reloaded.latest_payload(key_id))

    def test_thresholds_are_persisted(self):
        result = self.store.set_thresholds({"warn": 30, "danger": 12, "critical": 4})
        self.assertEqual(result, {"warn": 30.0, "danger": 12.0, "critical": 4.0})
        self.assertEqual(self.store.get_thresholds(), result)

    def test_refresh_intervals_are_clamped_and_persisted(self):
        minimums = self.store.set_refresh_intervals(10, 120)
        self.assertEqual(
            minimums,
            {"foreground": app.FOREGROUND_INTERVAL, "background": app.BACKGROUND_INTERVAL},
        )

        custom = self.store.set_refresh_intervals(180, 900)
        self.assertEqual(custom, {"foreground": 180, "background": 900})
        self.assertEqual(self.store.get_refresh_intervals(), custom)

    def test_rate_limit_progress_mode_is_normalized_and_persisted(self):
        self.assertEqual(self.store.get_rate_limit_progress_mode(), "remaining")
        self.assertEqual(self.store.set_rate_limit_progress_mode("used"), "used")
        self.assertEqual(self.store.get_rate_limit_progress_mode(), "used")
        self.assertEqual(self.store.set_rate_limit_progress_mode("invalid"), "remaining")
        self.assertEqual(self.store.get_rate_limit_progress_mode(), "remaining")

    def test_application_preferences_are_normalized_and_persisted(self):
        self.assertEqual(self.store.get_update_frequency(), "startup")
        self.assertEqual(self.store.get_close_action(), "ask")
        self.assertEqual(self.store.set_update_frequency("weekly"), "weekly")
        self.assertEqual(self.store.set_close_action("tray"), "tray")
        self.assertEqual(self.store.get_update_frequency(), "weekly")
        self.assertEqual(self.store.get_close_action(), "tray")
        self.assertEqual(self.store.set_update_frequency("invalid"), "startup")
        self.assertEqual(self.store.set_close_action("invalid"), "ask")

    def test_last_update_check_is_persisted(self):
        self.assertEqual(self.store.get_last_update_check(), 0)
        self.assertEqual(self.store.set_last_update_check(123.5), 123.5)
        self.assertEqual(self.store.get_last_update_check(), 123.5)

    @patch("app.protect_secret", side_effect=lambda value: value)
    @patch("app.unprotect_secret", side_effect=lambda value: value)
    def test_rates_use_utc8_natural_day_and_week_with_missing_days_as_zero(
        self, _unprotect, _protect
    ):
        key_id = self.store.add_key("test", "secret", "https://example.test/v1")
        now_local = datetime(2026, 7, 15, 12, 0, tzinfo=app.BUSINESS_TIMEZONE)
        payload = {
            "usage": {
                "today": {"cost": 6, "requests": 3},
                "total": {"cost": 100, "requests": 20},
            },
            "daily_usage": [
                {"date": "2026-07-13", "cost": 12, "requests": 6},
                {"date": "2026-07-15", "cost": 6, "requests": 3},
            ],
        }
        with patch("app.time.time", return_value=now_local.timestamp()):
            self.store.save_snapshot(key_id, payload)
            rates = self.store.rates(key_id)

        self.assertEqual(rates["timezone"], "UTC+8")
        self.assertEqual(rates["averages"]["today"]["label"], "2026-07-15")
        self.assertAlmostEqual(rates["averages"]["today"]["avgHour"], 0.5)
        self.assertEqual(rates["averages"]["week"]["cost"], 18)
        self.assertAlmostEqual(rates["averages"]["week"]["avgHour"], 0.3)

    @patch("app.protect_secret", side_effect=lambda value: value)
    @patch("app.unprotect_secret", side_effect=lambda value: value)
    def test_rates_return_twelve_hour_buckets(self, _unprotect, _protect):
        key_id = self.store.add_key("test", "secret", "https://example.test/v1")
        now_local = datetime(2026, 7, 15, 12, 30, tzinfo=app.BUSINESS_TIMEZONE)
        for index in range(13):
            sampled_at = datetime(2026, 7, 15, index, 0, tzinfo=app.BUSINESS_TIMEZONE)
            payload = {
                "usage": {
                    "today": {"cost": index, "requests": index},
                    "total": {"cost": 100 + index, "requests": index},
                },
                "daily_usage": [{"date": "2026-07-15", "cost": index, "requests": index}],
            }
            with patch("app.time.time", return_value=sampled_at.timestamp()):
                self.store.save_snapshot(key_id, payload)

        with patch("app.time.time", return_value=now_local.timestamp()):
            rates = self.store.rates(key_id)

        self.assertEqual(len(rates["hourly12h"]), 12)
        self.assertEqual(rates["hourly12h"][0]["cost"], 1)
        self.assertEqual(rates["hourly12h"][-1]["cost"], 1)
        self.assertTrue(all(item["status"] == "recorded" for item in rates["hourly12h"]))
        self.assertTrue(all(item["endTimestamp"] >= item["startTimestamp"] for item in rates["hourly12h"]))
        for item in rates["hourly12h"]:
            start = datetime.fromtimestamp(item["startTimestamp"] / 1000, app.BUSINESS_TIMEZONE)
            end = datetime.fromtimestamp(item["endTimestamp"] / 1000, app.BUSINESS_TIMEZONE)
            self.assertEqual((start.minute, start.second, start.microsecond), (0, 0, 0))
            self.assertEqual((end.minute, end.second, end.microsecond), (0, 0, 0))
            self.assertEqual((end - start).total_seconds(), 3600)


class UtilityTests(unittest.TestCase):
    def test_semantic_version_comparison(self):
        self.assertTrue(app.is_newer_version("v1.1.0", "1.0.9"))
        self.assertFalse(app.is_newer_version("1.0", "1.0.0"))
        self.assertFalse(app.is_newer_version("preview", "1.0.0"))

    def test_parse_timestamp(self):
        self.assertIsNotNone(app.parse_timestamp("2026-09-09T00:18:00+08:00"))
        self.assertIsNone(app.parse_timestamp(None))

    def test_interval_load_pressure_uses_quota_and_rate_percentages(self):
        payload = {
            "quota": {"limit": 110000, "remaining": 110000},
            "rate_limits": [
                {"window": "5h", "limit": 27},
                {"window": "1d", "limit": 44},
                {"window": "7d", "limit": 100},
            ]
        }

        load = app.interval_load_components(payload, 0.2)

        self.assertAlmostEqual(load["quotaPercent"], 0.000181818, places=7)
        self.assertAlmostEqual(load["ratePercent"], 0.7407407407)
        self.assertAlmostEqual(load["overall"], 7.407407407)
        self.assertEqual(load["source"], "速率")

    def test_large_quota_keeps_small_unlimited_spend_low(self):
        payload = {
            "quota": {"limit": 110000, "remaining": 110000},
            "rate_limits": [{"window": "5h", "limit": 0}],
        }

        load = app.interval_load_components(payload, 5)

        self.assertLess(load["overall"], 1)
        self.assertEqual(load["rate"], 0)
        self.assertEqual(load["source"], "额度")

    def test_ten_percent_of_rate_limit_is_medium_load(self):
        payload = {
            "quota": {"limit": 100000, "remaining": 100000},
            "rate_limits": [{"window": "5h", "limit": 10}],
        }

        load = app.interval_load_components(payload, 1)

        self.assertEqual(load["ratePercent"], 10)
        self.assertEqual(load["overall"], 45)
        self.assertEqual(load["source"], "速率")

    def test_quota_or_rate_channel_uses_higher_pressure(self):
        payload = {
            "quota": {"limit": 10, "remaining": 10},
            "rate_limits": [{"window": "5h", "limit": 1000}],
        }

        load = app.interval_load_components(payload, 5)

        self.assertEqual(load["overall"], load["quota"])
        self.assertEqual(load["source"], "额度")

    def test_dpapi_round_trip(self):
        secret = "test-secret-value"
        self.assertEqual(app.unprotect_secret(app.protect_secret(secret)), secret)

    def test_limit_changes_are_carried_and_expire(self):
        previous = {
            "quota": {"limit": 200},
            "rate_limits": [{"window": "5h", "limit": 27}],
        }
        changed = {
            "quota": {"limit": 300},
            "rate_limits": [{"window": "5h", "limit": 44}],
        }

        names = app.annotate_limit_changes(changed, previous, changed_at=1000)

        self.assertEqual(names, {"quota", "5h"})
        self.assertEqual(changed["_limit_changes"]["quota"]["previous"], 200)
        self.assertEqual(changed["_limit_changes"]["5h"]["current"], 44)

        unchanged = {
            "quota": {"limit": 300},
            "rate_limits": [{"window": "5h", "limit": 44}],
        }
        app.annotate_limit_changes(unchanged, changed, changed_at=1100)
        self.assertIn("5h", unchanged["_limit_changes"])

        expired = {
            "quota": {"limit": 300},
            "rate_limits": [{"window": "5h", "limit": 44}],
        }
        app.annotate_limit_changes(expired, unchanged, changed_at=1701)
        self.assertNotIn("_limit_changes", expired)

    def test_limit_cancellation_is_detected(self):
        previous = {"rate_limits": [{"window": "1d", "limit": 44}]}
        current = {"rate_limits": []}

        names = app.annotate_limit_changes(current, previous, changed_at=1000)

        self.assertIn("1d", names)
        self.assertEqual(current["_limit_changes"]["1d"]["previous"], 44)
        self.assertEqual(current["_limit_changes"]["1d"]["current"], 0)

    def test_wallet_spending_does_not_look_like_limit_change(self):
        previous = {
            "balance": 100,
            "usage": {"total": {"cost": 25}},
        }
        current = {
            "balance": 95,
            "usage": {"total": {"cost": 30}},
        }

        names = app.annotate_limit_changes(current, previous, changed_at=1000)

        self.assertNotIn("quota", names)

    def test_client_returns_unique_model_ids(self):
        client = app.EasyClinClient()
        responses = [
            {"usage": {"today": {"cost": 1}}},
            {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o"}, {"id": "claude-3.5"}, {"id": ""}]},
        ]

        with patch.object(client, "get_json", side_effect=responses):
            usage, models = client.fetch("https://example.test/v1", "secret")

        self.assertEqual(models, ["gpt-4o", "claude-3.5"])
        self.assertEqual(usage["usage"]["today"]["cost"], 1)

    def test_client_keeps_usage_when_models_request_fails(self):
        client = app.EasyClinClient()

        with patch.object(
            client,
            "get_json",
            side_effect=[{"usage": {"total": {"cost": 5}}}, RuntimeError("models unavailable")],
        ):
            usage, models = client.fetch("https://example.test/v1", "secret")

        self.assertEqual(usage["usage"]["total"]["cost"], 5)
        self.assertIsNone(models)


class StaticAssetCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.bundle = root / "bundle"
        self.data = root / "data"
        (self.bundle / "assets").mkdir(parents=True)
        (self.bundle / app.MAIN_PAGE_NAME).write_text(
            '<script defer src="vendor/lucide/lucide.min.js"></script>',
            encoding="utf-8",
        )
        (self.bundle / "assets" / "app.css").write_text("body{}", encoding="utf-8")
        (self.bundle / "assets" / "title_logo.png").write_bytes(b"logo")
        self.cache = app.StaticAssetCache(self.data, self.bundle)

    def tearDown(self):
        self.temp.cleanup()

    def test_install_uses_validated_lucide_and_creates_ready_release(self):
        script = b"lucide-test-script"
        with patch.object(app, "LUCIDE_SHA256", app.sha256_bytes(script)):
            self.cache.expected_hashes["vendor/lucide/lucide.min.js"] = app.sha256_bytes(script)
            with patch.object(self.cache, "_download_lucide", return_value=script):
                self.cache.install()

        self.assertTrue(self.cache.is_ready())
        self.assertEqual(self.cache.status()["status"], "ready")
        self.assertTrue((self.cache.release_dir / "vendor/lucide/lucide.min.js").is_file())

    def test_corrupted_lucide_invalidates_release(self):
        script = b"lucide-test-script"
        digest = app.sha256_bytes(script)
        with patch.object(app, "LUCIDE_SHA256", digest):
            self.cache.expected_hashes["vendor/lucide/lucide.min.js"] = digest
            with patch.object(self.cache, "_download_lucide", return_value=script):
                self.cache.install()

        (self.cache.release_dir / "vendor/lucide/lucide.min.js").write_bytes(b"damaged")

        self.assertFalse(self.cache.is_ready())

    def test_primary_mirror_failure_falls_back_to_archive(self):
        script = b"lucide-test-script"
        digest = app.sha256_bytes(script)
        with patch.object(app, "LUCIDE_SHA256", digest):
            with patch.object(
                self.cache,
                "_read_url",
                side_effect=[RuntimeError("primary unavailable"), b"archive"],
            ) as read_url, patch.object(
                self.cache,
                "_script_from_archive",
                return_value=script,
            ):
                result = self.cache._download_lucide()

        self.assertEqual(result, script)
        self.assertEqual(read_url.call_count, 2)

    def test_main_page_has_lucide_icons_and_continuous_container_font_scaling(self):
        project_root = Path(__file__).parents[1]
        page = (project_root / app.MAIN_PAGE_NAME).read_text(encoding="utf-8")
        scss_source = (project_root / "assets" / "app.scss").read_text(encoding="utf-8")
        stylesheet = (project_root / "assets" / "app.css").read_text(encoding="utf-8")
        build_script = (project_root / "build.ps1").read_text(encoding="utf-8")

        self.assertIn("iconMarkup('infinity'", page)
        self.assertIn("[data-lucide]", page)
        self.assertIn("selectMostConstrainedWindow", page)
        self.assertIn('<link rel="stylesheet" href="assets/app.css?v=15">', page)
        self.assertIn("container-type: size", stylesheet)
        self.assertIn("cqi", stylesheet)
        self.assertIn("renderUsageTrend", page)
        self.assertIn("openModelModal", page)
        self.assertIn('id="keyToolbar"', page)
        self.assertIn("#keySelector", stylesheet)
        self.assertIn('id="dashboardMetrics"', page)
        self.assertIn('class="metric-cell metric-primary metric-today"', page)
        self.assertIn('class="metric-cell metric-primary metric-expiry"', page)
        self.assertIn(".height-summary-2", stylesheet)
        self.assertIn(".height-summary-4", stylesheet)
        self.assertIn(".height-summary-countdown", stylesheet)
        self.assertIn(".height-details-metrics", stylesheet)
        self.assertIn(".height-intervals", stylesheet)
        self.assertIn(".height-trend", stylesheet)
        self.assertIn(".height-full", stylesheet)
        self.assertIn('id="trendNodeLayer"', page)
        self.assertIn('id="usageTrendPlot"', page)
        self.assertIn("const node = document.createElement('span')", page)
        self.assertIn("node.style.backgroundColor = loadColor(point.pressure)", page)
        self.assertIn("LOAD_NODE_COLORS", page)
        self.assertIn("LOAD_TRANSITION_RECIPES", page)
        self.assertIn("LOAD_NODE_COLOR_INDEX", page)
        self.assertIn("transitionRecipe", page)
        self.assertIn("#a8cf18", page)
        self.assertIn("#facc15", page)
        self.assertIn("stop.x / width * 100", page)
        self.assertIn("gradientUnits', 'userSpaceOnUse'", page)
        self.assertIn("gradient.setAttribute('x2', String(width))", page)
        self.assertIn("$trend-node-size: 6.5px", scss_source)
        self.assertIn("@mixin fixed-square", scss_source)
        self.assertIn("#usageTrendPlot", stylesheet)
        self.assertIn("#trendNodeLayer", stylesheet)
        self.assertIn("inset: 0", stylesheet)
        self.assertIn("width: 6.5px", stylesheet)
        self.assertIn("height: 6.5px", stylesheet)
        self.assertIn("border-radius: 9999px !important", stylesheet)
        self.assertNotIn("point.bucket.status === 'estimated' ? '#ffffff'", page)
        self.assertIn(".width-wide:is(.height-details, .height-details-metrics, .height-intervals, .height-trend, .height-full) #speedPanel", stylesheet)
        self.assertIn("grid-template-rows: repeat(2, minmax(38px, 1fr))", stylesheet)
        self.assertIn(".width-narrow.height-intervals #usageAnalysisPanel", stylesheet)
        self.assertIn("#usageAnalysisPanel > #usageTrendSection", stylesheet)
        self.assertIn("} else if (width < 768) {", page)
        self.assertIn("const fullLayoutWidth = width >= 768 ? 768 : 340", page)
        self.assertIn("const fullLayoutHeight = trendHeight", page)
        self.assertIn("--content-scale", page)
        self.assertIn("calc(7px * var(--content-scale, 1))", scss_source)
        self.assertIn("item.status !== 'unrecorded'", page)
        self.assertIn("calculateLoadComponents", page)
        self.assertIn("Math.max(quota, rate)", page)
        self.assertIn("cost / totalQuota * 100", page)
        self.assertIn("cost / limit * 100", page)
        self.assertNotIn("sustainedBudgetPct", page)
        self.assertNotIn("pressures.push((used / limit) * 100)", page)
        self.assertIn('id="modelModalPanel"', page)
        self.assertIn("width: min(88%, 1680px)", stylesheet)
        self.assertIn("const DEVTOOLS_SEQUENCE = 'ddjjyyxx'", page)
        self.assertIn("isSettingsPanelOpen()", page)
        self.assertIn("window.pywebview.api.open_devtools()", page)
        self.assertIn("input, textarea, select, [contenteditable=\"true\"]", page)
        self.assertIn("classList.toggle('is-open')", page)
        self.assertIn("load-status status-neutral", page)
        self.assertIn("bar-critical", page)
        self.assertIn("stableProgressSequence", page)
        self.assertIn("renderProgressBar", page)
        self.assertIn("initializeProgressResizeObserver", page)
        self.assertIn("window.__progressTrackResizeObserver", page)
        self.assertIn("Math.round(entry.contentRect.width)", page)
        self.assertIn("matrix-progress", scss_source)
        self.assertIn("matrix-dot is-filled", page)
        self.assertIn("border: 1px solid rgba(var(--progress-rgb), 0.08)", scss_source)
        self.assertIn("background: transparent", scss_source)
        self.assertIn("rateLimitProgressMode", page)
        self.assertIn("changeRateLimitProgressMode('used')", page)
        self.assertIn('id="updateFrequency"', page)
        self.assertIn('id="closeAction"', page)
        self.assertIn('id="startupEnabled"', page)
        self.assertIn('id="updateProgress"', page)
        self.assertIn("window.applyUpdateState", page)
        self.assertIn('id="changelogModal"', page)
        self.assertIn('id="closeActionModal"', page)
        self.assertIn("openAnimatedModal", page)
        self.assertIn("closeAnimatedModal", page)
        self.assertIn("MODAL_ANIMATION_MS = 260", page)
        self.assertIn("options.update ? totalDots", page)
        self.assertIn("--update-progress-clip", page)
        self.assertIn("transition: clip-path 6s", scss_source)
        self.assertIn("will-change: clip-path", scss_source)
        self.assertIn("translate3d(0, 14px, 0)", scss_source)
        self.assertIn("prefers-reduced-motion", scss_source)
        self.assertIn("bar-unlimited", scss_source)
        self.assertIn("--progress-color: #a855f7", scss_source)
        self.assertNotIn("app.min.css", page)
        self.assertNotIn("tailwind", build_script.lower())
        self.assertIn("npm.cmd", build_script.lower())
        self.assertIn("run build:css", build_script.lower())
        self.assertIn("assets\\app.scss", build_script)
        self.assertIn("assets\\app.css", build_script)
        self.assertNotIn("fontScaleForParent", page)
        self.assertNotIn("data-font-scale", page)
        self.assertIn('id="micro1dRow"', page)
        self.assertIn('id="win5hCountdown"', page)
        self.assertIn("['5h', '1d', '7d'].forEach", page)
        self.assertIn("重置时间未知", page)
        self.assertIn("top: 33px", stylesheet)
        self.assertIn("document.getElementById('settingsHeader')?.addEventListener('mousedown', beginWindowDrag)", page)
        self.assertNotIn("fa-solid", page)
        self.assertNotIn("fa-regular", page)
        self.assertNotIn("size-roomy", page)
        self.assertNotIn("--ui-scale", page)
        self.assertNotIn("∞", page)

    def test_main_page_window_controls_use_lucide_icons(self):
        project_root = Path(__file__).parents[1]
        page = (project_root / app.MAIN_PAGE_NAME).read_text(encoding="utf-8")
        stylesheet = (project_root / "assets" / "app.css").read_text(encoding="utf-8")

        self.assertIn('data-lucide="minus"', page)
        self.assertIn('data-lucide="square"', page)
        self.assertIn('data-lucide="x"', page)
        self.assertIn('class="titlebar-icon"', page)
        self.assertIn("flex: 0 0 33px", stylesheet)
        self.assertIn("width: 46px", stylesheet)
        self.assertIn("font-size: 12px", stylesheet)
        self.assertIn("stroke-width: 1.5 !important", stylesheet)
        self.assertIn("setLucideIcon(icon, isMaximized ? 'copy' : 'square', 'titlebar-icon')", page)


class ControllerTests(unittest.TestCase):
    def test_refresh_all_attempts_every_key_and_reports_each_result(self):
        controller = app.AppController.__new__(app.AppController)
        controller.refresh_lock = __import__("threading").Lock()
        controller.store = SimpleNamespace(
            list_key_records=lambda: [{"id": "key-1"}, {"id": "key-2"}, {"id": "key-3"}]
        )

        with patch.object(controller, "_refresh_key", side_effect=[True, False, True]) as refresh_key:
            result = controller.refresh_all(push_ui=False)

        self.assertEqual(
            [call.args[0] for call in refresh_key.call_args_list],
            ["key-1", "key-2", "key-3"],
        )
        self.assertEqual(result, {"refreshed": ["key-1", "key-3"], "failed": ["key-2"]})

    def test_web_api_exposes_only_page_methods(self):
        api = app.WebApi(SimpleNamespace())
        public_names = {name for name in dir(api) if not name.startswith("_")}

        self.assertEqual(
            public_names,
            {
                "add_key",
                "check_for_updates",
                "complete_initialization",
                "delete_key",
                "download_update",
                "get_asset_status",
                "get_state",
                "initialize_assets",
                "native_drag",
                "open_devtools",
                "refresh_now",
                "report_startup",
                "resolve_close_action",
                "update_app_preferences",
                "update_refresh_intervals",
                "update_rate_limit_progress_mode",
                "update_thresholds",
                "window_action",
            },
        )
        self.assertNotIn("store", public_names)
        self.assertNotIn("window", public_names)

    def test_open_devtools_uses_native_ui_thread_and_enables_webview_setting(self):
        settings = SimpleNamespace(AreDevToolsEnabled=False)

        class FakeCoreWebView:
            Settings = settings

            def __init__(self):
                self.open_calls = 0

            def OpenDevToolsWindow(self):
                self.open_calls += 1

        class FakeNativeForm:
            InvokeRequired = True

            def __init__(self, core_webview):
                self.webview = SimpleNamespace(CoreWebView2=core_webview)
                self.begin_invoke_calls = 0

            def BeginInvoke(self, action):
                self.begin_invoke_calls += 1
                action()

        core_webview = FakeCoreWebView()
        native_form = FakeNativeForm(core_webview)
        controller = app.AppController.__new__(app.AppController)
        controller.window = SimpleNamespace(native=native_form)

        with patch.dict(sys.modules, {"System": SimpleNamespace(Action=lambda callback: callback)}):
            result = controller.open_devtools()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(native_form.begin_invoke_calls, 1)
        self.assertTrue(settings.AreDevToolsEnabled)
        self.assertEqual(core_webview.open_calls, 1)

    def test_get_state_uses_latest_cached_payload(self):
        controller = app.AppController.__new__(app.AppController)
        record = {"id": "key-1", "name": "cached", "last_error": None}
        payload = {
            "status": "active",
            "quota": {"limit": 200, "used": 50, "remaining": 150},
            "usage": {"today": {"cost": 3}, "total": {"cost": 50}},
        }
        controller.visible = True
        controller.next_refresh_at = 1000
        controller.foreground_interval = 60
        controller.background_interval = 300
        controller.update_state = {
            "status": "idle",
            "percent": 0,
            "message": "尚未检查更新",
        }
        controller.store = SimpleNamespace(
            list_key_records=lambda: [record],
            latest_payload=lambda _key_id: payload,
            get_thresholds=lambda: {"warn": 25, "danger": 10, "critical": 5},
            get_rate_limit_progress_mode=lambda: "used",
            get_update_frequency=lambda: "startup",
            get_close_action=lambda: "ask",
            get_secret=lambda _key_id: "test-secret-value",
            rates=lambda _key_id: {
                "speed10m": 0,
                "speed1h": 0,
                "avgMin": 0,
                "avgHour": 0,
                "avgDay": 0,
            },
            path=Path("cached.db"),
        )

        with patch("app.time.time", return_value=900):
            state = controller.get_state()

        self.assertEqual(state["keys"][0]["remainingQuota"], 150)
        self.assertEqual(state["refreshIntervals"], {"foreground": 60, "background": 300})
        self.assertEqual(state["rateLimitProgressMode"], "used")

    def test_maximized_title_drag_uses_async_restore_without_js_reentry(self):
        class FakeUser32:
            def __init__(self):
                self.zoomed = True
                self.messages = []

            def IsZoomed(self, _hwnd):
                return self.zoomed

            def GetCursorPos(self, pointer):
                pointer._obj.x = 640
                pointer._obj.y = 16
                return True

            def GetWindowRect(self, _hwnd, pointer):
                pointer._obj.left = 0
                pointer._obj.top = 0
                pointer._obj.right = 1920 if self.zoomed else 920
                pointer._obj.bottom = 1080 if self.zoomed else 680
                return True

            def ShowWindow(self, _hwnd, _command):
                self.zoomed = False
                return True

            def SetWindowPos(self, *_args):
                return True

            def ReleaseCapture(self):
                return True

            def PostMessageW(self, _hwnd, message, hit_test, _position):
                self.messages.append((message, hit_test))
                return True

        class FakeNativeForm:
            InvokeRequired = True
            Handle = SimpleNamespace(ToInt64=lambda: 123)

            def __init__(self):
                self.begin_invoke_calls = 0

            def BeginInvoke(self, action):
                self.begin_invoke_calls += 1
                action()

        controller = app.AppController.__new__(app.AppController)
        native_form = FakeNativeForm()
        controller.window = SimpleNamespace(native=native_form)
        controller.maximized = True
        controller.drag_restore_suppressed_until = 0.0
        fake_user32 = FakeUser32()

        with patch.object(app, "user32", fake_user32), patch.object(
            controller, "_set_window_corner"
        ), patch.object(controller, "_push_window_state") as push_state, patch.dict(
            sys.modules, {"System": SimpleNamespace(Action=lambda callback: callback)}
        ):
            result = controller.native_drag("move")

        self.assertTrue(result["ok"])
        self.assertFalse(result["maximized"])
        self.assertEqual(native_form.begin_invoke_calls, 1)
        self.assertEqual(fake_user32.messages, [(app.WM_NCLBUTTONDOWN, app.HTCAPTION)])
        self.assertGreater(controller.drag_restore_suppressed_until, 0)
        push_state.assert_not_called()

    def test_missing_rate_limits_are_normalized_as_unlimited(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_secret=lambda _key_id: "test-secret-value",
            rates=lambda _key_id: {
                "speed10m": 0,
                "speed1h": 0,
                "avgMin": 0,
                "avgHour": 0,
                "avgDay": 0,
            },
        )
        record = {"id": "key-1", "name": "test", "last_error": None}
        payload = {
            "status": "active",
            "isValid": True,
            "balance": 100,
            "usage": {"total": {"cost": 25}},
        }

        normalized = controller._normalize(record, payload)

        self.assertEqual(normalized["win5h"]["limit"], 0)
        self.assertEqual(normalized["win1d"]["limit"], 0)
        self.assertEqual(normalized["win7d"]["limit"], 0)
        self.assertEqual(normalized["remainingQuota"], 100)

    def test_rate_limit_remaining_never_becomes_negative_when_usage_exceeds_limit(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_secret=lambda _key_id: "test-secret-value",
            rates=lambda _key_id: {},
        )
        record = {"id": "key-1", "name": "test", "last_error": None}
        payload = {
            "status": "active",
            "rate_limits": [
                {
                    "window": "7d",
                    "limit": 67,
                    "used": 67.05675485,
                    "remaining": 0,
                }
            ],
        }

        normalized = controller._normalize(record, payload)

        self.assertEqual(normalized["win7d"]["limit"], 67)
        self.assertEqual(normalized["win7d"]["used"], 67.05675485)
        self.assertEqual(normalized["win7d"]["remaining"], 0)

    def test_missing_rate_limits_do_not_trigger_alerts(self):
        severity_updates = []
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_thresholds=lambda: {"warn": 25, "danger": 10, "critical": 5},
            alert_severity=lambda _key_id, _metric: 0,
            set_alert_severity=lambda key_id, metric, severity: severity_updates.append(
                (key_id, metric, severity)
            ),
        )
        payload = {
            "quota": {},
            "rate_limits": [
                {"window": "5h", "limit": 0, "remaining": 0},
                {"window": "1d"},
            ],
        }

        with patch.object(controller, "notify") as notify:
            controller._check_alerts("key-1", "test", payload)

        notify.assert_not_called()
        self.assertEqual(severity_updates, [])

    def test_recorded_high_load_notifies_only_on_escalation(self):
        severities = {}
        notifications = []
        interval = {"value": 7, "status": "recorded"}
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_thresholds=lambda: {"warn": 25, "danger": 10, "critical": 5},
            alert_severity=lambda key_id, metric: severities.get((key_id, metric), 0),
            set_alert_severity=lambda key_id, metric, severity: severities.__setitem__(
                (key_id, metric), severity
            ),
            rates=lambda _key_id: {"intervals": {"10m": interval}},
        )
        payload = {"rate_limits": [{"window": "5h", "limit": 27, "remaining": 27}]}
        controller.notify = lambda title, message: notifications.append((title, message))

        controller._check_alerts("key-1", "test", payload)
        controller._check_alerts("key-1", "test", payload)
        interval["value"] = 16
        controller._check_alerts("key-1", "test", payload)
        interval["value"] = 0.2
        controller._check_alerts("key-1", "test", payload)

        self.assertEqual(len(notifications), 2)
        self.assertIn("速率高负载", notifications[0][0])
        self.assertIn("速率极高负载", notifications[1][0])
        self.assertEqual(severities[("key-1", "10m 负载")], 0)

    def test_estimated_load_does_not_notify(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_thresholds=lambda: {"warn": 25, "danger": 10, "critical": 5},
            alert_severity=lambda _key_id, _metric: 0,
            set_alert_severity=lambda *_args: None,
            rates=lambda _key_id: {
                "intervals": {"10m": {"value": 1, "status": "estimated"}}
            },
        )
        payload = {"rate_limits": [{"window": "5h", "limit": 27, "remaining": 27}]}

        with patch.object(controller, "notify") as notify:
            controller._check_alerts("key-1", "test", payload)

        notify.assert_not_called()

    def test_quota_load_channel_can_trigger_notification(self):
        severities = {}
        notifications = []
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_thresholds=lambda: {"warn": 25, "danger": 10, "critical": 5},
            alert_severity=lambda key_id, metric: severities.get((key_id, metric), 0),
            set_alert_severity=lambda key_id, metric, severity: severities.__setitem__(
                (key_id, metric), severity
            ),
            rates=lambda _key_id: {
                "intervals": {"10m": {"value": 2.5, "status": "recorded"}}
            },
        )
        payload = {
            "quota": {"limit": 10, "remaining": 10},
            "rate_limits": [{"window": "5h", "limit": 1000, "remaining": 1000}],
        }
        controller.notify = lambda title, message: notifications.append((title, message))

        controller._check_alerts("key-1", "test", payload)

        self.assertEqual(len(notifications), 1)
        self.assertIn("额度高负载", notifications[0][0])
        self.assertIn("额度 25.00% / 速率 0.25%", notifications[0][1])


if __name__ == "__main__":
    unittest.main()
