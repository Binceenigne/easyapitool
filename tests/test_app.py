import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import app
import backend.client as backend_client
import backend.network_log as backend_network_log
import backend.platform as backend_platform
import backend.runtime as backend_runtime
import backend.controller_mixins.image_files as controller_image_files
import backend.controller_mixins.image_reasoning as controller_image_reasoning
import backend.controller_mixins.quota_state as controller_quota_state
import backend.controller_mixins.updates as controller_updates
import backend.controller_mixins.window_commands as controller_window_commands
import backend.controller_mixins.window_state as controller_window_state
import backend.controller_mixins.workers_window as controller_workers_window
import image_editor


PROJECT_ROOT = Path(__file__).parents[1]


def frontend_source() -> str:
    page = (PROJECT_ROOT / app.MAIN_PAGE_NAME).read_text(encoding="utf-8")
    scripts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "frontend" / "scripts" / "modules").glob("*.js"))
    )
    return f"{page}\n{scripts}"


def frontend_scss_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "frontend" / "styles" / "modules").glob("*.scss"))
    )


def frontend_stylesheet() -> str:
    return (PROJECT_ROOT / "frontend" / "styles" / "app.css").read_text(encoding="utf-8")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = app.Store(Path(self.temp.name) / "test.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_changelog_between_includes_only_uninstalled_versions(self):
        markdown = (
            "# 更新日志\n\n"
            "## 1.0.11 - 2026-07-23\n\n- 新功能\n\n"
            "## 1.0.10 - 2026-07-22\n\n- 修复问题\n\n"
            "## 1.0.9 - 2026-07-21\n\n- 旧版本\n"
        )

        result = app.changelog_between(markdown, "1.0.9", "1.0.11")

        self.assertIn("## 1.0.11", result)
        self.assertIn("## 1.0.10", result)
        self.assertNotIn("## 1.0.9", result)

    def test_changelog_for_update_uses_full_range_for_multiple_versions(self):
        markdown = (
            "## 1.0.11\n- 新版本\n\n"
            "## 1.0.10\n- 中间版本\n\n"
            "## 1.0.9\n- 当前版本\n"
        )

        result = app.changelog_for_update(markdown, "1.0.9", "1.0.11", "仅最新 Release")

        self.assertIn("中间版本", result)
        self.assertNotIn("当前版本", result)

    def test_release_notes_since_combines_every_uninstalled_release(self):
        releases = [
            {"tag_name": "v1.0.12", "body": "- 最新版本"},
            {"tag_name": "v1.0.11", "body": "- 中间版本"},
            {"tag_name": "v1.0.10", "body": "- 已安装版本"},
        ]

        result = app.release_notes_since(releases, "1.0.10")

        self.assertIn("## 1.0.12", result)
        self.assertIn("最新版本", result)
        self.assertIn("## 1.0.11", result)
        self.assertIn("中间版本", result)
        self.assertNotIn("已安装版本", result)

    def test_release_notes_since_filters_cumulative_release_body(self):
        releases = [
            {
                "tag_name": "v1.0.12",
                "body": (
                    "## 1.0.12\n- 最新版本\n\n"
                    "## 1.0.11\n- 中间版本\n\n"
                    "## 1.0.10\n- 已安装版本"
                ),
            }
        ]

        result = app.release_notes_since(releases, "1.0.10")

        self.assertIn("最新版本", result)
        self.assertIn("中间版本", result)
        self.assertNotIn("已安装版本", result)

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

    def test_reset_limit_alerts_preserves_load_alerts(self):
        key_id = self.store.add_key("alerts", "secret", "https://example.test/v1")
        self.store.set_alert_severity(key_id, "总额度", 3)
        self.store.set_alert_severity(key_id, "5h 限额", 2)
        self.store.set_alert_severity(key_id, "10m 负载", 1)

        self.store.reset_limit_alerts()

        self.assertEqual(self.store.alert_severity(key_id, "总额度"), 0)
        self.assertEqual(self.store.alert_severity(key_id, "5h 限额"), 0)
        self.assertEqual(self.store.alert_severity(key_id, "10m 负载"), 1)

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
        self.assertFalse(self.store.get_always_on_top())
        self.assertEqual(self.store.get_background_ui_mode(), "delayed")
        self.assertEqual(self.store.set_update_frequency("weekly"), "weekly")
        self.assertEqual(self.store.set_close_action("tray"), "tray")
        self.assertTrue(self.store.set_always_on_top(True))
        self.assertEqual(self.store.set_background_ui_mode("active"), "active")
        self.assertEqual(self.store.get_update_frequency(), "weekly")
        self.assertEqual(self.store.get_close_action(), "tray")
        self.assertTrue(self.store.get_always_on_top())
        self.assertEqual(self.store.get_background_ui_mode(), "active")
        self.assertEqual(self.store.set_update_frequency("invalid"), "startup")
        self.assertEqual(self.store.set_close_action("invalid"), "ask")
        self.assertEqual(self.store.set_background_ui_mode("invalid"), "delayed")

    def test_window_size_is_clamped_and_persisted(self):
        self.assertEqual(
            self.store.get_window_size(),
            {"width": app.DEFAULT_WINDOW_WIDTH, "height": app.DEFAULT_WINDOW_HEIGHT},
        )

        saved = self.store.set_window_size(1280.4, 840.6)

        self.assertEqual(saved, {"width": 1280, "height": 841})
        self.assertEqual(self.store.get_window_size(), saved)
        self.assertEqual(
            self.store.set_window_size(1, 99999),
            {"width": app.MIN_WINDOW_WIDTH, "height": app.MAX_WINDOW_HEIGHT},
        )

    def test_title_bar_mode_is_normalized_and_persisted(self):
        self.assertEqual(self.store.get_title_bar_mode(), "default")
        self.assertEqual(self.store.set_title_bar_mode("minimal"), "minimal")
        self.assertEqual(self.store.get_title_bar_mode(), "minimal")
        self.assertEqual(self.store.set_title_bar_mode("original"), "original")
        self.assertEqual(self.store.get_title_bar_mode(), "original")
        self.assertEqual(self.store.set_title_bar_mode("invalid"), "default")

    def test_ignored_update_version_is_normalized_and_persisted(self):
        self.assertEqual(self.store.get_ignored_update_version(), "")
        self.assertEqual(self.store.set_ignored_update_version("v1.2.3"), "1.2.3")
        self.assertEqual(self.store.get_ignored_update_version(), "1.2.3")

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

    @patch("app.protect_secret", side_effect=lambda value: value)
    @patch("app.unprotect_secret", side_effect=lambda value: value)
    def test_rates_return_twelve_ten_minute_buckets(self, _unprotect, _protect):
        key_id = self.store.add_key("test", "secret", "https://example.test/v1")
        start = datetime(2026, 7, 15, 10, 0, tzinfo=app.BUSINESS_TIMEZONE)
        for index in range(13):
            sampled_at = start + timedelta(minutes=index * 10)
            payload = {
                "usage": {
                    "today": {"cost": index * 0.25, "requests": index},
                    "total": {"cost": 100 + index * 0.25, "requests": index},
                }
            }
            with patch("app.time.time", return_value=sampled_at.timestamp()):
                self.store.save_snapshot(key_id, payload)

        with patch("app.time.time", return_value=(start + timedelta(hours=2, minutes=5)).timestamp()):
            rates = self.store.rates(key_id)

        self.assertEqual(len(rates["tenMinute2h"]), 12)
        self.assertTrue(all(item["status"] == "recorded" for item in rates["tenMinute2h"]))
        self.assertTrue(all(abs(item["cost"] - 0.25) < 1e-9 for item in rates["tenMinute2h"]))
        for item in rates["tenMinute2h"]:
            start_at = datetime.fromtimestamp(item["startTimestamp"] / 1000, app.BUSINESS_TIMEZONE)
            end_at = datetime.fromtimestamp(item["endTimestamp"] / 1000, app.BUSINESS_TIMEZONE)
            self.assertEqual(start_at.minute % 10, 0)
            self.assertEqual((end_at - start_at).total_seconds(), 600)


class UtilityTests(unittest.TestCase):
    def test_response_usage_metrics_estimates_gpt_5_6_short_and_long_context_costs(self):
        short = app.response_usage_metrics(
            {"usage": {"input_tokens": 1000, "output_tokens": 500}},
            "gpt-5.6-terra",
        )
        long = app.response_usage_metrics(
            {"usage": {"input_tokens": 272000, "output_tokens": 1000}},
            "gpt-5.6-terra",
        )

        self.assertAlmostEqual(short["costUsd"], 0.008)
        self.assertAlmostEqual(long["costUsd"], 1.106)
        self.assertEqual(short["costSource"], "estimate")
        self.assertEqual(short["estimatedCallCount"], 1)
        self.assertTrue(short["hasCost"])
        self.assertEqual(long["costSource"], "estimate")

    def test_response_usage_metrics_prefers_actual_response_cost_over_estimate(self):
        usage = app.response_usage_metrics(
            {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cost": {"total": 0.123456},
                }
            },
            "gpt-5.6-sol",
        )

        self.assertAlmostEqual(usage["costUsd"], 0.123456)
        self.assertEqual(usage["costSource"], "response")
        self.assertEqual(usage["estimatedCallCount"], 0)

    def test_gpt_image_2_usage_estimates_modal_tokens_and_output_fallback(self):
        token_priced = app.image_usage_metrics(
            {
                "input_tokens": 3000,
                "output_tokens": 2000,
                "input_tokens_details": {"text_tokens": 1000, "image_tokens": 2000},
            },
            "gpt-image-2",
            "medium",
            "1024x1024",
        )
        output_fallback = app.image_usage_metrics(
            {"input_tokens": 1000, "input_tokens_details": {"text_tokens": 1000}},
            "gpt-image-2",
            "high",
            "1536x1024",
            partial_images=2,
        )

        self.assertAlmostEqual(token_priced["costUsd"], 0.081)
        self.assertEqual(token_priced["totalTokens"], 5000)
        self.assertEqual(token_priced["costSource"], "estimate")
        self.assertAlmostEqual(output_fallback["costUsd"], 0.176)
        self.assertEqual(output_fallback["outputTokens"], 5700)

    def test_response_usage_metrics_preserves_actual_tokens_calls_and_cost(self):
        first = app.response_usage_metrics(
            {
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    "cost": {"total": 0.0042},
                }
            }
        )
        second = app.response_usage_metrics(
            {"usage": {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100}}
        )
        costed_second = app.response_usage_metrics(
            {
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "total_tokens": 100,
                    "cost": 0.002,
                }
            }
        )

        combined = app.merge_reasoning_usage(first, second)
        fully_costed = app.merge_reasoning_usage(first, costed_second)

        self.assertEqual(combined["inputTokens"], 200)
        self.assertEqual(combined["outputTokens"], 50)
        self.assertEqual(combined["totalTokens"], 250)
        self.assertEqual(combined["callCount"], 2)
        self.assertEqual(combined["costUsd"], 0)
        self.assertTrue(combined["hasTokenUsage"])
        self.assertEqual(combined["costedCallCount"], 1)
        self.assertEqual(combined["costSource"], "")
        self.assertFalse(combined["hasCost"])
        self.assertFalse(second["hasCost"])
        self.assertAlmostEqual(fully_costed["costUsd"], 0.0062)
        self.assertEqual(fully_costed["costedCallCount"], 2)
        self.assertEqual(fully_costed["costSource"], "response")
        self.assertTrue(fully_costed["hasCost"])

        estimated = app.response_usage_metrics(
            {"usage": {"input_tokens": 1000, "output_tokens": 500}},
            "gpt-5.6-luna",
        )
        mixed = app.merge_reasoning_usage(first, estimated)
        self.assertAlmostEqual(mixed["costUsd"], 0.005)
        self.assertEqual(mixed["costSource"], "mixed")
        self.assertEqual(mixed["estimatedCallCount"], 1)
        self.assertTrue(mixed["hasCost"])

    def test_continuation_plan_separates_operation_from_reference_selection(self):
        generated = app.parse_image_continuation_plan(
            {
                "operation": "generate",
                "selected_reference_indexes": [1, 0, 1],
                "rationale": "Keep the character identity while creating a new battle scene.",
            },
            2,
        )
        without_references = app.parse_image_continuation_plan(
            {
                "operation": "generate",
                "selected_reference_indexes": [],
                "rationale": "No prior visual is relevant.",
            },
            2,
        )

        self.assertEqual(generated["operation"], "generate")
        self.assertEqual(generated["selectedReferenceIndexes"], [1, 0])
        self.assertEqual(without_references["selectedReferenceIndexes"], [])
        with self.assertRaisesRegex(ValueError, "至少需要一张参考图"):
            app.parse_image_continuation_plan(
                {"operation": "edit", "selected_reference_indexes": []},
                2,
            )
        with self.assertRaisesRegex(ValueError, "超出范围"):
            app.parse_image_continuation_plan(
                {"operation": "generate", "selected_reference_indexes": [2]},
                2,
            )

    def test_continuation_plan_selects_stable_assets_and_describes_only_visible_images(self):
        plan = app.parse_image_continuation_plan(
            {
                "operation": "generate",
                "selected_asset_ids": ["asset-older", "asset-parent", "asset-older"],
                "descriptions": [
                    {
                        "asset_id": "asset-parent",
                        "description": "紫色披风角色站在城门前，保持银色肩甲。",
                    }
                ],
                "rationale": "沿用角色设定，延续到新的场景。",
            },
            ["asset-parent", "asset-older"],
            ["asset-parent"],
        )

        self.assertEqual(plan["operation"], "generate")
        self.assertEqual(plan["selectedAssetIds"], ["asset-older", "asset-parent"])
        self.assertEqual(
            plan["descriptions"],
            {"asset-parent": "紫色披风角色站在城门前，保持银色肩甲。"},
        )
        with self.assertRaisesRegex(ValueError, "实际读取"):
            app.parse_image_continuation_plan(
                {
                    "operation": "generate",
                    "selected_asset_ids": [],
                    "descriptions": [
                        {"asset_id": "asset-older", "description": "并未读取的旧图"}
                    ],
                },
                ["asset-parent", "asset-older"],
                ["asset-parent"],
            )

    def test_continuation_prompt_contains_full_auditable_history_without_local_paths(self):
        prompt = app.image_continuation_prompt(
            "让角色走进雨夜车站",
            {
                "history": [
                    {
                        "setId": "set-1",
                        "roundNumber": 1,
                        "userPrompt": "设计主角",
                        "reasoningSummary": "采用紫色披风和银色肩甲。",
                        "operation": "generate",
                        "inputAssetIds": [],
                        "outputAssetIds": ["asset-character"],
                    }
                ],
                "assets": [
                    {
                        "assetId": "asset-character",
                        "description": "紫色披风角色正面设定图",
                        "sourceSetIds": ["set-1"],
                        "sourceRoles": ["output"],
                        "path": "D:/private/session/character.png",
                    }
                ],
            },
            [
                {
                    "assetId": "asset-character",
                    "description": "紫色披风角色正面设定图",
                    "path": "D:/private/session/character.png",
                }
            ],
        )
        payload = json.loads(prompt)

        self.assertEqual(payload["currentRequest"], "让角色走进雨夜车站")
        self.assertEqual(payload["history"][0]["userPrompt"], "设计主角")
        self.assertEqual(payload["history"][0]["reasoningSummary"], "采用紫色披风和银色肩甲。")
        self.assertEqual(payload["visibleAssetIds"], ["asset-character"])
        self.assertEqual(payload["descriptionRequiredAssetIds"], [])
        self.assertNotIn("D:/private", prompt)

    def test_asset_description_parser_requires_every_read_image_once(self):
        parsed = app.parse_image_asset_descriptions(
            {
                "descriptions": [
                    {"asset_id": "asset-a", "description": "紫色披风角色"},
                    {"asset_id": "asset-b", "description": "银色城堡大厅"},
                ]
            },
            ["asset-a", "asset-b"],
        )

        self.assertEqual(parsed["asset-a"], "紫色披风角色")
        with self.assertRaisesRegex(ValueError, "覆盖本批全部图片"):
            app.parse_image_asset_descriptions(
                {"descriptions": [{"asset_id": "asset-a", "description": "角色"}]},
                ["asset-a", "asset-b"],
            )

    def test_instant_continuation_planner_is_hidden_minimal_and_does_not_rewrite_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            parent_image = Path(temp) / "parent.png"
            Image.new("RGB", (8, 8), "purple").save(parent_image)
            asset = app.image_asset_record(parent_image)
            context = {
                "history": [{"setId": "set-1", "userPrompt": "设计角色"}],
                "assets": [asset],
            }
            controller = app.AppController.__new__(app.AppController)
            captured = {}

            def stream_response(*args, **kwargs):
                captured["model"] = args[2]
                captured["instructions"] = args[3]
                captured["input"] = args[4]
                captured["kwargs"] = kwargs
                return json.dumps(
                    {
                        "operation": "generate",
                        "selected_asset_ids": [asset["assetId"]],
                        "descriptions": [
                            {
                                "asset_id": asset["assetId"],
                                "description": "紫色披风角色正面图",
                            }
                        ],
                        "rationale": "延续角色并创建新场景。",
                    },
                    ensure_ascii=False,
                )

            controller.client = SimpleNamespace(stream_response=stream_response)
            plan = controller._run_instant_image_continuation_planner(
                {"base_url": "https://example.test/v1"},
                "secret",
                "让她走进雨夜车站",
                context,
                [asset],
            )

        self.assertEqual(captured["model"], app.IMAGE_CONTINUATION_PLANNER_MODEL)
        self.assertEqual(captured["kwargs"]["reasoning_effort"], "minimal")
        self.assertNotIn("tools", captured["kwargs"])
        self.assertNotIn("on_delta", captured["kwargs"])
        self.assertIn("不润色", captured["instructions"])
        self.assertIn("让她走进雨夜车站", captured["input"][0]["content"][0]["text"])
        self.assertEqual(captured["input"][0]["content"][1]["type"], "input_image")
        self.assertEqual(plan["selectedAssetIds"], [asset["assetId"]])
        self.assertEqual(plan["descriptions"][asset["assetId"]], "紫色披风角色正面图")

    def test_window_frame_options_use_native_frame_only_for_original_mode(self):
        self.assertEqual(
            app.window_frame_options("default"),
            {"frameless": True, "easy_drag": False},
        )
        self.assertEqual(
            app.window_frame_options("minimal"),
            {"frameless": True, "easy_drag": False},
        )
        self.assertEqual(
            app.window_frame_options("original"),
            {"frameless": False, "easy_drag": True},
        )
        self.assertEqual(app.normalize_title_bar_mode("unknown"), "default")
        self.assertEqual(app.window_min_size("default"), (260, 120))
        self.assertEqual(app.window_min_size("minimal"), (220, 96))
        self.assertEqual(app.window_min_size("original"), (260, 120))
    def test_semantic_version_comparison(self):
        self.assertTrue(app.is_newer_version("v1.1.0", "1.0.9"))
        self.assertFalse(app.is_newer_version("1.0", "1.0.0"))
        self.assertFalse(app.is_newer_version("preview", "1.0.0"))
        self.assertEqual(app.release_version("imagen-v1.2.3"), "1.2.3")
        self.assertEqual(app.release_version("v1.2.3"), "")
        self.assertTrue(
            app.release_matches_channel(
                {"tag_name": "imagen-v1.2.3", "target_commitish": "imagen"}
            )
        )
        self.assertFalse(
            app.release_matches_channel(
                {"tag_name": "imagen-v1.2.3", "target_commitish": "main"}
            )
        )

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
            "mode": "unrestricted",
            "balance": 100,
            "usage": {"total": {"cost": 25}},
        }
        current = {
            "mode": "unrestricted",
            "balance": 95,
            "usage": {"total": {"cost": 30}},
        }

        names = app.annotate_limit_changes(current, previous, changed_at=1000)

        self.assertNotIn("quota", names)
        self.assertEqual(app.limit_definitions(current)["quota"], 0)

    def test_unrestricted_live_response_never_reports_limit_changes(self):
        previous = {
            "mode": "unrestricted",
            "balance": 110333.48824915,
            "remaining": 110333.48824915,
            "usage": {"total": {"cost": 3882.69894125}},
            "_limit_changes": {
                "quota": {"previous": 114219, "current": 114216, "changedAt": 999}
            },
        }
        current = {
            "mode": "unrestricted",
            "balance": 110331.23500065,
            "remaining": 110331.23500065,
            "usage": {"total": {"cost": 3884.12541625}},
        }

        names = app.annotate_limit_changes(current, previous, changed_at=1000)

        self.assertEqual(names, set())
        self.assertNotIn("_limit_changes", current)
        self.assertEqual(
            app.limit_definitions(current),
            {"quota": 0, "5h": 0, "1d": 0, "7d": 0},
        )

    def test_quota_limited_live_response_reports_real_limit_increase(self):
        previous = {
            "mode": "quota_limited",
            "quota": {"limit": 200, "remaining": 115.17, "used": 84.83},
            "rate_limits": [
                {"window": "5h", "limit": 27},
                {"window": "1d", "limit": 44},
                {"window": "7d", "limit": 68},
            ],
        }
        current = {
            "mode": "quota_limited",
            "quota": {"limit": 210, "remaining": 125.17, "used": 84.83},
            "rate_limits": previous["rate_limits"],
        }

        names = app.annotate_limit_changes(current, previous, changed_at=1000)

        self.assertEqual(names, {"quota"})
        self.assertEqual(
            current["_limit_changes"]["quota"],
            {"previous": 200, "current": 210, "changedAt": 1000},
        )

    def test_real_response_shape_notifies_only_quota_increase(self):
        previous = {
            "mode": "quota_limited",
            "quota": {"limit": 200, "remaining": 115.17470995, "used": 84.82529005},
            "rate_limits": [
                {"window": "5h", "limit": 27, "remaining": 26.04020585},
                {"window": "1d", "limit": 44, "remaining": 43.04020585},
                {"window": "7d", "limit": 68, "remaining": 0},
            ],
            "usage": {"total": {"cost": 97.99585925}},
        }
        current = json.loads(json.dumps(previous))
        current["quota"] = {
            "limit": 210,
            "remaining": 125.17470995,
            "used": 84.82529005,
        }
        changed_names = app.annotate_limit_changes(current, previous, changed_at=1000)
        notifications = []
        controller = app.AppController.__new__(app.AppController)
        controller.notify = lambda title, message, severity=0: notifications.append(
            (title, message, severity)
        )

        controller._notify_limit_changes("venus", current, changed_names)

        self.assertEqual(changed_names, {"quota"})
        self.assertEqual(
            notifications,
            [("venus · 限制调整", "您的总额度上限已从 200 USD 提高到 210 USD", 0)],
        )

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

    def test_client_streams_responses_output_text_deltas(self):
        class FakeHeaders:
            @staticmethod
            def get(_name):
                return "text/event-stream"

        class FakeResponse:
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter(
                    [
                        b"event: response.output_text.delta\n",
                        b'data: {"type":"response.output_text.delta","delta":"Analyze "}\n',
                        b"\n",
                        b'data: {"type":"response.output_text.delta","delta":"the scene"}\n',
                        b"\n",
                        b'data: {"type":"response.completed","response":{"output":[]}}\n',
                        b"\n",
                    ]
                )

        deltas = []
        completed = []
        with patch("app.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            text = app.EasyClinClient.stream_response(
                "https://example.test/v1",
                "secret",
                "gpt-test",
                "instructions",
                "input",
                on_delta=deltas.append,
                reasoning_effort="high",
                tools=[{"type": "web_search", "search_content_types": ["image", "text"]}],
                tool_choice="auto",
                include=["web_search_call.results"],
                max_tool_calls=3,
                parallel_tool_calls=True,
                on_completed=completed.append,
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://example.test/v1/responses")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(request.headers["User-agent"], f"{app.APP_NAME}/{app.APP_VERSION}")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(
            payload["tools"],
            [{"type": "web_search", "search_content_types": ["image", "text"]}],
        )
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertEqual(payload["include"], ["web_search_call.results"])
        self.assertEqual(payload["max_tool_calls"], 3)
        self.assertTrue(payload["parallel_tool_calls"])
        self.assertTrue(payload["stream"])
        self.assertEqual(text, "Analyze the scene")
        self.assertEqual(deltas, ["Analyze ", "the scene"])
        self.assertEqual(completed, [{"output": []}])

    def test_client_accepts_non_streaming_responses_payload(self):
        class FakeHeaders:
            @staticmethod
            def get(_name):
                return "application/json"

        class FakeResponse:
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return json.dumps(
                    {
                        "output": [
                            {"content": [{"type": "output_text", "text": "Polished prompt"}]}
                        ]
                    }
                ).encode("utf-8")

        with patch("app.urllib.request.urlopen", return_value=FakeResponse()):
            text = app.EasyClinClient.stream_response(
                "https://example.test/v1", "secret", "model", "instructions", "input"
            )

        self.assertEqual(text, "Polished prompt")

    def test_client_retries_generic_forbidden_response_once(self):
        class FakeHeaders:
            @staticmethod
            def get(_name):
                return "application/json"

        class FakeResponse:
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return json.dumps({"output_text": "Recovered"}).encode("utf-8")

        forbidden = app.urllib.error.HTTPError(
            "https://example.test/v1/responses",
            403,
            "Forbidden",
            {},
            app.io.BytesIO(b"Forbidden"),
        )
        with patch(
            "app.urllib.request.urlopen",
            side_effect=[forbidden, FakeResponse()],
        ) as urlopen:
            text = app.EasyClinClient.stream_response(
                "https://example.test/v1", "secret", "model", "instructions", "input"
            )

        self.assertEqual(text, "Recovered")
        self.assertEqual(urlopen.call_count, 2)

    def test_client_reports_actionable_error_after_repeated_generic_forbidden(self):
        forbidden_responses = [
            app.urllib.error.HTTPError(
                "https://example.test/v1/responses",
                403,
                "Forbidden",
                {},
                app.io.BytesIO(b"Forbidden"),
            )
            for _ in range(2)
        ]
        with patch(
            "app.urllib.request.urlopen",
            side_effect=forbidden_responses,
        ) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "思维服务暂时拒绝请求"):
                app.EasyClinClient.stream_response(
                    "https://example.test/v1", "secret", "model", "instructions", "input"
                )

        self.assertEqual(urlopen.call_count, 2)

    def test_client_does_not_retry_structured_forbidden_response(self):
        forbidden = app.urllib.error.HTTPError(
            "https://example.test/v1/responses",
            403,
            "Forbidden",
            {},
            app.io.BytesIO(b'{"error":{"message":"model access denied"}}'),
        )
        with patch("app.urllib.request.urlopen", side_effect=forbidden) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "HTTP 403: model access denied"):
                app.EasyClinClient.stream_response(
                    "https://example.test/v1", "secret", "model", "instructions", "input"
                )

        urlopen.assert_called_once()

    def test_network_error_log_redacts_secrets_and_request_content(self):
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "network-errors.jsonl"
            secret = "sk-sensitive-secret"
            error = RuntimeError(f"Bearer {secret} failed")
            with patch.object(backend_network_log, "NETWORK_ERROR_LOG_PATH", log_path), patch.object(
                backend_network_log.urllib.request,
                "getproxies",
                return_value={"https": "http://user:password@127.0.0.1:7897"},
            ):
                backend_network_log.log_network_error(
                    "test_network_error",
                    request_id="request-123",
                    method="POST",
                    url="https://example.test/v1/responses?api_key=hidden&mode=debug",
                    error=error,
                    status=403,
                    response_headers={"CF-Ray": "ray-123", "Authorization": secret},
                    response_body={"error": {"message": f"token {secret}"}},
                    details={
                        "model": "gpt-test",
                        "prompt": "private prompt",
                        "input": [{"image": "data:image/png;base64,AAAA"}],
                    },
                    secrets=(secret,),
                )

            raw_log = log_path.read_text(encoding="utf-8")
            record = json.loads(raw_log)

        self.assertNotIn(secret, raw_log)
        self.assertNotIn("private prompt", raw_log)
        self.assertNotIn("hidden", raw_log)
        self.assertNotIn("password", raw_log)
        self.assertEqual(record["endpoint"], "https://example.test/v1/responses?fields=api_key,mode")
        self.assertEqual(record["responseHeaders"], {"cf-ray": "ray-123"})
        self.assertEqual(record["details"]["prompt"], "[REDACTED]")
        self.assertEqual(record["proxy"]["https"], "http://127.0.0.1:7897")

    def test_client_logs_ssl_eof_during_responses_stream(self):
        class FailingResponse:
            headers = {"Content-Type": "text/event-stream", "CF-Ray": "ray-eof"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                raise app.ssl.SSLEOFError(8, "EOF occurred in violation of protocol")

        with patch("app.urllib.request.urlopen", return_value=FailingResponse()), patch.object(
            backend_client, "log_network_error"
        ) as log_error:
            with self.assertRaisesRegex(RuntimeError, "Responses 网络请求失败"):
                app.EasyClinClient.stream_response(
                    "https://example.test/v1",
                    "secret",
                    "gpt-test",
                    "instructions",
                    "input",
                    reasoning_effort="high",
                )

        log_error.assert_called_once()
        logged = log_error.call_args
        self.assertEqual(logged.args[0], "responses_stream_error")
        self.assertIsInstance(logged.kwargs["error"], app.ssl.SSLEOFError)
        self.assertEqual(logged.kwargs["details"]["model"], "gpt-test")
        self.assertEqual(logged.kwargs["details"]["reasoningEffort"], "high")

    def test_client_allows_tool_only_response_without_text(self):
        class FakeHeaders:
            @staticmethod
            def get(_name):
                return "application/json"

        class FakeResponse:
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return json.dumps(
                    {
                        "output": [
                            {
                                "type": "function_call",
                                "name": "search_visual_references",
                                "call_id": "call-1",
                                "arguments": '{"query":"Shanghai Tower"}',
                            }
                        ]
                    }
                ).encode("utf-8")

        completed = []
        with patch("app.urllib.request.urlopen", return_value=FakeResponse()):
            text = app.EasyClinClient.stream_response(
                "https://example.test/v1",
                "secret",
                "model",
                "instructions",
                "input",
                allow_empty_text=True,
                on_completed=completed.append,
            )

        self.assertEqual(text, "")
        self.assertEqual(completed[0]["output"][0]["name"], "search_visual_references")

    def test_bing_image_parser_reads_structured_metadata(self):
        parser = app.BingImageResultParser()
        parser.feed(
            '<a class="iusc image" m="{&quot;murl&quot;:&quot;https://images.example/a.jpg&quot;,'
            '&quot;turl&quot;:&quot;https://thumbs.example/a.jpg&quot;,'
            '&quot;purl&quot;:&quot;https://source.example/page&quot;,'
            '&quot;t&quot;:&quot;Shanghai Tower&quot;}"></a>'
        )

        self.assertEqual(len(parser.results), 1)
        self.assertEqual(parser.results[0]["murl"], "https://images.example/a.jpg")
        self.assertEqual(parser.results[0]["t"], "Shanghai Tower")

    def test_visual_search_combines_candidates_and_adds_previews(self):
        service = app.WebSearchService()
        image_buffer = __import__("io").BytesIO()
        Image.new("RGB", (64, 48), "blue").save(image_buffer, format="PNG")
        image_bytes = image_buffer.getvalue()
        bing_candidate = {
            "id": "webref-one",
            "title": "Bing result",
            "caption": "Exterior",
            "imageUrl": "https://images.example/shared.jpg",
            "thumbnailUrl": "https://thumbs.example/shared.jpg",
            "sourceUrl": "https://source.example/bing",
            "width": 1200,
            "height": 800,
            "provider": "Bing Images",
        }
        commons_duplicate = dict(
            bing_candidate,
            id="webref-duplicate",
            provider="Wikimedia Commons",
        )
        commons_unique = dict(
            bing_candidate,
            id="webref-two",
            imageUrl="https://images.example/unique.jpg",
            thumbnailUrl="https://thumbs.example/unique.jpg",
            provider="Wikimedia Commons",
        )

        with patch.object(service, "_bing_images", return_value=[bing_candidate]), patch.object(
            service,
            "_commons_images",
            return_value=[commons_duplicate, commons_unique],
        ), patch.object(service, "_public_image_bytes", return_value=image_bytes):
            result = service.search_visual_references("Shanghai Tower", max_results=6)

        self.assertEqual(result["query"], "Shanghai Tower")
        self.assertEqual([item["id"] for item in result["results"]], ["webref-one", "webref-two"])
        self.assertTrue(
            all(item["previewDataUrl"].startswith("data:image/jpeg;base64,") for item in result["results"])
        )
        self.assertTrue(all(isinstance(item["_previewBytes"], bytes) for item in result["results"]))

    def test_public_image_url_rejects_private_and_non_https_hosts(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            app.require_public_https_url("http://example.com/image.jpg")
        with self.assertRaisesRegex(ValueError, "非公开网络"):
            app.require_public_https_url("https://127.0.0.1/image.jpg")
        with patch("app.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.8", 443))]):
            with self.assertRaisesRegex(ValueError, "非公开网络"):
                app.require_public_https_url("https://images.example/image.jpg")

    def test_public_image_url_encodes_special_characters_and_rejects_injection(self):
        raw_url = (
            "https://images.example/gallery/img/automobiles/Hyundai/"
            "现代/2016 Hyundai%20Tuson (TL) 0N/d/1542094232 jpg"
            "?size=full image#原图 1"
        )
        with patch(
            "app.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
        ):
            normalized = app.require_public_https_url(raw_url)

        self.assertEqual(
            normalized,
            "https://images.example/gallery/img/automobiles/Hyundai/"
            "%E7%8E%B0%E4%BB%A3/2016%20Hyundai%20Tuson%20%28TL%29%200N/d/"
            "1542094232%20jpg?size=full%20image#%E5%8E%9F%E5%9B%BE%201",
        )
        self.assertNotIn(" ", app.urllib.request.Request(normalized).selector)
        unsafe_urls = (
            "https://images.example/image.jpg\r\nX-Injected: true",
            "\nhttps://images.example/image.jpg",
            "https://images.example/image.jpg\x00",
            "https://images.example/image.jpg\x7f",
        )
        for unsafe_url in unsafe_urls:
            with self.subTest(unsafe_url=repr(unsafe_url)):
                with self.assertRaisesRegex(ValueError, "控制字符"):
                    app.require_public_https_url(unsafe_url)

    def test_public_image_download_uses_encoded_selector_and_contains_invalid_url(self):
        requested_urls = []

        class FakeHeaders:
            @staticmethod
            def get(name):
                return "image/jpeg" if name == "Content-Type" else None

        class FakeResponse:
            headers = FakeHeaders()

            def __init__(self, url):
                self.url = url
                self.read_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return self.url

            def read(self, _size):
                if self.read_count:
                    return b""
                self.read_count += 1
                return b"jpeg-bytes"

        class FakeOpener:
            def open(self, request, timeout):
                self.timeout = timeout
                requested_urls.append(request.full_url)
                return FakeResponse(request.full_url)

        with patch(
            "app.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
        ), patch("app.urllib.request.build_opener", return_value=FakeOpener()):
            image_bytes = app.WebSearchService._public_image_bytes(
                "https://images.example/gallery/2016 Hyundai Tuson (TL).jpg",
                1024,
            )

        self.assertEqual(image_bytes, b"jpeg-bytes")
        self.assertEqual(
            requested_urls,
            ["https://images.example/gallery/2016%20Hyundai%20Tuson%20%28TL%29.jpg"],
        )

        class InvalidUrlOpener:
            @staticmethod
            def open(_request, timeout):
                raise app.http.client.InvalidURL("bad selector")

        with patch(
            "app.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
        ), patch("app.urllib.request.build_opener", return_value=InvalidUrlOpener()):
            with self.assertRaisesRegex(RuntimeError, "bad selector"):
                app.WebSearchService._public_image_bytes(
                    "https://images.example/image.jpg",
                    1024,
                )

    def test_client_posts_multiple_edit_images_as_multipart(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"data":[{"b64_json":"aW1hZ2U="}]}'

        class FakeOpener:
            def __init__(self):
                self.request = None
                self.timeout = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.png"
            second = Path(temp_dir) / "second.webp"
            first.write_bytes(b"first-image")
            second.write_bytes(b"second-image")
            opener = FakeOpener()

            with patch("image_editor.urllib.request.build_opener", return_value=opener) as build_opener:
                result = image_editor.ImageGenerationClient.edit_images(
                    "https://example.test/v1",
                    "secret-value",
                    (first, second),
                    {
                        "model": "gpt-image-2",
                        "prompt": "Combine both references",
                        "quality": "auto",
                    },
                )

        request = opener.request
        self.assertEqual(result["data"][0]["b64_json"], "aW1hZ2U=")
        self.assertEqual(request.full_url, "https://example.test/v1/images/edits")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer secret-value")
        self.assertEqual(opener.timeout, 600)
        self.assertEqual(build_opener.call_count, 1)
        content_type = request.headers["Content-type"]
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        body = request.data
        self.assertEqual(body.count(b'name="image[]"'), 2)
        self.assertIn(b'name="prompt"', body)
        self.assertIn(b"Combine both references", body)
        self.assertIn(b'filename="image-1.png"', body)
        self.assertIn(b'filename="image-2.webp"', body)


class StaticAssetCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.bundle = root / "bundle"
        self.data = root / "data"
        for relative in app.FRONTEND_RUNTIME_FILES:
            path = self.bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test-resource")
        self.cache = app.StaticAssetCache(self.data, self.bundle)

    def tearDown(self):
        self.temp.cleanup()

    def test_install_uses_validated_lucide_and_creates_ready_release(self):
        script = b"lucide-test-script"
        with patch.object(backend_platform, "LUCIDE_SHA256", app.sha256_bytes(script)):
            self.cache.expected_hashes["frontend/vendor/lucide/lucide.min.js"] = app.sha256_bytes(script)
            with patch.object(self.cache, "_download_lucide", return_value=script):
                self.cache.install()

        self.assertTrue(self.cache.is_ready())
        self.assertEqual(self.cache.status()["status"], "ready")
        self.assertTrue((self.cache.release_dir / "frontend/vendor/lucide/lucide.min.js").is_file())

    def test_replace_release_path_retries_transient_permission_errors(self):
        source = self.data / "staging"
        destination = self.data / "release"
        source.mkdir(parents=True)

        real_replace = app.os.replace
        attempts = 0

        def replace_with_transient_lock(source_path, destination_path):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError(5, "Access is denied")
            real_replace(source_path, destination_path)

        with patch.object(backend_platform.os, "replace", side_effect=replace_with_transient_lock), patch.object(
            backend_platform.time, "sleep"
        ) as sleep:
            self.cache._replace_release_path(source, destination)

        self.assertEqual(attempts, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.08, 0.16])
        self.assertTrue(destination.is_dir())

    def test_corrupted_lucide_invalidates_release(self):
        script = b"lucide-test-script"
        digest = app.sha256_bytes(script)
        with patch.object(backend_platform, "LUCIDE_SHA256", digest):
            self.cache.expected_hashes["frontend/vendor/lucide/lucide.min.js"] = digest
            with patch.object(self.cache, "_download_lucide", return_value=script):
                self.cache.install()

        (self.cache.release_dir / "frontend/vendor/lucide/lucide.min.js").write_bytes(b"damaged")

        self.assertFalse(self.cache.is_ready())

    def test_primary_mirror_failure_falls_back_to_archive(self):
        script = b"lucide-test-script"
        digest = app.sha256_bytes(script)
        with patch.object(backend_platform, "LUCIDE_SHA256", digest):
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
        page = frontend_source()
        scss_source = frontend_scss_source()
        stylesheet = frontend_stylesheet()
        build_script = (project_root / "build.ps1").read_text(encoding="utf-8")

        self.assertIn("iconMarkup('infinity'", page)
        self.assertIn("[data-lucide]", page)
        self.assertIn("selectMostConstrainedWindow", page)
        self.assertIn('<link rel="stylesheet" href="styles/app.css?v=34">', page)
        self.assertIn("container-type: size", stylesheet)
        self.assertIn("cqi", stylesheet)
        self.assertIn("renderUsageTrend", page)
        self.assertIn('id="trend1hButton"', page)
        self.assertIn('id="trend10mButton"', page)
        self.assertIn("rates?.tenMinute2h", page)
        self.assertIn("openModelModal", page)
        self.assertIn('id="workspaceModeButton"', page)
        self.assertNotIn('id="imageEditModeButton"', page)
        self.assertIn('<div id="widget-root" class="image-edit-mode">', page)
        self.assertIn('id="imageEditWorkspace"', page)
        self.assertIn('id="imageEditPrompt"', page)
        self.assertIn('id="imageEditPrompt" rows="7"', page)
        self.assertIn('id="expandImagePromptButton" hidden', page)
        self.assertIn('id="imagePromptModal"', page)
        self.assertIn('id="imagePromptModalTextarea"', page)
        self.assertIn("function resizeImagePrompt()", page)
        self.assertIn("expandThreshold: Math.max(minimum, Math.floor(maximum * 0.75))", page)
        self.assertIn("contentHeight > metrics.maximum + 1", page)
        self.assertIn("appMain.append(modal)", page)
        self.assertNotIn('class="image-prompt-back-button"', page)
        self.assertNotIn('支持粘贴或拖入参考图片</p>', page)
        self.assertIn('title="关闭提示词编辑器"', page)
        self.assertNotIn('查看过程图', page)
        self.assertIn('<em>生成中</em>', page)
        self.assertIn("document.createElement(completed ? 'button' : 'div')", page)
        self.assertNotIn("card.disabled = true;", page)
        self.assertIn('id="imageSetDeleteModal"', page)
        self.assertIn('id="imageGenerationStopModal"', page)
        self.assertIn('role="alertdialog"', page)
        self.assertIn("activeRequests: new Map()", page)
        self.assertIn("cancel_image_generation(set.requestId || set.setId)", page)
        self.assertIn("requestStopImageGeneration(set.setId)", page)
        self.assertIn("stopButton.className = 'is-stop'", page)
        self.assertIn("set.justCompleted = set.justCompleted === true", page)
        self.assertIn("const expanded = set.expanded === true", page)
        self.assertIn("您的任务已完成：", page)
        self.assertNotIn("image-reasoning-stop", page)
        self.assertNotIn("image-generation-item-stop", page)
        self.assertNotIn("window.confirm", page)
        self.assertIn("copy_generated_image", page)
        self.assertIn("function copyGeneratedImage(result)", page)
        self.assertIn("card.addEventListener('contextmenu'", page)
        self.assertIn("canvas.addEventListener('contextmenu'", page)
        self.assertIn("document.getElementById('editImageSelection').hidden = active", page)
        self.assertIn("? '描述本轮要修改或继续创作的内容'", page)
        self.assertIn("const files = session ? [] : window.imageEditState.files", page)
        self.assertIn("continuation: Boolean(session)", page)
        self.assertNotIn("function generatedResultReference", page)
        self.assertNotIn("session.references", page)
        self.assertNotIn("references: generatedResultReferences(results)", page)
        self.assertNotIn("上一轮参考图不可用", page)
        self.assertIn("operation: metadata.operation || 'generate'", page)
        self.assertIn("event.type === 'react_continuation_planned'", page)
        self.assertIn("set.selectedAssetIds = [...event.selectedAssetIds]", page)
        self.assertIn("#appMain > #imagePromptModal", scss_source)
        self.assertIn("document.addEventListener('paste'", page)
        self.assertIn("import_reference_image", page)
        self.assertIn("event.dataTransfer?.files", page)
        self.assertIn('id="editImageList"', page)
        self.assertIn("list.append(addTile)", page)
        self.assertIn("renderEditImageList(true)", page)
        self.assertIn("list.scrollLeft = scrollToEnd", page)
        self.assertIn('id="editImageScrollbar"', page)
        self.assertIn("function initializeEditImageScrollbar()", page)
        self.assertIn("scrollbar-width: none", scss_source)
        self.assertIn(".image-edit-scrollbar", scss_source)
        self.assertIn("image-edit-file:is(:hover, :focus-within)", scss_source)
        self.assertIn("resize: none", scss_source)
        self.assertNotIn('id="imageEditStream"', page)
        self.assertNotIn('id="imageEditPartialImages"', page)
        self.assertNotIn("partialImagesReceived", page)
        self.assertNotIn("image-edit-toggle-field", scss_source)
        self.assertIn('id="imageEditQuality" type="hidden" value="auto"', page)
        self.assertIn('id="imageEditOutputPreset" type="hidden" value="lossless"', page)
        self.assertIn('data-preset="large"', page)
        self.assertIn('data-preset="medium"', page)
        self.assertIn('data-preset="small"', page)
        self.assertNotIn('id="imageEditBackground"', page)
        self.assertNotIn('id="imageEditModeration"', page)
        self.assertIn('id="imageGenerationCountButtons"', page)
        self.assertIn('data-count="9"', page)
        self.assertNotIn('id="imageResolutionTierButtons"', page)
        self.assertNotIn('data-tier="2k"', page)
        self.assertIn('id="imageQualityButtons"', page)
        self.assertIn('data-quality="high"', page)
        self.assertIn('id="imageOutputPresetButtons"', page)
        self.assertIn('data-preset="small"', page)
        output_controls = page[page.index('id="imageOutputPresetButtons"'):]
        self.assertLess(output_controls.index('data-preset="small"'), output_controls.index('data-preset="medium"'))
        self.assertLess(output_controls.index('data-preset="medium"'), output_controls.index('data-preset="large"'))
        self.assertLess(output_controls.index('data-preset="large"'), output_controls.index('data-preset="lossless"'))
        self.assertIn('id="imageGenerationQualitySummary" data-quality="auto"', page)
        self.assertIn('id="imageGenerationOutputSummary" data-preset="lossless"', page)
        self.assertIn('#imageGenerationQualitySummary[data-quality="auto"]', scss_source)
        self.assertIn('#imageGenerationQualitySummary[data-quality="high"]', scss_source)
        self.assertIn('#imageOutputPresetButtons button[data-preset="lossless"].is-active', scss_source)
        self.assertIn('id="imageAspectRatioButtons"', page)
        self.assertIn('data-ratio="21:9"', page)
        self.assertIn("'16:9': '1280x720'", page)
        self.assertIn('id="imageGenerationSets"', page)
        self.assertIn("window.applyImageGenerationEvent", page)
        self.assertIn("enterImageEditSession", page)
        self.assertIn("exitImageEditSession", page)
        self.assertIn("deleteImageGenerationSet", page)
        self.assertIn("loadImageGenerationHistory", page)
        self.assertIn("delete_image_set", page)
        self.assertIn("list_image_sets", page)
        self.assertNotIn("appendGeneratedReferences", page)
        self.assertNotIn("useViewerImageAsReference", page)
        self.assertIn('id="imageResultModal"', page)
        self.assertIn('id="imageResultModalCanvas"', page)
        self.assertIn("initializeImageViewerInteractions", page)
        self.assertIn("setImageViewerScale", page)
        self.assertIn("load_generated_image", page)
        self.assertIn("#appMain > #imageResultModal", scss_source)
        self.assertIn("appMain.append(modal)", page)
        self.assertNotIn('id="imageEditCompression"', page)
        self.assertNotIn('id="imageEditFormat"', page)
        self.assertIn("choose_edit_images", page)
        self.assertIn("generate_image", page)
        self.assertIn("save_edited_image", page)
        self.assertNotIn(".image-edit-mode #manualRefreshButton", scss_source)
        self.assertIn(
            'name="image[]"',
            (project_root / "backend" / "image_editor.py").read_text(encoding="utf-8"),
        )
        self.assertIn('id="keyToolbar"', page)
        self.assertIn('id="keySwitcherButton"', page)
        self.assertIn('id="keySwitcherMenu" role="listbox"', page)
        self.assertNotIn('<select id="keySelector"', page)
        self.assertIn("#keySwitcherButton", stylesheet)
        self.assertIn("#keySwitcherMenu", stylesheet)
        self.assertIn("handleKeySwitcherOptionKeydown", page)
        self.assertIn("keySwitcherStatusClass", page)
        self.assertIn(".key-switcher-status.is-error", stylesheet)
        self.assertIn("set_window_background(window.__pendingNativeTheme)", page)
        self.assertIn("set_window_background(normalizedMode)", page)
        self.assertIn("syncVisibleBackendState", page)
        self.assertIn("setInterval(syncVisibleBackendState, 5000)", page)
        self.assertIn('id="manualRefreshButton"', page)
        self.assertNotIn('id="manualRefreshButton" onclick=', page)
        self.assertIn('id="manualRefreshCountdown"', page)
        self.assertIn("setManualRefreshButtonState('refreshing'", page)
        self.assertIn("startManualRefreshCooldown", page)
        self.assertIn("manualRefreshCooldownDeadline", page)
        self.assertIn("MANUAL_REFRESH_LOG_PREFIX", page)
        self.assertIn("bridge-call-started", page)
        self.assertIn("bridge-call-resolved", page)
        self.assertIn("backendDebug", page)
        self.assertIn("getManualRefreshDebugLog", page)
        self.assertIn("clearManualRefreshDebugLog", page)
        self.assertIn("manualRefreshPendingTraceId", page)
        self.assertIn("button-click", page)
        self.assertIn("if (event.button !== 0) return;", page)
        self.assertIn("RELIABLE_BUTTON_PRESS_TIMEOUT_MS", page)
        self.assertIn("dispatchReliableButtonFallback", page)
        self.assertIn("finishReliableButtonPress", page)
        self.assertIn("document.addEventListener('mouseup'", page)
        self.assertIn("press.button.click();", page)
        self.assertNotIn("'pointerup-missing'", page)
        self.assertIn("late-click-suppressed", page)
        self.assertIn("getReliableButtonDebugLog", page)
        self.assertIn("manualRefreshSpin", stylesheet)
        self.assertIn("manualRefreshSuccess", stylesheet)
        self.assertIn("manualRefreshError", stylesheet)
        self.assertIn("#manualRefreshButton.is-refresh-cooldown [data-lucide]", stylesheet)
        self.assertIn("#manualRefreshCountdown[hidden]", stylesheet)
        self.assertIn("#keyToolbar {\n  z-index: 5;", stylesheet)
        self.assertIn("#settingsPanel {\n  position: absolute;", stylesheet)
        self.assertIn("  z-index: 40;", stylesheet)
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
        self.assertIn('id="titleBarDefault"', page)
        self.assertIn('id="titleBarMinimal"', page)
        self.assertIn('id="titleBarOriginal"', page)
        self.assertIn('id="titleBarRestartRow"', page)
        self.assertIn("changeTitleBarMode", page)
        self.assertIn("restartApp", page)
        self.assertIn("#widget-root.titlebar-minimal #windowTitleBar", stylesheet)
        self.assertIn("height: 24px", stylesheet)
        self.assertIn("--fixed-titlebar-height: 24px", stylesheet)
        self.assertIn("#widget-root.titlebar-minimal #settingsPanel", stylesheet)
        self.assertIn("top: 0", stylesheet)
        self.assertIn("#widget-root.titlebar-original #windowTitleBar", stylesheet)
        self.assertIn("#widget-root.titlebar-original .native-resize-handle", stylesheet)
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
        self.assertNotIn('id="updateFrequency"', page)
        self.assertIn('id="closeAction"', page)
        self.assertIn('id="startupEnabled"', page)
        self.assertIn('id="updateProgress"', page)
        self.assertIn("window.applyUpdateState", page)
        self.assertIn('id="updateModal"', page)
        self.assertIn('id="downloadUpdateButton"', page)
        self.assertIn('id="declineUpdateButton"', page)
        self.assertIn('id="restartLaterButton"', page)
        self.assertIn('id="restartNowButton"', page)
        self.assertIn("deferUpdateRestart", page)
        self.assertIn("['downloading', 'ready'].includes(window.appState.update?.status)", page)
        self.assertIn("stripChecksumNotes", page)
        self.assertIn('id="ignoreUpdateButton"', page)
        self.assertIn("ignoreCurrentUpdate", page)
        self.assertIn('id="closeActionModal"', page)
        self.assertIn("openAnimatedModal", page)
        self.assertIn("closeAnimatedModal", page)
        self.assertIn("MODAL_ANIMATION_MS = 260", page)
        self.assertIn("if (barElement.id === 'updateProgress') return 4", page)
        self.assertIn("const filledDots = Math.round(totalDots * fill / 100)", page)
        self.assertNotIn("globalFilledIndexes", page)
        self.assertIn("const blockFill = Math.max(0, Math.min(dotsPerBlock, filledDots - consumed))", page)
        self.assertIn("active: busy", page)
        self.assertIn(".update-progress.is-active", scss_source)
        self.assertIn("@keyframes updateProgressScan", scss_source)
        self.assertIn("status: 'downloading', percent: 0, message: '正在连接下载源'", page)
        self.assertIn(".update-progress-track { position: relative; display: flex !important; align-items: center; width: 100%; height: 17px", scss_source)
        self.assertIn("opacity: 0", scss_source)
        self.assertIn("renderSimpleMarkdown", page)
        self.assertIn('value="50"', page)
        self.assertIn('value="25"', page)
        self.assertIn('value="10"', page)
        self.assertIn("translate3d(0, 14px, 0)", scss_source)
        self.assertIn("prefers-reduced-motion", scss_source)
        self.assertIn("bar-unlimited", scss_source)
        self.assertIn("--progress-color: #a855f7", scss_source)
        self.assertNotIn("app.min.css", page)
        self.assertNotIn("tailwind", build_script.lower())
        self.assertNotIn('<script defer src="../vendor/lucide/lucide.min.js"></script>', page)
        self.assertIn(
            '<script src="vendor/lucide/lucide.min.js"></script>\n'
            '    <script src="scripts/modules/00-core-ui.js?v=1"></script>',
            page,
        )
        self.assertIn("window.addEventListener('load', () => {\n            renderLucideIcons();", page)
        self.assertIn("npm.cmd", build_script.lower())
        self.assertIn("run build:css", build_script.lower())
        self.assertIn("frontend\\styles\\app.scss", build_script)
        self.assertIn("frontend\\styles\\app.css", build_script)
        self.assertNotIn("fontScaleForParent", page)
        self.assertNotIn("data-font-scale", page)
        self.assertIn('id="micro1dRow"', page)
        self.assertIn('id="win5hCountdown"', page)
        self.assertIn("['5h', '1d', '7d'].forEach", page)
        self.assertIn("重置时间未知", page)
        self.assertIn("--fixed-titlebar-height: 33px", stylesheet)
        self.assertIn('id="pageZoomLayer"', page)
        self.assertIn("--page-zoom: 1", stylesheet)
        self.assertIn('id="pageZoomViewport"', page)
        self.assertIn("width: calc(100% / var(--page-zoom));", stylesheet)
        self.assertIn("height: calc(100% / var(--page-zoom));", stylesheet)
        self.assertIn("overflow: visible;", stylesheet)
        self.assertIn("transform: scale(var(--page-zoom));", stylesheet)
        self.assertIn("transform-origin: top left;", stylesheet)
        self.assertNotIn("zoom: var(--page-zoom)", stylesheet)
        self.assertIn("zoomViewport.clientWidth / pageZoom", page)
        self.assertIn("zoomViewport.clientHeight / pageZoom", page)
        self.assertIn(
            "overflow-x: hidden;\n  overflow-y: auto;\n  background: var(--bg);",
            stylesheet,
        )
        self.assertIn("const PAGE_ZOOM_STORAGE_KEY = 'api-tools-page-zoom'", page)
        self.assertIn("const DEFAULT_PAGE_ZOOM = 1", page)
        self.assertIn("const PAGE_ZOOM_STORAGE_VERSION = '2'", page)
        self.assertIn("handlePageZoomShortcut(event)", page)
        self.assertIn("localStorage.setItem(PAGE_ZOOM_STORAGE_KEY", page)
        self.assertIn("restorePageZoom()", page)
        self.assertIn("#windowTitleBar {\n  z-index: auto", stylesheet)
        self.assertIn("document.getElementById('settingsHeader')?.addEventListener('mousedown', beginWindowDrag)", page)
        self.assertNotIn("fa-solid", page)
        self.assertNotIn("fa-regular", page)
        self.assertNotIn("size-roomy", page)
        self.assertNotIn("--ui-scale", page)
        self.assertNotIn("∞", page)

    def test_image_set_batch_mode_uses_item_clicks_without_checkboxes(self):
        page = frontend_source()
        stylesheet = frontend_stylesheet()

        self.assertIn('id="toggleImageSetBatchModeButton"', page)
        self.assertIn('onclick="toggleImageSetBatchMode()"', page)
        self.assertIn('id="exportSelectedImageSetsButton" onclick="exportSelectedImageSets()" hidden disabled', page)
        self.assertIn('id="deleteSelectedImageSetsButton" onclick="deleteSelectedImageSets()" hidden disabled', page)
        self.assertIn('batchSelectionMode: false', page)
        self.assertIn('function toggleImageSetBatchMode()', page)
        self.assertIn("section.addEventListener('click', event => {", page)
        self.assertIn("section.addEventListener('keydown', event => {", page)
        self.assertIn("section.className = `image-generation-set is-${set.status}", page)
        self.assertIn("' is-batch-selected'", page)
        self.assertNotIn('image-generation-set-checkbox', page)
        self.assertNotIn('.image-generation-set-checkbox', stylesheet)
        self.assertIn('.image-generation-set.is-batch-selected {', stylesheet)
        self.assertIn('background: color-mix(in srgb, #8b5cf6 9%, var(--bg));', stylesheet)

    def test_image_prompt_assistance_controls_and_streaming_summary(self):
        self.maxDiff = 600
        page = frontend_source()
        stylesheet = frontend_stylesheet()

        self.assertIn('id="polishImagePromptButton"', page)
        self.assertIn('onclick="polishImagePrompt()"', page)
        self.assertIn('<form id="imageEditForm" novalidate onsubmit="submitImageEdit(event)">', page)
        self.assertNotIn('id="imageEditPrompt" rows="7" maxlength="32000" required', page)
        self.assertIn("promptInput.focus();\n                return showToast('请输入生图提示词', 'error')", page)
        self.assertIn('id="imageReasoningControl"', page)
        self.assertIn('id="imageReasoningMenu"', page)
        self.assertIn('id="reasoningSliderTrack"', page)
        self.assertIn('#imageReasoningMenuButton[aria-expanded=true] [data-lucide]', stylesheet)
        self.assertIn('#imageReasoningMenuButton[aria-expanded=true] svg', stylesheet)
        self.assertIn('transform: rotate(180deg)', stylesheet)
        self.assertIn('role="slider"', page)
        self.assertIn('aria-valuemax="5"', page)
        for mode in ("instant", "flash", "medium", "high", "extra", "max"):
            self.assertIn(f'data-layer-mode="{mode}"', page)
        self.assertIn("const IMAGE_REASONING_MODES = ['instant', 'flash', 'medium', 'high', 'extra', 'max']", page)
        self.assertIn("function setImageReasoningModeFromPointer(event)", page)
        self.assertIn("function initializeImageReasoningSlider()", page)
        self.assertIn("sliderTrack.addEventListener('keydown'", page)
        self.assertIn("if (window.imageEditState.busy) return;", page)
        self.assertIn("window.clearTimeout(imageReasoningGradientTimer);\n            if (nextGradient !== activeImageReasoningGradient)", page)
        self.assertIn("createImageReasoningColorPulse(window.imageEditState.reasoningMode)", page)
        self.assertIn("reasoningMode: window.imageEditState.reasoningMode", page)
        self.assertIn("event.type === 'prompt_polish_delta'", page)
        self.assertIn("event.type === 'react_summary_delta'", page)
        self.assertIn("event.type === 'react_tool_started'", page)
        self.assertIn("event.type === 'react_tool_completed'", page)
        self.assertIn("event.type === 'react_tool_failed'", page)
        self.assertIn("event.type === 'react_visual_results'", page)
        self.assertIn("event.type === 'react_visual_selected'", page)
        self.assertIn("function reasoningPanelTitle(set, activeTurn, activeTool)", page)
        self.assertIn("const mode = normalizeImageReasoningMode(set.reasoningMode)", page)
        self.assertIn("? '方案已确定'", page)
        self.assertIn("? '正在确认方案'", page)
        self.assertIn("return `${modeLabel}：${statusLabel}`", page)
        self.assertIn("const title = reasoningPanelTitle(set, activeTurn, activeTool)", page)
        self.assertIn('id="imageWebSearchEnabled"', page)
        self.assertIn("setImageWebSearchEnabled(this.checked)", page)
        self.assertIn("webSearchEnabled: window.imageEditState.webSearchEnabled", page)
        self.assertIn("IMAGE_GENERATION_PREFERENCES_KEY", page)
        self.assertIn("restoreImageGenerationPreferences()", page)
        self.assertIn("function toggleWebReferences(setId)", page)
        self.assertIn("set.webReferencesExpanded", page)
        self.assertIn("image-reasoning-web-references", page)
        self.assertIn("查看采用的网络参考图", page)
        self.assertIn("网络参考 ${set.webReferenceCount}", page)
        self.assertIn("function toggleFinalPrompt(setId)", page)
        self.assertIn("set.finalPromptExpanded = !set.finalPromptExpanded", page)
        self.assertIn("if (set.finalPromptExpanded) set.webReferencesExpanded = false", page)
        self.assertIn("if (set.webReferencesExpanded) set.finalPromptExpanded = false", page)
        self.assertNotIn("const finalPrompt = document.createElement('details')", page)
        self.assertIn("function queueReasoningTurnRender(setId, turnNumber)", page)
        self.assertIn("reasoningTurnRenderFrame = requestAnimationFrame", page)
        self.assertIn("function reasoningTurnDisplayText(turn)", page)
        self.assertIn("if (turn?.status === 'running') return '正在形成这一轮的判断…'", page)
        self.assertIn("return '本轮判断已完成'", page)
        self.assertIn("text.textContent = reasoningTurnDisplayText(turn)", page)
        self.assertIn("turnText.textContent = reasoningTurnDisplayText(turn)", page)
        self.assertIn("data-reasoning-turn-text", page)
        self.assertIn("event.type === 'react_turn_started'", page)
        self.assertIn("event.type === 'react_turn_delta'", page)
        self.assertIn("event.type === 'react_turn_completed'", page)
        self.assertIn("function toggleReasoningPanel(setId)", page)
        self.assertIn("function toggleReasoningContent(setId)", page)
        self.assertIn("function updateReasoningTimers()", page)
        self.assertIn("totalReasoningElapsed(set)", page)
        self.assertIn("reasoningDurationMs", page)
        self.assertIn("reasoningUsage: normalizeReasoningUsage(metadata.reasoningUsage)", page)
        self.assertIn("function normalizeReasoningUsage(value)", page)
        self.assertIn("function formatReasoningUsage(set)", page)
        self.assertIn("function reasoningUsageTitle(set)", page)
        self.assertIn("${tokens} token · ${calls} 次调用 · ${cost}", page)
        self.assertIn("['response', 'estimate', 'mixed'].includes(costSource)", page)
        self.assertIn("costedCallCount === callCount", page)
        self.assertIn("官方价目估算", page)
        self.assertIn("未使用账户总额差值", page)
        self.assertIn("set.reasoningUsage = normalizeReasoningUsage(event.reasoningUsage || set.reasoningUsage)", page)
        self.assertIn("class=\"image-reasoning-usage\"", page)
        self.assertIn("persistedDuration && set.reasoningStatus !== 'running'", page)
        self.assertIn("reasoningToolLabel(activeTool)", page)
        self.assertIn("flash: 'Flash'", page)
        self.assertIn("instant: '直接快速获得结果'", page)
        self.assertIn("flash: '快速思考，优化生成质量'", page)
        self.assertIn("medium: '增强推理，丰富画面细节'", page)
        self.assertIn("high: '深入编排方案，强化视觉表现'", page)
        self.assertIn("extra: 'Extra'", page)
        self.assertIn("extra: '延长思维链，持续推演与优化'", page)
        self.assertIn("max: '使用顶级模型持续反思，获得最优结果'", page)
        for mode_label in ("Instant", "Flash", "Medium", "High", "Extra", "Max"):
            self.assertNotIn(f"{mode_label} 模式：", page)
        self.assertIn("high: ['#5865f2', '#6478f5', '#7185f7', '#8170f5']", page)
        self.assertIn("#b7c4ff 27%", stylesheet)
        self.assertIn("#c9c7ff 67%", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.image-reasoning-control\[data-mode=high\]\s*\{[^}]*background: #5865f2;",
        )
        self.assertRegex(stylesheet, r"\.slider-desc\s*\{[^}]*white-space: nowrap;")
        self.assertIn(".image-reasoning-control[data-mode=flash]", stylesheet)
        self.assertIn(".image-reasoning-control[data-mode=extra]", stylesheet)
        self.assertIn(".image-reasoning-control[data-mode=max]", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.image-reasoning-menu\s*\{[^}]*display: flex;[^}]*flex-direction: column;[^}]*width: var\(--reasoning-menu-width, 220px\)",
        )
        self.assertRegex(
            stylesheet,
            r"\.image-reasoning-control\s*\{[^}]*overflow: visible;[^}]*isolation: isolate;",
        )
        self.assertRegex(
            stylesheet,
            r"\.reasoning-bg-layer\s*\{[^}]*transition: opacity 0\.5s cubic-bezier\(0\.4, 0, 0\.2, 1\)",
        )
        self.assertRegex(
            stylesheet,
            r"\.reasoning-bg-layer\s*\{[^}]*inset: 0;[^}]*clip-path: inset\(0 round 8px\);",
        )
        self.assertRegex(
            stylesheet,
            r"\.image-reasoning-control\[data-mode=flash\]\s*\{[^}]*background: #8fd3ff;[^}]*box-shadow: none;",
        )
        self.assertIn('.reasoning-bg-layer[data-layer-mode=flash] .reasoning-gradient', stylesheet)
        self.assertIn('.reasoning-bg-layer[data-layer-mode=medium] .reasoning-gradient', stylesheet)
        self.assertIn('.reasoning-bg-layer[data-layer-mode=high] .reasoning-gradient', stylesheet)
        self.assertIn('.reasoning-bg-layer[data-layer-mode=extra] .reasoning-gradient', stylesheet)
        self.assertIn('.reasoning-bg-layer[data-layer-mode=max] .reasoning-gradient', stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.image-reasoning-control\[data-mode=high\]\s*\{[^}]*background: #5865f2;",
        )
        self.assertRegex(
            stylesheet,
            r"\.image-reasoning-control\[data-mode=extra\]\s*\{[^}]*background: #6d5dfc;",
        )
        self.assertIn("Math.round(fraction * (IMAGE_REASONING_MODES.length - 1))", page)
        self.assertIn("Math.min(IMAGE_REASONING_MODES.length - 1, nextIndex)", page)
        self.assertIn("@keyframes flashFluidGradient", stylesheet)
        self.assertIn("@keyframes mediumSeamlessFlow", stylesheet)
        self.assertIn("@keyframes highGlowPulse", stylesheet)
        self.assertIn("@keyframes maxGlowPulse", stylesheet)
        self.assertIn("@keyframes maxSliderBorderPulse", stylesheet)
        self.assertIn("@keyframes maxSliderAmbientGlow", stylesheet)
        self.assertNotIn("@keyframes maxSliderInternalPulse", stylesheet)
        self.assertNotIn(".slider-track-bg::after", stylesheet)
        self.assertIn("--reasoning-hover-shadow", stylesheet)
        self.assertNotIn("--reasoning-hover-edge", stylesheet)
        hover_block = re.search(
            r"\.image-reasoning-control:has\(#generateEditedImageButton:hover:not\(:disabled\)\)::after\s*\{([^}]*)\}",
            stylesheet,
            re.DOTALL,
        )
        self.assertIsNotNone(hover_block)
        self.assertNotIn("outline:", hover_block.group(1))
        self.assertNotIn("0 0 0 2px", hover_block.group(1))
        self.assertNotIn("animation-play-state: paused", hover_block.group(1))
        self.assertIn(".image-reasoning-control::after", stylesheet)
        self.assertIn("box-shadow: var(--reasoning-hover-shadow)", stylesheet)
        self.assertIn("transition: opacity 0.7s cubic-bezier(0.4, 0, 0.2, 1)", stylesheet)
        self.assertIn(".image-reasoning-control:has(#generateEditedImageButton:hover:not(:disabled))::after", stylesheet)
        self.assertIn("sliderTrack.classList.add('is-max-entering')", page)
        self.assertIn("sliderTrack.classList.add('is-max-settled')", page)
        self.assertIn("animation: maxSliderBorderPulse 460ms cubic-bezier(0.18, 0.72, 0.24, 1) 60ms both", stylesheet)
        self.assertIn("}, 540);", page)
        button_block = re.search(
            r"#generateEditedImageButton,\s*#imageReasoningMenuButton\s*\{([^}]*)\}",
            stylesheet,
        ).group(1)
        self.assertNotIn("translate3d", button_block)
        self.assertNotIn("will-change", button_block)
        self.assertRegex(
            stylesheet,
            r"\.reasoning-bg-layer\s*\{[^}]*inset: 0;[^}]*clip-path: inset\(0 round 8px\);",
        )
        self.assertRegex(
            stylesheet,
            r"\.image-reasoning-control\s*\{[^}]*backface-visibility: hidden;[^}]*contain: style;[^}]*will-change: box-shadow;",
        )
        self.assertNotRegex(
            stylesheet,
            r"\.image-reasoning-control\s*\{[^}]*contain: layout",
        )
        self.assertNotRegex(
            stylesheet,
            r"\.image-reasoning-control\s*\{[^}]*transform: translate3d",
        )
        self.assertIn("0 0 24px rgba(167, 139, 250, 0.16)", stylesheet)
        self.assertIn("0 0 29px rgba(168, 85, 247, 0.24)", stylesheet)
        self.assertIn("mix-blend-mode: normal", stylesheet)
        self.assertNotIn("mix-blend-mode: screen", stylesheet)
        self.assertIn(".toggle-switch input:checked + .toggle-slider", stylesheet)
        self.assertIn(".horizontal-slider-container", stylesheet)
        self.assertIn(".slider-gradient-next.is-blending", stylesheet)
        self.assertIn("transition: opacity 520ms cubic-bezier(0.22, 1, 0.36, 1)", stylesheet)
        self.assertIn(
            'id="sliderTrackFill" class="slider-track-fill" '
            'style="transform: translate3d(0, 0, 0) scaleX(0);"',
            page,
        )
        self.assertNotIn(
            'id="sliderTrackFill" class="slider-track-fill" style="width: 0%;"',
            page,
        )
        self.assertIn("sliderFill.style.transform = `translate3d(0, 0, 0) scaleX(${percentage / 100})`", page)
        self.assertIn("transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1)", stylesheet)
        self.assertIn("transform-origin: left center", stylesheet)
        self.assertIn("controlRight - menuWidth", page)
        self.assertIn(".image-reasoning-menu.is-open", stylesheet)
        self.assertIn(".image-reasoning-panel", stylesheet)
        self.assertIn(".image-reasoning-toggle", stylesheet)
        self.assertIn(".image-reasoning-log", stylesheet)
        self.assertIn("max-height: calc(9.9em + 14px)", stylesheet)
        self.assertIn(".image-reasoning-log.is-fully-expanded", stylesheet)
        self.assertIn(".image-reasoning-log-shell", stylesheet)
        self.assertIn(".image-reasoning-content-toggle", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.image-reasoning-content-toggle\s*\{[^}]*right: 14px;[^}]*border: 0;[^}]*background: transparent;",
        )
        self.assertIn(".image-reasoning-supplement-tabs", stylesheet)
        self.assertIn(".image-reasoning-supplement-toggle.is-active", stylesheet)
        self.assertIn(".web-search-row", stylesheet)
        self.assertIn(".toggle-switch", stylesheet)
        self.assertIn(".image-reasoning-panel.mode-flash", stylesheet)
        self.assertIn(".image-reasoning-panel.mode-medium", stylesheet)
        self.assertIn(".image-reasoning-panel.mode-high", stylesheet)
        self.assertIn(".image-reasoning-panel.mode-extra", stylesheet)
        self.assertIn(".image-reasoning-panel.mode-max", stylesheet)
        self.assertNotIn("@keyframes reasoningPanelBreath", stylesheet)
        self.assertIn("@keyframes reasoningBorderSweep", stylesheet)
        self.assertIn("@keyframes reasoningInnerSweep", stylesheet)
        self.assertIn("background-position: 115% 0", stylesheet)
        self.assertIn("background-position: -15% 0", stylesheet)
        self.assertIn("@keyframes reasoningPanelComplete", stylesheet)
        self.assertIn("@keyframes reasoningPanelInnerComplete", stylesheet)
        self.assertIn(".image-reasoning-web-references", stylesheet)
        self.assertIn(".image-reasoning-web-reference", stylesheet)
        self.assertIn("@keyframes reasoningCursor", stylesheet)
        self.assertIn("const IMAGE_STREAM_PARTIAL_DURATION = 10000", page)
        self.assertIn("const IMAGE_STREAM_CLARITY_STEP = 0.25", page)
        self.assertNotIn("IMAGE_STREAM_PARTIAL_BLUR_STEP", page)
        self.assertIn("const IMAGE_STREAM_FINAL_DURATION = 3000", page)
        self.assertIn("const IMAGE_STREAM_INITIAL_FADE_DURATION = 5000", page)
        self.assertIn("const IMAGE_STREAM_CROSSFADE_DURATION = 10000", page)
        self.assertIn("const IMAGE_STREAM_DEBUG_PREFIX = '[ImageStreamBlur]'", page)
        self.assertIn("function imageStreamActualStyle(image)", page)
        self.assertIn("const style = getComputedStyle(image)", page)
        self.assertIn("actualBlurPx", page)
        self.assertIn("window.getImageStreamDebugLog", page)
        self.assertIn("append_image_stream_debug", page)
        self.assertIn("frameIndex === 0 && frame.kind === 'partial'", page)
        self.assertIn("function currentImageRevealBlur(item, beforeFrameIndex", page)
        self.assertIn("function freezeImageRevealFrame(frame, image, blur, opacity", page)
        self.assertIn("frame.frozenOpacity", page)
        self.assertIn("function imageRevealTargetBlur(partialIndex)", page)
        self.assertIn("? 1 - index * IMAGE_STREAM_CLARITY_STEP", page)
        self.assertIn("frame.fromBlur = Math.max(Number(frame.toBlur) || 0, Math.min(capturedBlur, visibleBlur))", page)
        self.assertIn("if (incomingPartialIndex <= previousPartialIndex)", page)
        self.assertIn("imageStreamDebugLog('partial-ignored'", page)
        self.assertIn("item.revealFrames.push(frame)", page)
        self.assertIn("const imageRevealElementCache = new WeakMap()", page)
        self.assertIn("createImageRevealElement(item, frame, frameIndex)", page)
        load_originals_block = re.search(
            r"async function loadImageGenerationSetOriginals\(set\)\s*\{(.+?)\n        \}",
            page,
            re.DOTALL,
        ).group(1)
        self.assertNotIn("currentItem.revealFrames = []", load_originals_block)
        self.assertIn("function captureImageRevealFramesForRender()", page)
        self.assertIn("imageStreamDebugLog('frame-captured-for-render'", page)
        self.assertIn("frame.fromOpacity = Math.max(0, Math.min(1, actual.opacity))", page)
        self.assertIn("frame.resumeDuration = Math.max(1, blurRemaining)", page)
        self.assertIn("Number.isFinite(frame.resumeCrossfadeDuration)", page)
        self.assertIn("captureImageRevealFramesForRender();\n            container.replaceChildren()", page)
        self.assertIn("function scheduleFinalImageFrameCleanup(item, frame)", page)
        self.assertIn("const cleanupDuration = frame.duration", page)
        self.assertIn("Math.max(0, cleanupDuration - elapsed)", page)
        self.assertIn("item.revealFrames = [frame]", page)
        self.assertIn("function toggleImageGenerationSet(setId)", page)
        self.assertIn("if (!set || set.status === 'running') return", page)
        self.assertIn("toggleButton.addEventListener('click', () => toggleImageGenerationSet(set.setId))", page)
        self.assertIn("if (!set.expanded && set.history)", page)
        self.assertIn("set.previewsLoaded = false", page)
        self.assertIn("existingSet.expanded = false", page)
        self.assertNotIn("set.status = 'completed';\n                set.expanded = false", page)
        self.assertIn("if (!expanded)", page)
        self.assertIn("function loadImageGenerationSetPreviews(set)", page)
        self.assertIn("previewPath = item.previewPath || item.result?.previewPath", page)
        self.assertIn("setImageReasoningMode(normalizeImageReasoningMode(set.reasoningMode), false)", page)
        self.assertIn("item.setId === cleanSetId || item.requestId === cleanSetId", page)
        self.assertIn("const existingFinalFrame = Array.isArray(item.revealFrames)", page)
        self.assertIn("if (existingFinalFrame) return", page)
        self.assertIn("setImageWebSearchEnabled(set.webSearchEnabled === true, false)", page)
        self.assertIn("previousPrompt: set.originalPrompt || set.prompt || ''", page)
        self.assertIn("reasoningMode: window.imageEditState.reasoningMode", page)
        self.assertIn("setImageReasoningMode(normalizeImageReasoningMode(draft.reasoningMode), false)", page)
        self.assertIn("function reasoningTurnsForSet(set)", page)
        self.assertIn("if (!Array.isArray(set.reasoningTurns)) set.reasoningTurns = []", page)
        self.assertIn("reasoningTurns: Array.isArray(set.reasoningTurns) ? set.reasoningTurns : []", page)
        self.assertNotIn("return [...set.reasoningTurns]", page)
        self.assertIn("if (!reasoningTurnsForSet(set).length) set.reasoningSummary += event.delta || ''", page)
        self.assertIn("reasoningTurnsForSet(set).forEach(turn =>", page)
        self.assertNotIn("pendingSet?.reasoningTurns.find", page)
        self.assertNotIn("control.closest('.image-generation-footer')?.getBoundingClientRect()", page)
        self.assertIn("const anchorTop = (controlRect.top - layerRect.top) / scale", page)
        self.assertIn("if (appMain && menu.parentElement !== appMain) appMain.append(menu)", page)
        self.assertIn("function copyImagePrompt(text, label)", page)
        self.assertIn("复制原始提示词", page)
        self.assertIn("复制思维优化提示词", page)
        self.assertIn("image-generation-set-title-row", page)
        self.assertIn("image-reasoning-final-prompt", page)
        self.assertIn("function browserImageSource(...candidates)", page)
        self.assertIn("!candidate.toLowerCase().startsWith('file:')", page)
        self.assertIn("browserImageSource(item.previewUri, item.uri)", page)
        self.assertIn("browserImageSource(item.previewUri, item.uri)", page)
        self.assertIn("browserImageSource(item.fullUri || item.result?.fullUri, frame.uri, frame.fallbackUri)", page)
        self.assertIn("image.src = browserImageSource(result.previewUri, result.uri)", page)
        self.assertIn("preview.src = browserImageSource(file.previewUri, file.uri)", page)
        self.assertIn("image.src = browserImageSource(reference.previewUri, reference.uri)", page)
        self.assertNotIn("image.src = result.uri || result.previewUri", page)
        self.assertIn("window.pywebview.api.load_generated_image(item.result.path)", page)
        self.assertIn("--image-reveal-from-blur", page)
        self.assertIn("--image-reveal-to-blur", page)
        self.assertIn("--image-reveal-delay", page)
        self.assertIn("--image-reveal-crossfade-delay", page)
        self.assertIn(".image-generation-item img.is-image-reveal", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.image-generation-item img\s*\{[^}]*object-fit: contain;",
        )
        self.assertIn("function imageGenerationItemAspectRatio(item)", page)
        self.assertIn("set.history ? ' is-history' : ''", page)
        self.assertIn("function applyImageGenerationItemAspectRatio(card, item, image = null)", page)
        self.assertIn("item.width = image.naturalWidth", page)
        self.assertIn("card.style.aspectRatio = aspectRatio", page)
        self.assertIn("applyImageGenerationItemAspectRatio(card, item)", page)
        self.assertIn("image.addEventListener('load', () => applyImageGenerationItemAspectRatio(card, item, image))", page)
        self.assertIn("item.width = Number(event.width) || item.width || 0", page)
        self.assertIn("item.height = Number(event.height) || item.height || 0", page)
        self.assertIn("animation-name: imageBlurReveal, imageLayerReveal", stylesheet)
        self.assertIn("will-change: filter, opacity, transform", stylesheet)
        self.assertIn("transform: translate3d(0, 0, 0)", stylesheet)
        self.assertIn("backface-visibility: hidden", stylesheet)
        self.assertIn("contain: paint", stylesheet)
        self.assertIn("@keyframes imageBlurReveal", stylesheet)
        self.assertIn("@keyframes imageLayerReveal", stylesheet)
        self.assertIn("const reasoningMode = normalizeImageReasoningMode(set.reasoningMode)", page)
        self.assertIn("mode-${reasoningMode}", page)
        self.assertIn("function createReasoningColorPulse(target, mode)", page)
        self.assertIn("const IMAGE_REASONING_STATUS_COLORS", page)
        self.assertIn("function pulseImageGenerationStatusText(mode, duration)", page)
        self.assertIn("if (pulse) pulseImageGenerationStatusText(cleanMode, pulse.duration)", page)
        self.assertIn("label.style.willChange = 'color, text-shadow'", page)
        self.assertIn("label.style.willChange = 'auto'", page)
        self.assertNotIn("function cloneImageReasoningBackground(mode)", page)
        self.assertNotIn("card.append(reasoningLayer)", page)
        self.assertNotIn("imageGenerationAnimationObserver", page)
        self.assertNotIn(".image-generation-item.is-animation-active", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.image-generation-item:is\(\.is-queued, \.is-running\)\s*\{[^}]*background: #fff;",
        )
        self.assertRegex(
            stylesheet,
            r"\.image-generation-item:is\(\.is-queued, \.is-running\) > span:not\(\.reasoning-bg-layer\)\s*\{[^}]*background: #fff;[^}]*color: #64748b;[^}]*text-shadow: none;",
        )
        self.assertNotIn("@keyframes imageGenerationDiffuseDrift", stylesheet)
        self.assertNotIn("@keyframes imageGenerationLightFlow", stylesheet)
        self.assertNotIn("--generation-glow-a", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.image-generation-item\s*\{[^}]*background: var\(--bg\);[^}]*contain: layout paint style;",
        )
        self.assertRegex(
            stylesheet,
            r"\.image-generation-item img\s*\{[^}]*object-fit: contain;[^}]*background: var\(--bg\);",
        )
        self.assertRegex(
            stylesheet,
            r"\.image-generation-item > span:not\(\.reasoning-bg-layer\)\s*\{[^}]*z-index: 2;",
        )
        self.assertIn(".image-generation-item img.is-reveal-pending", stylesheet)
        self.assertIn(".image-generation-set-loading", stylesheet)
        self.assertIn(".image-prompt-copy", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.image-prompt-copy\s*\{[^}]*width: 16px[^}]*height: 16px[^}]*border: 0[^}]*background: transparent",
        )
        self.assertNotIn("background: rgba(2, 6, 23, 0.46)", stylesheet)
        self.assertNotIn("@keyframes imagePartialReveal", stylesheet)
        self.assertNotIn("@keyframes imageFinalReveal", stylesheet)
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet)

    def test_main_page_window_controls_use_lucide_icons(self):
        page = frontend_source()
        stylesheet = frontend_stylesheet()

        self.assertIn('data-lucide="minus"', page)
        self.assertIn('data-lucide="square"', page)
        self.assertIn('data-lucide="x"', page)
        self.assertIn('class="titlebar-icon"', page)
        self.assertIn("flex: 0 0 33px", stylesheet)
        self.assertIn("#windowTitleBar {\n  z-index: auto;", stylesheet)
        self.assertIn("width: 46px", stylesheet)
        self.assertIn(".window-controls {\n  position: relative;\n  z-index: 101;", stylesheet)
        self.assertIn("font-size: 12px", stylesheet)
        self.assertIn("stroke-width: 1.5 !important", stylesheet)
        self.assertIn("setLucideIcon(icon, isMaximized ? 'copy' : 'square', 'titlebar-icon')", page)


class ControllerTests(unittest.TestCase):
    def test_image_task_context_cancel_closes_active_responses(self):
        class FakeResponse:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        context = controller_image_reasoning.ImageTaskContext()
        responses = [FakeResponse(), FakeResponse()]
        for response in responses:
            context.add_response(response)

        context.cancel()

        self.assertTrue(context.cancel_event.is_set())
        self.assertTrue(all(response.closed for response in responses))
        with self.assertRaises(controller_image_reasoning.ImageGenerationCancelled):
            context.check_cancelled()

    def test_import_reference_image_validates_and_persists_image_data(self):
        controller = app.AppController.__new__(app.AppController)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_buffer = __import__("io").BytesIO()
            app.Image.new("RGBA", (12, 8), (10, 20, 30, 128)).save(
                image_buffer,
                format="PNG",
            )
            data_url = "data:image/png;base64," + __import__("base64").b64encode(
                image_buffer.getvalue()
            ).decode("ascii")

            with patch.object(controller_image_files, "app_data_dir", return_value=root):
                result = controller.import_reference_image(data_url, "clipboard.png")
                invalid = controller.import_reference_image(
                    "data:image/png;base64," + __import__("base64").b64encode(b"not-image").decode("ascii"),
                    "invalid.png",
                )

            imported_path = Path(result["path"])
            self.assertTrue(result["ok"])
            self.assertTrue(imported_path.is_file())
            self.assertEqual(imported_path.parent, root / "image-references")
            self.assertEqual(result["uri"], imported_path.as_uri())
            self.assertFalse(invalid["ok"])
            self.assertIn("图片内容无效", invalid["error"])

    def test_choose_edit_images_imports_only_first_sixteen_with_warning(self):
        controller = app.AppController.__new__(app.AppController)
        with tempfile.TemporaryDirectory() as temp:
            paths = []
            for index in range(18):
                image_path = Path(temp) / f"reference-{index}.png"
                app.Image.new("RGB", (2, 2), "red").save(image_path, format="PNG")
                paths.append(str(image_path))
            oversized_path = Path(temp) / "oversized.png"
            with oversized_path.open("wb") as handle:
                handle.seek(50 * 1024 * 1024)
                handle.write(b"0")
            paths.append(str(oversized_path))
            controller.window = SimpleNamespace(create_file_dialog=lambda *_args, **_kwargs: paths)

            result = controller.choose_edit_images()

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["paths"]), 16)
        self.assertEqual(len(result["files"]), 16)
        self.assertIn("仅导入前 16 张", result["warning"])
        self.assertIn("1 张图片超过 50 MB，未导入", result["warning"])

    def test_generate_image_uses_selected_key_and_generation_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_path = root / "reference.png"
            input_image = app.Image.new("RGB", (8, 8), "red")
            input_image.save(input_path, format="PNG")
            result_counter = __import__("itertools").count()

            def generate_result(*_args, **_kwargs):
                result_path = root / f"result-{next(result_counter)}.png"
                Image.new("RGB", (8, 8), "blue").save(result_path)
                return {
                    "ok": True,
                    "path": str(result_path),
                    "uri": result_path.as_uri(),
                    "width": 8,
                    "height": 8,
                    "format": "png",
                    "actualSize": "8x8",
                }

            service = SimpleNamespace(
                generate=__import__("unittest.mock").mock.Mock(
                    side_effect=generate_result
                )
            )
            controller = app.AppController.__new__(app.AppController)
            controller.image_generator = service
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {
                    "id": key_id,
                    "base_url": "https://example.test/v1",
                },
                get_secret=lambda _key_id: "secret",
            )

            events = []
            with patch.object(controller_image_reasoning, "app_data_dir", return_value=root / "data"), patch.object(
                controller_image_files, "generated_pictures_dir",
                return_value=root / "Pictures" / app.APP_NAME,
            ):
                result = controller.generate_image(
                    "key-1",
                    "Combine this reference into a new image",
                    [str(input_path)],
                    {
                        "size": "1024x1024",
                        "quality": "low",
                        "outputPreset": "lossless",
                        "background": "opaque",
                        "moderation": "auto",
                        "imageCount": 2,
                        "requestId": "request-1",
                    },
                    event_callback=events.append,
                )
                saved_paths_exist = all(
                    Path(item["savedPath"]).is_file() for item in result["items"]
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["setId"], "request-1")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(service.generate.call_count, 2)
        args = service.generate.call_args_list[0].args
        self.assertEqual(args[0:2], ("https://example.test/v1", "secret"))
        self.assertEqual(args[2].image_paths, (input_path.resolve(),))
        self.assertEqual(args[2].fields["model"], "gpt-image-2")
        self.assertEqual(args[2].fields["quality"], "low")
        self.assertEqual(args[2].fields["output_format"], "png")
        self.assertEqual(args[2].fields["background"], "auto")
        self.assertEqual(args[2].fields["moderation"], "low")
        self.assertNotIn("imageCount", args[2].fields)
        self.assertEqual(
            {call.args[3] for call in service.generate.call_args_list},
            {
                root
                / "Pictures"
                / app.APP_NAME
                / "sessions"
                / result["sessionId"]
                / "round-001-request-1"
                / "process-images"
                / f"item-{item_index:03d}"
                for item_index in (1, 2)
            },
        )
        self.assertTrue(saved_paths_exist)
        self.assertEqual(events[0]["type"], "set_started")
        self.assertEqual(events[-1]["type"], "set_completed")
        self.assertEqual(
            sum(event["type"] == "item_completed" for event in events),
            2,
        )

    def test_cancel_image_generation_stops_running_request_and_releases_task(self):
        threading = __import__("threading")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generation_started = threading.Event()
            result_holder = {}

            def generate_until_cancelled(*_args, **kwargs):
                task_context = kwargs["task_context"]
                response = SimpleNamespace(closed=False)
                response.close = lambda: setattr(response, "closed", True)
                task_context.add_response(response)
                generation_started.set()
                task_context.cancel_event.wait(2)
                self.assertTrue(response.closed)
                task_context.check_cancelled()

            controller = app.AppController.__new__(app.AppController)
            controller.image_generator = SimpleNamespace(generate=generate_until_cancelled)
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {
                    "id": key_id,
                    "base_url": "https://example.test/v1",
                },
                get_secret=lambda _key_id: "secret",
            )
            events = []

            def run_generation():
                result_holder["result"] = controller.generate_image(
                    "key-1",
                    "生成一张测试图片",
                    [],
                    {"imageCount": 1, "requestId": "cancel-request"},
                    event_callback=events.append,
                )

            with patch.object(controller_image_reasoning, "app_data_dir", return_value=root / "data"), patch.object(
                controller_image_files,
                "generated_pictures_dir",
                return_value=root / "Pictures" / app.APP_NAME,
            ):
                worker = threading.Thread(target=run_generation)
                worker.start()
                self.assertTrue(generation_started.wait(2))
                cancelled = controller.cancel_image_generation("cancel-request")
                worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertTrue(cancelled["ok"])
        self.assertTrue(result_holder["result"]["cancelled"])
        self.assertEqual(events[-1]["type"], "set_cancelled")
        self.assertNotIn("cancel-request", controller.image_tasks)

    def test_generate_image_runs_independent_sessions_concurrently(self):
        threading = __import__("threading")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            overlap = threading.Barrier(2, timeout=2)
            results = {}

            def generate_with_overlap(_base_url, _secret, _request, output_dir, **_kwargs):
                overlap.wait()
                output_dir.mkdir(parents=True, exist_ok=True)
                result_path = output_dir / "result.png"
                Image.new("RGB", (8, 8), "blue").save(result_path)
                return {
                    "ok": True,
                    "path": str(result_path),
                    "uri": result_path.as_uri(),
                    "width": 8,
                    "height": 8,
                    "format": "png",
                    "actualSize": "8x8",
                }

            controller = app.AppController.__new__(app.AppController)
            controller.image_generator = SimpleNamespace(generate=generate_with_overlap)
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {
                    "id": key_id,
                    "base_url": "https://example.test/v1",
                },
                get_secret=lambda _key_id: "secret",
            )

            def run_generation(request_id):
                results[request_id] = controller.generate_image(
                    "key-1",
                    f"并发任务 {request_id}",
                    [],
                    {"imageCount": 1, "requestId": request_id},
                )

            with patch.object(controller_image_reasoning, "app_data_dir", return_value=root / "data"), patch.object(
                controller_image_files,
                "generated_pictures_dir",
                return_value=root / "Pictures" / app.APP_NAME,
            ):
                workers = [
                    threading.Thread(target=run_generation, args=(request_id,))
                    for request_id in ("parallel-a", "parallel-b")
                ]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join(4)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(set(results), {"parallel-a", "parallel-b"})
        self.assertTrue(all(result["ok"] for result in results.values()))

    def test_generate_image_accepts_missing_reference_images(self):
        with tempfile.TemporaryDirectory() as temp:
            result_path = Path(temp) / "result.png"
            Image.new("RGB", (8, 8), "green").save(result_path)
            controller = app.AppController.__new__(app.AppController)
            controller.image_generator = SimpleNamespace(
                generate=__import__("unittest.mock").mock.Mock(
                    return_value={
                        "ok": True,
                        "path": str(result_path),
                        "uri": result_path.as_uri(),
                        "width": 8,
                        "height": 8,
                        "format": "png",
                        "actualSize": "8x8",
                    }
                )
            )
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {"id": key_id, "base_url": "https://example.test/v1"},
                get_secret=lambda _key_id: "secret",
            )

            with patch.object(controller_image_files, "generated_pictures_dir", return_value=Path(temp) / "Pictures"):
                result = controller.generate_image("key-1", "Generate image", [], {})

        self.assertTrue(result["ok"])

    def test_instant_continuation_uses_parent_asset_for_semantic_generate_without_frontend_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pictures_root = root / "Pictures"
            session_store = image_editor.ImageSessionStore(pictures_root)
            parent_source = root / "parent.png"
            output_source = root / "output.png"
            Image.new("RGB", (8, 8), "purple").save(parent_source)
            Image.new("RGB", (8, 8), "blue").save(output_source)
            session_store.begin_round(
                "world-session",
                "set-1",
                "设计紫色披风角色",
                1,
                0,
                {"originalPrompt": "设计紫色披风角色", "operation": "generate"},
            )
            parent_result = session_store.persist_result(
                "world-session",
                "set-1",
                0,
                parent_source,
                {"width": 8, "height": 8, "format": "png"},
            )
            session_store.complete_round("world-session", "set-1")
            service = SimpleNamespace(
                generate=__import__("unittest.mock").mock.Mock(
                    return_value={
                        "ok": True,
                        "path": str(output_source),
                        "uri": output_source.as_uri(),
                        "width": 8,
                        "height": 8,
                        "format": "png",
                        "actualSize": "8x8",
                    }
                )
            )
            controller = app.AppController.__new__(app.AppController)
            controller.active_image_sets = set()
            controller.image_generator = service
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {
                    "id": key_id,
                    "base_url": "https://example.test/v1",
                },
                get_secret=lambda _key_id: "secret",
            )

            def stream_response(*_args, **_kwargs):
                return json.dumps(
                    {
                        "operation": "generate",
                        "selected_asset_ids": [parent_result["assetId"]],
                        "descriptions": [
                            {
                                "asset_id": parent_result["assetId"],
                                "description": "紫色披风、银色肩甲的角色正面图",
                            }
                        ],
                        "rationale": "延续角色身份并创作新的车站场景。",
                    },
                    ensure_ascii=False,
                )

            controller.client = SimpleNamespace(stream_response=stream_response)
            events = []
            with patch.object(controller_image_reasoning, "app_data_dir", return_value=root / "data"), patch.object(
                controller_image_files, "generated_pictures_dir", return_value=pictures_root
            ):
                result = controller.generate_image(
                    "key-1",
                    "让她走进雨夜车站",
                    [],
                    {
                        "requestId": "set-2",
                        "sessionId": "world-session",
                        "parentSetId": "set-1",
                        "continuation": True,
                        "reasoningMode": "instant",
                    },
                    events.append,
                )

            generated_request = service.generate.call_args.args[2]
            manifest = json.loads(
                (pictures_root / "sessions" / "world-session" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["prompt"], "让她走进雨夜车站")
        self.assertEqual(result["operation"], "generate")
        self.assertEqual(result["transportOperation"], "edit")
        self.assertEqual(result["selectedAssetIds"], [parent_result["assetId"]])
        self.assertEqual(generated_request.operation, "generate")
        self.assertEqual(len(generated_request.image_paths), 1)
        self.assertIn("sessions", str(generated_request.image_paths[0]))
        set_started = next(event for event in events if event["type"] == "set_started")
        self.assertEqual(set_started["operation"], "generate")
        self.assertEqual(set_started["selectedAssetIds"], [parent_result["assetId"]])
        self.assertEqual(manifest["rounds"][1]["options"]["operation"], "generate")
        self.assertEqual(
            manifest["rounds"][1]["options"]["selectedAssetIds"],
            [parent_result["assetId"]],
        )
        self.assertEqual(
            manifest["assets"][0]["description"],
            "紫色披风、银色肩甲的角色正面图",
        )

    def test_continuation_planner_and_image_request_exclude_sibling_branch_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pictures_root = root / "Pictures"
            session_store = image_editor.ImageSessionStore(pictures_root)
            sources = {}
            for name, color in (
                ("root", "red"),
                ("branch-a", "green"),
                ("branch-b", "blue"),
                ("result", "white"),
            ):
                source_path = root / f"{name}.png"
                Image.new("RGB", (8, 8), color).save(source_path)
                sources[name] = source_path

            session_store.begin_round("branch-world", "set-root", "根", 1, 0, {})
            root_result = session_store.persist_result(
                "branch-world", "set-root", 0, sources["root"],
                {"width": 8, "height": 8, "format": "png"},
            )
            session_store.complete_round("branch-world", "set-root")
            session_store.begin_round(
                "branch-world", "set-a", "A", 1, 0, {}, parent_set_id="set-root"
            )
            branch_a_result = session_store.persist_result(
                "branch-world", "set-a", 0, sources["branch-a"],
                {"width": 8, "height": 8, "format": "png"},
            )
            session_store.complete_round("branch-world", "set-a")
            session_store.begin_round(
                "branch-world", "set-b", "B", 1, 0, {}, parent_set_id="set-root"
            )
            branch_b_result = session_store.persist_result(
                "branch-world", "set-b", 0, sources["branch-b"],
                {"width": 8, "height": 8, "format": "png"},
            )
            session_store.complete_round("branch-world", "set-b")
            session_store.update_asset_descriptions(
                "branch-world",
                {
                    root_result["assetId"]: "红色根场景",
                    branch_a_result["assetId"]: "绿色 A 分支场景",
                    branch_b_result["assetId"]: "蓝色 B 分支场景",
                },
            )
            branch_b_asset_path = next(
                asset["path"]
                for asset in session_store.continuation_context(
                    "branch-world", "set-b"
                )["assets"]
                if asset["assetId"] == branch_b_result["assetId"]
            )
            service = SimpleNamespace(
                generate=__import__("unittest.mock").mock.Mock(
                    return_value={
                        "ok": True,
                        "path": str(sources["result"]),
                        "uri": sources["result"].as_uri(),
                        "width": 8,
                        "height": 8,
                        "format": "png",
                        "actualSize": "8x8",
                    }
                )
            )
            controller = app.AppController.__new__(app.AppController)
            controller.active_image_sets = set()
            controller.image_generator = service
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {
                    "id": key_id,
                    "base_url": "https://example.test/v1",
                },
                get_secret=lambda _key_id: "secret",
            )
            planner_inputs = []

            def stream_response(*args, **_kwargs):
                planner_inputs.append(__import__("copy").deepcopy(args[4]))
                return json.dumps(
                    {
                        "operation": "generate",
                        "selected_asset_ids": [branch_a_result["assetId"]],
                        "descriptions": [],
                        "rationale": "仅延续 A 分支。",
                    },
                    ensure_ascii=False,
                )

            controller.client = SimpleNamespace(stream_response=stream_response)
            with patch.object(controller_image_reasoning, "app_data_dir", return_value=root / "data"), patch.object(
                controller_image_files, "generated_pictures_dir", return_value=pictures_root
            ):
                result = controller.generate_image(
                    "key-1",
                    "继续 A 分支",
                    [],
                    {
                        "requestId": "set-a-child",
                        "sessionId": "branch-world",
                        "parentSetId": "set-a",
                        "continuation": True,
                        "reasoningMode": "instant",
                    },
                )

            planner_payload = json.dumps(planner_inputs, ensure_ascii=False)
            generated_request = service.generate.call_args.args[2]
            generated_paths = {str(path) for path in generated_request.image_paths}

        self.assertTrue(result["ok"])
        self.assertEqual(result["roundNumber"], 3)
        self.assertIn(branch_a_result["assetId"], planner_payload)
        self.assertNotIn(branch_b_result["assetId"], planner_payload)
        self.assertNotIn(branch_b_asset_path, planner_payload)
        self.assertEqual(len(generated_paths), 1)
        self.assertNotIn(branch_b_asset_path, generated_paths)
        self.assertNotIn(str(sources["branch-b"]), generated_paths)

    def test_continuation_describes_more_than_sixteen_old_assets_in_batches_before_planning(self):
        controller = app.AppController.__new__(app.AppController)
        old_assets = [
            {"assetId": f"asset-{index:02d}", "path": f"old-{index:02d}.png", "description": ""}
            for index in range(17)
        ]
        parent_asset = {"assetId": "asset-parent", "path": "parent.png", "description": ""}
        context = {
            "history": [{"setId": "set-parent", "outputAssetIds": ["asset-parent"]}],
            "parentOutputAssetIds": ["asset-parent"],
            "assets": [*old_assets, parent_asset],
        }
        batch_sizes = []
        saved_descriptions = {}
        controller._describe_image_asset_batch = __import__("unittest.mock").mock.Mock(
            side_effect=lambda _record, _secret, assets: (
                batch_sizes.append(len(assets))
                or {asset["assetId"]: f"描述 {asset['assetId']}" for asset in assets}
            )
        )
        session_store = SimpleNamespace(
            update_asset_descriptions=lambda _session_id, descriptions: saved_descriptions.update(
                descriptions
            ),
            continuation_context=lambda _session_id, _parent_set_id, _additional_asset_ids=None: {
                **context,
                "assets": [
                    {
                        **asset,
                        "description": saved_descriptions.get(asset["assetId"], asset["description"]),
                    }
                    for asset in context["assets"]
                ],
            },
        )

        refreshed = controller._cache_undescribed_image_assets(
            {"base_url": "https://example.test/v1"},
            "secret",
            session_store,
            "session-world",
            "set-parent",
            context,
            ["asset-parent"],
        )

        self.assertEqual(batch_sizes, [16, 1])
        self.assertEqual(len(saved_descriptions), 17)
        self.assertTrue(all(asset["description"] for asset in refreshed["assets"][:-1]))
        self.assertEqual(refreshed["assets"][-1]["description"], "")

    def test_polish_prompt_uses_terra_and_streams_events(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_key_record=lambda key_id: {
                "id": key_id,
                "base_url": "https://example.test/v1",
            },
            get_secret=lambda _key_id: "secret",
        )
        events = []

        def stream_response(*_args, **kwargs):
            kwargs["on_delta"]("polished ")
            kwargs["on_delta"]("prompt")
            return "polished prompt"

        controller.client = SimpleNamespace(
            stream_response=__import__("unittest.mock").mock.Mock(side_effect=stream_response)
        )

        result = controller.polish_prompt("key-1", "rough prompt", events.append)

        self.assertEqual(result["prompt"], "polished prompt")
        self.assertEqual(controller.client.stream_response.call_args.args[2], app.PROMPT_POLISH_MODEL)
        self.assertEqual(
            controller.client.stream_response.call_args.kwargs["reasoning_effort"],
            "medium",
        )
        self.assertEqual(
            [event["type"] for event in events],
            [
                "prompt_polish_started",
                "prompt_polish_delta",
                "prompt_polish_delta",
                "prompt_polish_completed",
            ],
        )

    def test_reasoning_modes_keep_model_and_effort_separate(self):
        self.assertEqual(
            {
                mode: (config["model"], config["effort"])
                for mode, config in app.IMAGE_REASONING_MODES.items()
            },
            {
                "flash": ("gpt-5.6-luna", "medium"),
                "medium": ("gpt-5.6-terra", "medium"),
                "high": ("gpt-5.6-terra", "high"),
                "extra": ("gpt-5.6-terra", "xhigh"),
                "max": ("gpt-5.6-sol", "xhigh"),
            },
        )
        self.assertEqual(app.PROMPT_POLISH_MODEL, "gpt-5.6-terra")
        self.assertEqual(app.PROMPT_POLISH_REASONING_EFFORT, "medium")
        self.assertEqual(
            app.IMAGE_WEB_SEARCH_MODES,
            {"flash", "medium", "high", "extra", "max"},
        )
        self.assertEqual(
            {
                mode: (config["max_turns"], config["max_references"])
                for mode, config in app.IMAGE_REASONING_MODES.items()
            },
            {
                "flash": (3, 3),
                "medium": (5, 4),
                "high": (7, 6),
                "extra": (10, 6),
                "max": (12, 8),
            },
        )
        flash_depth = app.IMAGE_REASONING_MODES["flash"]["depth"]
        self.assertIn("自己要完成什么", flash_depth)
        self.assertIn("信息缺口", flash_depth)
        self.assertIn("若开启搜索且缺口重要", flash_depth)
        self.assertEqual(
            app.IMAGE_REASONING_MODES["extra"]["depth"],
            app.IMAGE_REASONING_MODES["max"]["depth"],
        )

    def test_generate_image_rejects_removed_low_reasoning_mode(self):
        controller = app.AppController.__new__(app.AppController)

        result = controller.generate_image(
            "key-1",
            "Generate image",
            [],
            {"reasoningMode": "low"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "无效的思维模式")

    def test_generate_image_medium_react_streams_summary_and_uses_final_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_path = root / "reference.png"
            Image.new("RGB", (8, 8), "red").save(input_path)
            output_path = root / "result.png"
            Image.new("RGB", (8, 8), "blue").save(output_path)
            service = SimpleNamespace(
                generate=__import__("unittest.mock").mock.Mock(
                    return_value={
                        "ok": True,
                        "path": str(output_path),
                        "uri": output_path.as_uri(),
                        "width": 8,
                        "height": 8,
                        "format": "png",
                        "actualSize": "8x8",
                    }
                )
            )
            controller = app.AppController.__new__(app.AppController)
            controller.image_generator = service
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {
                    "id": key_id,
                    "base_url": "https://example.test/v1",
                },
                get_secret=lambda _key_id: "secret",
            )

            def stream_response(*_args, **kwargs):
                text = "环境：工作创作\n方案：清晰图表\n<<<FINAL_PROMPT>>>A clean professional chart"
                for chunk in [
                    "环境：工作创作\n",
                    "方案：清晰图表\n<<<FINAL_",
                    "PROMPT>>>A clean professional chart",
                ]:
                    kwargs["on_delta"](chunk)
                return text

            controller.client = SimpleNamespace(
                stream_response=__import__("unittest.mock").mock.Mock(side_effect=stream_response)
            )
            events = []
            with patch.object(controller_image_reasoning, "app_data_dir", return_value=root / "data"), patch.object(
                controller_image_files, "generated_pictures_dir", return_value=root / "Pictures"
            ):
                result = controller.generate_image(
                    "key-1",
                    "make this clearer",
                    [str(input_path)],
                    {"requestId": "react-1", "reasoningMode": "medium"},
                    event_callback=events.append,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["prompt"], "A clean professional chart")
        self.assertEqual(result["originalPrompt"], "make this clearer")
        self.assertEqual(result["reasoningModel"], "gpt-5.6-terra")
        self.assertEqual(result["reasoningEffort"], "medium")
        self.assertEqual(service.generate.call_args.args[2].prompt, "A clean professional chart")
        response_call = controller.client.stream_response.call_args
        self.assertEqual(response_call.args[2], "gpt-5.6-terra")
        self.assertEqual(response_call.kwargs["reasoning_effort"], "medium")
        self.assertEqual(
            [tool["name"] for tool in response_call.kwargs["tools"]],
            ["search_web", "search_visual_references", "select_visual_references"],
        )
        response_input = response_call.args[4]
        self.assertEqual(response_input[0]["content"][0]["type"], "input_text")
        self.assertEqual(response_input[0]["content"][1]["type"], "input_image")
        event_types = [event["type"] for event in events]
        self.assertEqual(event_types[0], "react_started")
        self.assertIn("react_turn_started", event_types)
        self.assertIn("react_turn_delta", event_types)
        self.assertIn("react_turn_completed", event_types)
        self.assertIn("react_summary_delta", event_types)
        self.assertLess(event_types.index("react_completed"), event_types.index("set_started"))
        visible_summary = "".join(
            event.get("delta", "")
            for event in events
            if event["type"] == "react_summary_delta"
        )
        self.assertNotIn("<<<FINAL_PROMPT>>>", visible_summary)
        completed_turn = next(
            event for event in events if event["type"] == "react_turn_completed"
        )
        self.assertEqual(completed_turn["text"], "环境：工作创作\n方案：清晰图表")

    def test_flash_agent_assesses_information_gaps_and_keeps_search_tools(self):
        controller = app.AppController.__new__(app.AppController)
        captured = {}
        events = []

        def stream_response(*args, **kwargs):
            captured["instructions"] = args[3]
            captured["tools"] = kwargs["tools"]
            captured["reasoning_effort"] = kwargs["reasoning_effort"]
            text = "已判断任务目标与信息缺口。\n<<<FINAL_PROMPT>>>Fast final prompt"
            kwargs["on_delta"](text)
            kwargs["on_completed"]({
                "output": [],
                "usage": {
                    "input_tokens": 180,
                    "output_tokens": 45,
                    "total_tokens": 225,
                    "cost": 0.0036,
                },
            })
            return text

        controller.client = SimpleNamespace(stream_response=stream_response)
        result = controller._run_image_prompt_agent(
            {"base_url": "https://example.test/v1"},
            "secret",
            "Create an accurate product image",
            (),
            "flash",
            True,
            lambda event_type, **details: events.append({"type": event_type, **details}),
        )

        self.assertEqual(captured["reasoning_effort"], "medium")
        self.assertIn("第一步都必须先判断", captured["instructions"])
        self.assertIn("不得因为处于 Flash 模式就跳过", captured["instructions"])
        self.assertIn("若缺口会影响事实", captured["instructions"])
        self.assertEqual(result["reasoningUsage"]["totalTokens"], 225)
        self.assertEqual(result["reasoningUsage"]["callCount"], 1)
        self.assertAlmostEqual(result["reasoningUsage"]["costUsd"], 0.0036)
        self.assertEqual(result["reasoningUsage"]["costedCallCount"], 1)
        self.assertEqual(result["reasoningUsage"]["costSource"], "response")
        self.assertTrue(result["reasoningUsage"]["hasCost"])
        completed_turn = next(event for event in events if event["type"] == "react_turn_completed")
        self.assertEqual(completed_turn["usage"]["totalTokens"], 225)
        completed = next(event for event in events if event["type"] == "react_completed")
        self.assertEqual(completed["reasoningUsage"], result["reasoningUsage"])
        self.assertEqual(
            [tool["name"] for tool in captured["tools"]],
            ["search_web", "search_visual_references", "select_visual_references"],
        )
        self.assertEqual(result["prompt"], "Fast final prompt")

    def test_image_agent_does_not_attribute_account_cost_delta_to_response(self):
        controller = app.AppController.__new__(app.AppController)

        def stream_response(*_args, **kwargs):
            text = "已完成分析。\n<<<FINAL_PROMPT>>>Final prompt"
            kwargs["on_delta"](text)
            kwargs["on_completed"](
                {"output": [], "usage": {"input_tokens": 200, "output_tokens": 50}}
            )
            return text

        controller.client = SimpleNamespace(
            stream_response=stream_response,
            get_json=Mock(return_value={"usage": {"total": {"cost": 999.0}}}),
        )

        result = controller._run_image_prompt_agent(
            {"base_url": "https://example.test/v1"},
            "secret",
            "Create an image",
            (),
            "flash",
            False,
            lambda *_args, **_kwargs: None,
        )

        self.assertEqual(result["reasoningUsage"]["totalTokens"], 250)
        self.assertEqual(result["reasoningUsage"]["callCount"], 1)
        self.assertEqual(result["reasoningUsage"]["costedCallCount"], 1)
        self.assertEqual(result["reasoningUsage"]["estimatedCallCount"], 1)
        self.assertEqual(result["reasoningUsage"]["costSource"], "estimate")
        self.assertTrue(result["reasoningUsage"]["hasCost"])
        self.assertAlmostEqual(result["reasoningUsage"]["costUsd"], 0.0001)
        controller.client.get_json.assert_not_called()

    def test_extra_agent_uses_terra_xhigh_with_max_depth_logic(self):
        controller = app.AppController.__new__(app.AppController)
        captured = {}

        def stream_response(*args, **kwargs):
            captured["model"] = args[2]
            captured["instructions"] = args[3]
            captured["reasoning_effort"] = kwargs["reasoning_effort"]
            text = "已完成充分核对。\n<<<FINAL_PROMPT>>>Extra final prompt"
            kwargs["on_delta"](text)
            kwargs["on_completed"]({"output": []})
            return text

        controller.client = SimpleNamespace(stream_response=stream_response)
        result = controller._run_image_prompt_agent(
            {"base_url": "https://example.test/v1"},
            "secret",
            "Create a thoroughly checked image",
            (),
            "extra",
            False,
            lambda *_args, **_kwargs: None,
        )

        self.assertEqual(captured["model"], "gpt-5.6-terra")
        self.assertEqual(captured["reasoning_effort"], "xhigh")
        self.assertIn(app.IMAGE_REASONING_MODES["max"]["depth"], captured["instructions"])
        self.assertEqual(result["prompt"], "Extra final prompt")

    def test_react_continuation_plans_assets_then_loads_only_selected_older_image(self):
        with tempfile.TemporaryDirectory() as temp:
            parent_path = Path(temp) / "parent.png"
            older_path = Path(temp) / "older.png"
            Image.new("RGB", (8, 8), "purple").save(parent_path)
            Image.new("RGB", (8, 8), "silver").save(older_path)
            parent_asset = app.image_asset_record(parent_path)
            older_asset = {
                **app.image_asset_record(older_path),
                "description": "银色城堡大厅背景",
            }
            context = {
                "history": [
                    {
                        "setId": "set-1",
                        "userPrompt": "设计主角",
                        "reasoningSummary": "采用紫色披风。",
                        "outputAssetIds": [parent_asset["assetId"]],
                    }
                ],
                "assets": [older_asset, parent_asset],
            }
            controller = app.AppController.__new__(app.AppController)
            response_inputs = []
            response_tools = []
            response_index = __import__("itertools").count()

            def stream_response(*args, **kwargs):
                response_inputs.append(__import__("copy").deepcopy(args[4]))
                response_tools.append(kwargs.get("tools"))
                if next(response_index) == 0:
                    kwargs["on_completed"](
                        {
                            "output": [
                                {
                                    "type": "function_call",
                                    "name": "plan_image_continuation",
                                    "call_id": "call-plan",
                                    "arguments": json.dumps(
                                        {
                                            "operation": "generate",
                                            "selected_asset_ids": [
                                                older_asset["assetId"],
                                                parent_asset["assetId"],
                                            ],
                                            "descriptions": [
                                                {
                                                    "asset_id": parent_asset["assetId"],
                                                    "description": "紫色披风角色正面图",
                                                }
                                            ],
                                            "rationale": "保留角色和既有场景语言。",
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                            ]
                        }
                    )
                    return ""
                text = "已核对角色和场景。\n<<<FINAL_PROMPT>>>角色走入雨夜车站"
                kwargs["on_delta"](text)
                kwargs["on_completed"]({"output": []})
                return text

            controller.client = SimpleNamespace(stream_response=stream_response)
            result = controller._run_image_prompt_agent(
                {"base_url": "https://example.test/v1"},
                "secret",
                "让她走进雨夜车站",
                (parent_path,),
                "medium",
                False,
                lambda *_args, **_kwargs: None,
                context,
                [parent_asset],
            )

        self.assertEqual(response_tools[0][0]["name"], "plan_image_continuation")
        self.assertIsNone(response_tools[1])
        first_content = response_inputs[0][0]["content"]
        self.assertEqual(sum(item["type"] == "input_image" for item in first_content), 1)
        plan_output = next(
            item
            for item in response_inputs[1]
            if item.get("type") == "function_call_output"
        )["output"]
        self.assertEqual(sum(item["type"] == "input_image" for item in plan_output), 1)
        self.assertEqual(result["continuationPlan"]["operation"], "generate")
        self.assertEqual(
            result["continuationPlan"]["selectedAssetIds"],
            [older_asset["assetId"], parent_asset["assetId"]],
        )

    def test_generate_image_high_uses_custom_visual_search_and_selected_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_path = root / "result.png"
            Image.new("RGB", (8, 8), "blue").save(output_path)
            service = SimpleNamespace(
                generate=__import__("unittest.mock").mock.Mock(
                    return_value={
                        "ok": True,
                        "path": str(output_path),
                        "uri": output_path.as_uri(),
                        "width": 8,
                        "height": 8,
                        "format": "png",
                        "actualSize": "8x8",
                    }
                )
            )
            candidate = {
                "id": "webref-shanghai",
                "title": "Shanghai Tower exterior",
                "caption": "Glass facade and twisting silhouette",
                "imageUrl": "https://images.example/shanghai.jpg",
                "thumbnailUrl": "https://thumbs.example/shanghai.jpg",
                "sourceUrl": "https://source.example/shanghai",
                "width": 1200,
                "height": 1800,
                "provider": "Bing Images",
                "previewDataUrl": "data:image/jpeg;base64,cHJldmlldw==",
                "_previewBytes": b"preview",
            }
            search_service = SimpleNamespace(
                search_web=__import__("unittest.mock").mock.Mock(),
                search_visual_references=__import__("unittest.mock").mock.Mock(
                    return_value={"query": "Shanghai Tower exterior", "results": [candidate]}
                ),
                stage_reference_records=__import__("unittest.mock").mock.Mock(),
            )

            def stage_reference_records(_candidates, target_dir, _max_count):
                target_dir.mkdir(parents=True, exist_ok=True)
                reference_path = target_dir / "reference-1.jpg"
                Image.new("RGB", (16, 16), "silver").save(reference_path, format="JPEG")
                return [{
                    "id": candidate["id"],
                    "title": candidate["title"],
                    "caption": candidate["caption"],
                    "provider": candidate["provider"],
                    "sourceUrl": candidate["sourceUrl"],
                    "imageUrl": candidate["imageUrl"],
                    "path": str(reference_path),
                }]

            search_service.stage_reference_records.side_effect = stage_reference_records
            controller = app.AppController.__new__(app.AppController)
            controller.image_generator = service
            controller.web_search = search_service
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {
                    "id": key_id,
                    "base_url": "https://example.test/v1",
                },
                get_secret=lambda _key_id: "secret",
            )
            response_inputs = []
            response_tools = []
            response_instructions = []
            response_index = __import__("itertools").count()

            def stream_response(*args, **kwargs):
                response_inputs.append(args[4])
                response_tools.append(kwargs.get("tools"))
                response_instructions.append(args[3])
                index = next(response_index)
                if index == 0:
                    kwargs["on_completed"](
                        {
                            "output": [
                                {
                                    "type": "function_call",
                                    "name": "search_visual_references",
                                    "call_id": "call-search",
                                    "arguments": json.dumps(
                                        {"query": "Shanghai Tower exterior", "max_results": 6}
                                    ),
                                }
                            ]
                        }
                    )
                    return ""
                if index == 1:
                    kwargs["on_completed"](
                        {
                            "output": [
                                {
                                    "type": "function_call",
                                    "name": "select_visual_references",
                                    "call_id": "call-select",
                                    "arguments": json.dumps(
                                        {
                                            "reference_ids": ["webref-shanghai"],
                                            "rationale": "The facade and silhouette improve accuracy.",
                                        }
                                    ),
                                }
                            ]
                        }
                    )
                    return ""
                text = (
                    "环境：真实建筑写实创作\n"
                    "参考：采用已核验的外观轮廓\n"
                    "<<<FINAL_PROMPT>>>Photorealistic Shanghai Tower with twisting glass facade"
                )
                kwargs["on_delta"](text)
                kwargs["on_completed"](
                    {
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": text}],
                            }
                        ]
                    }
                )
                return text

            controller.client = SimpleNamespace(
                stream_response=__import__("unittest.mock").mock.Mock(
                    side_effect=stream_response
                )
            )
            events = []
            data_root = root / "data"
            with patch.object(controller_image_reasoning, "app_data_dir", return_value=data_root), patch.object(
                controller_image_files, "generated_pictures_dir", return_value=root / "Pictures"
            ):
                result = controller.generate_image(
                    "key-1",
                    "Create an accurate Shanghai Tower exterior",
                    [],
                    {"requestId": "react-high-1", "reasoningMode": "high"},
                    event_callback=events.append,
                )

        self.assertTrue(result["ok"])
        self.assertTrue(result["webSearchEnabled"])
        self.assertTrue(result["webSearchUsed"])
        self.assertEqual(result["webSearchResultCount"], 1)
        self.assertFalse(result["webSearchFailed"])
        self.assertEqual(result["webReferenceCount"], 1)
        self.assertEqual(result["referenceCount"], 1)
        self.assertEqual(len(response_inputs), 3)
        self.assertTrue(all("必须保留其名称、身份" in value for value in response_instructions))
        self.assertTrue(all("不要仅因对象属于知名 IP 就改写为原创角色" in value for value in response_instructions))
        self.assertTrue(response_tools[0])
        self.assertTrue(response_tools[1])
        self.assertIsNone(response_tools[2])
        first_tool_output = next(
            item for item in response_inputs[1] if item.get("type") == "function_call_output"
        )
        self.assertTrue(
            any(content.get("type") == "input_image" for content in first_tool_output["output"])
        )
        selected_tool_output = next(
            item
            for item in response_inputs[2]
            if item.get("type") == "function_call_output" and item.get("call_id") == "call-select"
        )
        self.assertEqual(json.loads(selected_tool_output["output"])["acceptedCount"], 1)
        generation_request = service.generate.call_args.args[2]
        self.assertEqual(len(generation_request.image_paths), 1)
        self.assertEqual(generation_request.operation, "generate")
        self.assertIn("image-search-references", str(generation_request.image_paths[0]))
        self.assertFalse((data_root / "image-search-references" / "react-high-1").exists())
        event_types = [event["type"] for event in events]
        self.assertIn("react_visual_results", event_types)
        self.assertIn("react_visual_selected", event_types)
        set_started = next(event for event in events if event["type"] == "set_started")
        self.assertEqual(set_started["operation"], "generate")
        self.assertEqual(set_started["webReferences"][0]["title"], "Shanghai Tower exterior")
        self.assertTrue(set_started["webReferences"][0]["previewUri"].startswith("data:image/jpeg;base64,"))
        self.assertIn("Pictures", set_started["webReferences"][0]["path"])

    def test_generate_image_cleans_staged_web_references_after_unexpected_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_root = root / "data"
            request_id = "react-high-error"
            candidate = {"id": "webref-error"}
            controller = app.AppController.__new__(app.AppController)
            controller.active_image_sets = set()
            controller.image_generator = SimpleNamespace(
                generate=__import__("unittest.mock").mock.Mock(
                    side_effect=TypeError("unexpected generator failure")
                )
            )
            controller.store = SimpleNamespace(
                get_key_record=lambda key_id: {
                    "id": key_id,
                    "base_url": "https://example.test/v1",
                },
                get_secret=lambda _key_id: "secret",
            )
            controller._run_image_prompt_agent = __import__("unittest.mock").mock.Mock(
                return_value={
                    "prompt": "Final prompt",
                    "summary": "Checked a visual reference",
                    "model": "gpt-5.6-sol",
                    "webSearchEnabled": True,
                    "webSearchUsed": True,
                    "webSearchResultCount": 1,
                    "webCandidates": [candidate],
                }
            )

            def stage_reference_records(_candidates, target_dir, _max_count):
                target_dir.mkdir(parents=True, exist_ok=True)
                reference_path = target_dir / "reference-1.jpg"
                Image.new("RGB", (8, 8), "silver").save(reference_path, format="JPEG")
                return [{"id": "webref-error", "path": str(reference_path)}]

            controller.web_search = SimpleNamespace(stage_reference_records=stage_reference_records)
            with patch.object(controller_image_reasoning, "app_data_dir", return_value=data_root), patch.object(
                controller_image_files, "generated_pictures_dir", return_value=root / "Pictures"
            ):
                with self.assertRaisesRegex(TypeError, "unexpected generator failure"):
                    controller.generate_image(
                        "key-1",
                        "Create an accurate landmark",
                        [],
                        {"requestId": request_id, "reasoningMode": "high"},
                    )

            self.assertNotIn(request_id, controller.active_image_sets)
            self.assertFalse((data_root / "image-search-references" / request_id).exists())

    def test_image_agent_ignores_unknown_visual_reference_ids(self):
        candidate = {
            "id": "webref-known",
            "title": "Known reference",
            "imageUrl": "https://images.example/known.jpg",
            "thumbnailUrl": "https://thumbs.example/known.jpg",
            "sourceUrl": "https://source.example/known",
            "provider": "Bing Images",
            "previewDataUrl": "data:image/jpeg;base64,cHJldmlldw==",
        }
        controller = app.AppController.__new__(app.AppController)
        controller.web_search = SimpleNamespace(
            search_visual_references=lambda *_args, **_kwargs: {
                "query": "known subject",
                "results": [candidate],
            }
        )
        response_inputs = []
        response_index = __import__("itertools").count()

        def stream_response(*args, **kwargs):
            response_inputs.append(__import__("copy").deepcopy(args[4]))
            index = next(response_index)
            if index == 0:
                kwargs["on_completed"](
                    {
                        "output": [{
                            "type": "function_call",
                            "name": "search_visual_references",
                            "call_id": "call-search",
                            "arguments": '{"query":"known subject"}',
                        }]
                    }
                )
                return ""
            if index == 1:
                kwargs["on_completed"](
                    {
                        "output": [{
                            "type": "function_call",
                            "name": "select_visual_references",
                            "call_id": "call-select",
                            "arguments": '{"reference_ids":["webref-unknown"]}',
                        }]
                    }
                )
                return ""
            text = "No candidate adopted\n<<<FINAL_PROMPT>>>Final prompt without a web reference"
            kwargs["on_delta"](text)
            kwargs["on_completed"]({"output": []})
            return text

        controller.client = SimpleNamespace(stream_response=stream_response)
        events = []
        result = controller._run_image_prompt_agent(
            {"base_url": "https://example.test/v1"},
            "secret",
            "Create the subject",
            (),
            "high",
            True,
            lambda event_type, **details: events.append({"type": event_type, **details}),
        )

        selection_output = next(
            item
            for item in response_inputs[2]
            if item.get("type") == "function_call_output" and item.get("call_id") == "call-select"
        )
        self.assertEqual(json.loads(selection_output["output"])["acceptedCount"], 0)
        self.assertEqual(result["webSearchResultCount"], 1)
        self.assertEqual(result["webCandidates"], [])
        selected_event = next(event for event in events if event["type"] == "react_visual_selected")
        self.assertEqual(selected_event["selectedCount"], 0)

    def test_image_agent_returns_search_error_to_model_and_continues(self):
        controller = app.AppController.__new__(app.AppController)
        controller.web_search = SimpleNamespace(
            search_visual_references=__import__("unittest.mock").mock.Mock(
                side_effect=RuntimeError("search temporarily unavailable")
            )
        )
        response_inputs = []
        response_index = __import__("itertools").count()

        def stream_response(*args, **kwargs):
            response_inputs.append(args[4])
            if next(response_index) == 0:
                kwargs["on_completed"](
                    {
                        "output": [{
                            "type": "function_call",
                            "name": "search_visual_references",
                            "call_id": "call-failed-search",
                            "arguments": '{"query":"subject"}',
                        }]
                    }
                )
                return ""
            text = "Search unavailable; proceeding from the request\n<<<FINAL_PROMPT>>>Fallback final prompt"
            kwargs["on_delta"](text)
            kwargs["on_completed"]({"output": []})
            return text

        controller.client = SimpleNamespace(stream_response=stream_response)
        events = []
        result = controller._run_image_prompt_agent(
            {"base_url": "https://example.test/v1"},
            "secret",
            "Create the subject",
            (),
            "high",
            True,
            lambda event_type, **details: events.append({"type": event_type, **details}),
        )

        failed_output = next(
            item
            for item in response_inputs[1]
            if item.get("type") == "function_call_output"
        )
        self.assertFalse(json.loads(failed_output["output"])["ok"])
        self.assertEqual(result["prompt"], "Fallback final prompt")
        self.assertTrue(result["webSearchUsed"])
        self.assertTrue(result["webSearchFailed"])
        self.assertEqual(result["webCandidates"], [])
        self.assertIn("react_tool_failed", [event["type"] for event in events])

    def test_image_agent_flushes_turn_text_before_tool_event(self):
        controller = app.AppController.__new__(app.AppController)
        controller.web_search = SimpleNamespace(
            search_web=__import__("unittest.mock").mock.Mock(
                return_value={"query": "Siamese cat", "results": []}
            )
        )
        response_index = __import__("itertools").count()

        def stream_response(*_args, **kwargs):
            if next(response_index) == 0:
                kwargs["on_delta"]("先核对产品外观。")
                kwargs["on_completed"]({
                    "output": [{
                        "type": "function_call",
                        "name": "search_web",
                        "call_id": "call-search",
                        "arguments": '{"query":"Siamese cat","max_results":5}',
                    }]
                })
                return "先核对产品外观。"
            text = "已完成核对。\n<<<FINAL_PROMPT>>>Final image prompt"
            kwargs["on_delta"](text)
            kwargs["on_completed"]({"output": []})
            return text

        controller.client = SimpleNamespace(stream_response=stream_response)
        events = []
        result = controller._run_image_prompt_agent(
            {"base_url": "https://example.test/v1"},
            "secret",
            "Create a product image",
            (),
            "medium",
            True,
            lambda event_type, **details: events.append({"type": event_type, **details}),
        )

        tool_index = next(
            index for index, event in enumerate(events)
            if event["type"] == "react_tool_started"
        )
        visible_before_tool = "".join(
            event.get("delta", "")
            for event in events[:tool_index]
            if event["type"] == "react_turn_delta"
        )
        first_turn = next(
            event for event in events[:tool_index]
            if event["type"] == "react_turn_completed"
        )
        self.assertEqual(visible_before_tool, "先核对产品外观。")
        self.assertEqual(first_turn["text"], "先核对产品外观。")
        self.assertEqual(result["prompt"], "Final image prompt")

    def test_image_agent_runs_same_turn_visual_searches_concurrently(self):
        controller = app.AppController.__new__(app.AppController)
        barrier = __import__("threading").Barrier(2, timeout=2)
        search_threads = []

        def search_visual_references(query, _max_results):
            search_threads.append(__import__("threading").get_ident())
            barrier.wait()
            candidate_id = f"webref-{query}"
            return {
                "query": query,
                "results": [{
                    "id": candidate_id,
                    "title": f"Reference {query}",
                    "imageUrl": f"https://images.example/{query}.jpg",
                    "thumbnailUrl": f"https://thumbs.example/{query}.jpg",
                    "sourceUrl": f"https://source.example/{query}",
                    "provider": "Test Search",
                    "previewDataUrl": "data:image/jpeg;base64,cHJldmlldw==",
                }],
            }

        controller.web_search = SimpleNamespace(
            search_visual_references=search_visual_references,
        )
        response_inputs = []
        response_index = __import__("itertools").count()

        def stream_response(*args, **kwargs):
            response_inputs.append(__import__("copy").deepcopy(args[4]))
            index = next(response_index)
            if index == 0:
                kwargs["on_completed"]({
                    "output": [
                        {
                            "type": "function_call",
                            "name": "search_visual_references",
                            "call_id": "call-a",
                            "arguments": '{"query":"alpha"}',
                        },
                        {
                            "type": "function_call",
                            "name": "search_visual_references",
                            "call_id": "call-b",
                            "arguments": '{"query":"beta"}',
                        },
                    ]
                })
                return ""
            if index == 1:
                kwargs["on_completed"]({
                    "output": [{
                        "type": "function_call",
                        "name": "select_visual_references",
                        "call_id": "call-select",
                        "arguments": '{"reference_ids":["webref-alpha","webref-beta"]}',
                    }]
                })
                return ""
            text = "Compared both searches\n<<<FINAL_PROMPT>>>Final prompt with two references"
            kwargs["on_delta"](text)
            kwargs["on_completed"]({"output": []})
            return text

        controller.client = SimpleNamespace(stream_response=stream_response)
        result = controller._run_image_prompt_agent(
            {"base_url": "https://example.test/v1"},
            "secret",
            "Create a comparison",
            (),
            "high",
            True,
            lambda *_args, **_kwargs: None,
        )

        self.assertEqual(len(set(search_threads)), 2)
        self.assertEqual(result["webSearchResultCount"], 2)
        self.assertEqual(
            [candidate["id"] for candidate in result["webCandidates"]],
            ["webref-alpha", "webref-beta"],
        )
        first_round_outputs = [
            item for item in response_inputs[1] if item.get("type") == "function_call_output"
        ]
        self.assertEqual([item["call_id"] for item in first_round_outputs], ["call-a", "call-b"])

    def test_github_request_retries_without_system_proxy_when_proxy_refuses(self):
        request = app.urllib.request.Request("https://api.github.com/test")
        response = object()
        direct_opener = SimpleNamespace(open=__import__("unittest.mock").mock.Mock(return_value=response))
        refused = app.urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

        with patch("app.urllib.request.urlopen", side_effect=refused), patch(
            "app.urllib.request.build_opener", return_value=direct_opener
        ) as build_opener:
            result = app.open_url_with_direct_fallback(request, timeout=7)

        self.assertIs(result, response)
        proxy_handler = build_opener.call_args.args[0]
        self.assertEqual(proxy_handler.proxies, {})
        direct_request = direct_opener.open.call_args.args[0]
        self.assertIsNot(direct_request, request)
        self.assertEqual(direct_request.full_url, request.full_url)
        direct_opener.open.assert_called_once_with(direct_request, timeout=7)

    def test_github_request_does_not_mask_http_errors_with_direct_retry(self):
        request = app.urllib.request.Request("https://api.github.com/test")
        http_error = app.urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, None)

        with patch("app.urllib.request.urlopen", side_effect=http_error), patch(
            "app.urllib.request.build_opener"
        ) as build_opener:
            with self.assertRaises(app.urllib.error.HTTPError):
                app.open_url_with_direct_fallback(request, timeout=7)

        build_opener.assert_not_called()

    def test_github_request_does_not_duplicate_normal_timeout(self):
        request = app.urllib.request.Request("https://api.github.com/test")
        timeout = TimeoutError("timed out")

        with patch("app.urllib.request.urlopen", side_effect=timeout), patch(
            "app.urllib.request.build_opener"
        ) as build_opener:
            with self.assertRaises(TimeoutError):
                app.open_url_with_direct_fallback(request, timeout=7)

        build_opener.assert_not_called()

    def test_curl_get_uses_system_proxy_for_github_metadata(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=b'{"tag_name":"v9.9.9"}\n200',
            stderr=b"",
        )
        request = app.urllib.request.Request("https://api.github.com/releases")

        with patch("app.shutil.which", return_value="curl.exe"), patch(
            "app.urllib.request.getproxies", return_value={"https": "http://127.0.0.1:7890"}
        ), patch("app.subprocess.run", return_value=completed) as run:
            payload = app.curl_get_bytes(request, timeout=8)

        self.assertEqual(payload, b'{"tag_name":"v9.9.9"}')
        self.assertEqual(run.call_args.kwargs["env"]["HTTPS_PROXY"], "http://127.0.0.1:7890")
        self.assertIn("--http1.1", run.call_args.args[0])
        self.assertIn("--retry-all-errors", run.call_args.args[0])
        max_time_index = run.call_args.args[0].index("--max-time")
        self.assertEqual(run.call_args.args[0][max_time_index + 1], "4")

    def test_curl_get_preserves_http_rate_limit_status(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=b'{"message":"API rate limit exceeded"}\n403',
            stderr=b"",
        )
        request = app.urllib.request.Request("https://api.github.com/releases")

        with patch("app.shutil.which", return_value="curl.exe"), patch(
            "app.urllib.request.getproxies", return_value={}
        ), patch("app.subprocess.run", return_value=completed):
            with self.assertRaises(app.urllib.error.HTTPError) as raised:
                app.curl_get_bytes(request, timeout=8)

        self.assertEqual(raised.exception.code, 403)

    def test_small_github_request_does_not_retry_python_after_curl_tls_failure(self):
        request = app.urllib.request.Request("https://api.github.com/releases")

        with patch.object(
            backend_platform,
            "curl_get_bytes",
            side_effect=app.NetworkTransportError("TLS connection closed"),
        ), patch.object(backend_platform, "open_url_with_direct_fallback") as python_transport:
            with self.assertRaises(app.NetworkTransportError):
                app.get_small_url_bytes(request, timeout=8)

        python_transport.assert_not_called()

    def test_update_check_skips_latest_api_after_tls_transport_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = app.AppController.__new__(app.AppController)
            controller.store = app.Store(Path(temp) / "test.db")
            controller.update_lock = __import__("threading").Lock()
            controller.update_state = {}
            controller.window = None
            controller.visible = False
            controller._github_json = __import__("unittest.mock").mock.Mock(
                side_effect=app.NetworkTransportError("SSL UNEXPECTED_EOF_WHILE_READING")
            )
            controller._github_release_feed = __import__("unittest.mock").mock.Mock(
                return_value=[
                    {
                        "tag_name": "imagen-v9.9.9",
                        "target_commitish": "imagen",
                        "body": "- TLS 后备成功",
                        "draft": False,
                        "prerelease": False,
                        "assets": [
                            {
                                "name": app.RELEASE_ASSET_NAME,
                                "browser_download_url": "https://github.com/download/exe",
                            }
                        ],
                    }
                ]
            )

            controller._check_for_updates_worker(manual=True)

            controller._github_json.assert_called_once_with("/releases?per_page=20")
            controller._github_release_feed.assert_called_once_with()
            self.assertEqual(controller.update_state["status"], "available")
            self.assertIn("TLS 后备成功", controller.update_state["releaseNotes"])

    def test_update_check_hides_transport_details_when_all_routes_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = app.AppController.__new__(app.AppController)
            controller.store = app.Store(Path(temp) / "test.db")
            controller.update_lock = __import__("threading").Lock()
            controller.update_state = {}
            controller.window = None
            controller.visible = False
            controller._github_json = __import__("unittest.mock").mock.Mock(
                side_effect=app.NetworkTransportError("SSL UNEXPECTED_EOF_WHILE_READING")
            )
            controller._github_release_feed = __import__("unittest.mock").mock.Mock(
                side_effect=app.NetworkTransportError("curl schannel failed")
            )

            controller._check_for_updates_worker(manual=True)

            self.assertEqual(controller.update_state["status"], "failed")
            self.assertEqual(
                controller.update_state["message"],
                "检查更新失败：无法连接 GitHub，请检查网络或代理后重试",
            )
            self.assertNotIn("SSL", controller.update_state["message"])

    def test_manual_refresh_returns_valid_result_then_enforces_five_second_cooldown(self):
        controller = app.AppController.__new__(app.AppController)
        controller.manual_refresh_lock = __import__("threading").Lock()
        controller.manual_refresh_available_at = 0.0
        controller.refresh_lock = __import__("threading").Lock()
        controller.store = SimpleNamespace(list_key_records=lambda: [{"id": "key-1"}])
        controller._refresh_key = __import__("unittest.mock").mock.Mock(return_value=True)
        controller.get_state = lambda: {"keys": ["key-1"]}

        with patch("app.time.monotonic", side_effect=[100.0, 102.2, 103.0]):
            result = controller.refresh_now()
            cooling = controller.refresh_now()

        self.assertTrue(result["ok"])
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["cooldownSeconds"], 2.8)
        self.assertEqual(result["debug"]["outcome"], "success")
        self.assertEqual(result["debug"]["traceId"], "backend-refresh")
        self.assertIn(
            "manual-lock-released",
            [event["event"] for event in result["debug"]["events"]],
        )
        self.assertEqual(cooling["cooldownSeconds"], 2.0)
        self.assertEqual(cooling["error"], "手动刷新冷却中")
        controller._refresh_key.assert_called_once_with("key-1")

    def test_manual_refresh_returns_busy_instead_of_waiting_for_background_refresh(self):
        controller = app.AppController.__new__(app.AppController)
        controller.manual_refresh_lock = __import__("threading").Lock()
        controller.manual_refresh_available_at = 0.0
        controller.refresh_lock = __import__("threading").Lock()
        controller.refresh_lock.acquire()

        try:
            with patch("app.time.monotonic", return_value=100.0):
                result = controller.refresh_now()
        finally:
            controller.refresh_lock.release()

        self.assertFalse(result["ok"])
        self.assertTrue(result["busy"])
        self.assertEqual(result["cooldownSeconds"], 0)
        self.assertEqual(result["error"], "后台刷新正在进行")
        self.assertEqual(controller.manual_refresh_available_at, 0.0)

    def test_manual_refresh_does_not_extend_cooldown_after_slow_response(self):
        controller = app.AppController.__new__(app.AppController)
        controller.manual_refresh_lock = __import__("threading").Lock()
        controller.manual_refresh_available_at = 0.0
        controller.refresh_lock = __import__("threading").Lock()
        controller.store = SimpleNamespace(list_key_records=lambda: [{"id": "key-1"}])
        controller._refresh_key = __import__("unittest.mock").mock.Mock(return_value=True)
        controller.get_state = lambda: {"keys": ["key-1"]}

        with patch("app.time.monotonic", side_effect=[100.0, 106.0]):
            result = controller.refresh_now()

        self.assertTrue(result["valid"])
        self.assertEqual(result["cooldownSeconds"], 0)
        self.assertEqual(controller.manual_refresh_available_at, 105.0)

    def test_minimize_posts_native_message_without_sync_webview_call(self):
        mock = __import__("unittest.mock").mock
        controller = app.AppController.__new__(app.AppController)
        controller.window = SimpleNamespace(minimize=mock.Mock())
        controller.visible = True
        controller.maximized = False
        controller.refresh_wakeup = __import__("threading").Event()

        with patch.object(controller, "_window_handle", return_value=1234), patch.object(
            app.user32, "PostMessageW", return_value=True
        ) as post_message:
            result = controller.window_action("minimize")

        self.assertTrue(result["ok"])
        self.assertFalse(controller.visible)
        self.assertTrue(controller.refresh_wakeup.is_set())
        post_message.assert_called_once_with(1234, app.WM_SYSCOMMAND, app.SC_MINIMIZE, 0)
        controller.window.minimize.assert_not_called()

    def test_window_size_save_deduplicates_and_skips_maximized_resize(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            set_window_size=lambda width, height: {"width": width, "height": height}
        )
        controller._last_saved_window_size = (920, 680)
        controller._window_size_lock = __import__("threading").Lock()

        saved = controller.set_window_size(1200.4, 800.6)
        duplicate = controller.set_window_size(1200, 801)

        self.assertEqual(
            saved,
            {
                "ok": True,
                "windowSize": {"width": 1200, "height": 801},
                "changed": True,
            },
        )
        self.assertEqual(
            duplicate,
            {
                "ok": True,
                "windowSize": {"width": 1200, "height": 801},
                "changed": False,
            },
        )

        controller._pending_window_size = None
        controller._window_size_timer = None
        controller._window_is_normal = Mock(return_value=False)
        controller._schedule_window_size_save(1920, 1080)

        self.assertIsNone(controller._pending_window_size)

    def test_ui_controller_forwards_window_size_to_background(self):
        controller = app.UiController.__new__(app.UiController)
        controller.rpc_client = SimpleNamespace(
            call=Mock(
                return_value={
                    "ok": True,
                    "windowSize": {"width": 1200, "height": 800},
                }
            )
        )
        controller._last_saved_window_size = (920, 680)

        result = controller.set_window_size(1200, 800)

        self.assertEqual(result["windowSize"], {"width": 1200, "height": 800})
        controller.rpc_client.call.assert_called_once_with("set_window_size", 1200, 800)
        self.assertEqual(controller._last_saved_window_size, (1200, 800))

    def test_run_ui_process_uses_saved_window_size(self):
        class FakeAssetCache:
            main_page = Path("cached.html")

            @staticmethod
            def is_ready():
                return True

        rpc_client = SimpleNamespace(
            call=Mock(
                return_value={
                    "titleBarMode": "default",
                    "alwaysOnTop": False,
                    "windowSize": {"width": 1234, "height": 777},
                }
            )
        )
        window = SimpleNamespace()
        controller = SimpleNamespace(bind_window=Mock())

        with patch.object(backend_runtime, "ControllerRpcClient", return_value=rpc_client), patch.object(
            backend_runtime, "StaticAssetCache", return_value=FakeAssetCache()
        ), patch.object(backend_runtime, "UiController", return_value=controller), patch.object(
            backend_runtime, "RemoteWebApi", return_value=object()
        ), patch.object(backend_runtime.webview, "create_window", return_value=window) as create_window, patch.object(
            backend_runtime.webview, "start"
        ):
            app.run_ui_process("pipe", b"auth")

        self.assertEqual(create_window.call_args.kwargs["width"], 1234)
        self.assertEqual(create_window.call_args.kwargs["height"], 777)
        controller.bind_window.assert_called_once_with(window)

    def test_ui_controller_sets_native_always_on_top_and_syncs_background(self):
        mock = __import__("unittest.mock").mock
        controller = app.UiController.__new__(app.UiController)
        controller.window = SimpleNamespace()
        controller.rpc_client = SimpleNamespace(
            call=mock.Mock(return_value={"ok": True, "alwaysOnTop": True})
        )

        with patch.object(controller, "_window_handle", return_value=123), patch.object(
            app.user32, "SetWindowPos", return_value=True
        ) as set_window_pos:
            result = controller.set_always_on_top(True)

        self.assertEqual(result, {"ok": True, "alwaysOnTop": True})
        set_window_pos.assert_called_once_with(
            123,
            app.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            app.SWP_NOMOVE | app.SWP_NOSIZE | app.SWP_NOACTIVATE,
        )
        controller.rpc_client.call.assert_called_once_with("set_always_on_top", True)

    def test_always_on_top_buttons_follow_active_title_bar_mode(self):
        page = frontend_source()
        scss = frontend_scss_source()

        self.assertIn('id="titlebarPinButton"', page)
        self.assertIn('id="toolbarPinButton"', page)
        self.assertIn("toggleAlwaysOnTop", page)
        self.assertIn("window.applyAlwaysOnTopState", page)
        controls_start = page.index('<div class="window-controls">')
        title_pin = page.index('id="titlebarPinButton"')
        minimize = page.index("requestWindowAction('minimize')")
        self.assertLess(controls_start, title_pin)
        self.assertLess(title_pin, minimize)
        self.assertIn("#keyToolbar .toolbar-pin-button { display: none; }", scss)
        self.assertIn(".always-on-top-button:hover [data-lucide]", scss)
        self.assertIn(".always-on-top-button:hover svg", scss)
        self.assertIn("stroke: var(--brand)", scss)
        self.assertIn("background: transparent !important", scss)
        self.assertIn(".window-controls .titlebar-pin-button", scss)
        self.assertNotIn(".titlebar-pin-button:hover { background:", scss)
        self.assertIn("#widget-root.titlebar-original", scss)
        self.assertIn(".toolbar-pin-button", scss)

    def test_minimized_window_restore_skips_expensive_ui_state_push(self):
        mock = __import__("unittest.mock").mock
        controller = app.AppController.__new__(app.AppController)
        controller.visible = False
        controller.maximized = False
        controller.refresh_wakeup = __import__("threading").Event()
        controller._set_window_corner = mock.Mock()
        controller._push_window_state = mock.Mock()

        controller._on_restored()

        self.assertTrue(controller.visible)
        self.assertFalse(controller.maximized)
        self.assertTrue(controller.refresh_wakeup.is_set())
        controller._set_window_corner.assert_not_called()
        controller._push_window_state.assert_not_called()

    def test_maximized_window_restore_still_updates_window_state(self):
        mock = __import__("unittest.mock").mock
        controller = app.AppController.__new__(app.AppController)
        controller.visible = True
        controller.maximized = True
        controller.refresh_wakeup = __import__("threading").Event()
        controller._set_window_corner = mock.Mock()
        controller._push_window_state = mock.Mock()

        controller._on_restored()

        self.assertFalse(controller.maximized)
        controller._set_window_corner.assert_called_once_with(False)
        controller._push_window_state.assert_called_once_with()

    def test_tray_close_action_defers_window_hide_outside_api_callback(self):
        mock = __import__("unittest.mock").mock
        controller = app.AppController.__new__(app.AppController)
        controller.hide_window = mock.Mock()
        scheduled = []

        class DeferredTimer:
            def __init__(self, delay, callback):
                scheduled.append((delay, callback))

            def start(self):
                return None

        with patch("app.threading.Timer", DeferredTimer):
            result = controller._handle_close_request("tray")

        self.assertEqual(result, "tray")
        controller.hide_window.assert_not_called()
        self.assertEqual(scheduled, [(0.01, controller.hide_window)])

    def test_hide_window_posts_to_native_ui_thread_without_sync_webview_hide(self):
        mock = __import__("unittest.mock").mock
        native_form = SimpleNamespace(BeginInvoke=mock.Mock(), Hide=mock.Mock())
        controller = app.AppController.__new__(app.AppController)
        controller.window = SimpleNamespace(native=native_form, hide=mock.Mock())
        controller.visible = True
        controller.refresh_wakeup = __import__("threading").Event()

        fake_system = SimpleNamespace(Action=lambda callback: callback)
        with patch.dict(sys.modules, {"System": fake_system}):
            controller.hide_window()

        self.assertFalse(controller.visible)
        self.assertTrue(controller.refresh_wakeup.is_set())
        native_form.BeginInvoke.assert_called_once_with(native_form.Hide)
        controller.window.hide.assert_not_called()

    def test_window_background_matches_theme_without_blocking_ui_thread(self):
        mock = __import__("unittest.mock").mock
        native_webview = SimpleNamespace(DefaultBackgroundColor=None)
        native_form = SimpleNamespace(
            BackColor=None,
            webview=native_webview,
            BeginInvoke=mock.Mock(side_effect=lambda callback: callback()),
        )
        controller = app.AppController.__new__(app.AppController)
        controller.window = SimpleNamespace(native=native_form)
        fake_system = SimpleNamespace(Action=lambda callback: callback)
        fake_drawing = SimpleNamespace(
            Color=SimpleNamespace(FromArgb=lambda *channels: channels),
            ColorTranslator=SimpleNamespace(FromHtml=lambda color: color),
        )

        with patch.dict(sys.modules, {"System": fake_system, "System.Drawing": fake_drawing}):
            light_result = controller.set_window_background("light")
            light_back_color = native_form.BackColor
            light_webview_color = native_webview.DefaultBackgroundColor
            dark_result = controller.set_window_background("dark")

        self.assertEqual(light_result, {"ok": True, "color": "#ffffff"})
        self.assertEqual(light_back_color, "#ffffff")
        self.assertEqual(light_webview_color, (255, 255, 255, 255))
        self.assertEqual(dark_result, {"ok": True, "color": "#020617"})
        self.assertEqual(native_form.BackColor, "#020617")
        self.assertEqual(native_webview.DefaultBackgroundColor, (255, 2, 6, 23))
        self.assertEqual(native_form.BeginInvoke.call_count, 2)

    def test_page_load_disables_native_webview_zoom_controls(self):
        mock = __import__("unittest.mock").mock
        settings = SimpleNamespace(IsZoomControlEnabled=True)
        native_webview = SimpleNamespace(CoreWebView2=SimpleNamespace(Settings=settings))
        native_form = SimpleNamespace(
            webview=native_webview,
            InvokeRequired=True,
            BeginInvoke=mock.Mock(side_effect=lambda callback: callback()),
        )
        controller = app.AppController.__new__(app.AppController)
        controller.window = SimpleNamespace(native=native_form)
        controller.asset_cache = SimpleNamespace(is_ready=lambda: False)

        with patch.dict(sys.modules, {"System": SimpleNamespace(Action=lambda callback: callback)}):
            controller._on_page_loaded()

        self.assertFalse(settings.IsZoomControlEnabled)
        native_form.BeginInvoke.assert_called_once()

    def test_start_workers_confirms_restarted_application_is_ready(self):
        mock = __import__("unittest.mock").mock
        controller = app.AppController.__new__(app.AppController)
        controller.window = None
        controller._set_window_corner = mock.Mock()
        controller.check_for_updates = mock.Mock()

        with tempfile.TemporaryDirectory() as temp:
            ready = Path(temp) / "update-restarted.ready"
            controller.restart_ready_path = str(ready)
            with patch("app.threading.Thread") as thread, patch("app.trace_startup"):
                controller.start_workers()

            self.assertEqual(ready.read_text(encoding="ascii"), str(__import__("os").getpid()))

        self.assertEqual(thread.call_count, 3)
        self.assertEqual(thread.return_value.start.call_count, 3)
        controller.check_for_updates.assert_called_once_with(manual=False)

    def test_automatic_update_check_respects_ignored_version(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = app.AppController.__new__(app.AppController)
            controller.store = app.Store(Path(temp) / "test.db")
            controller.store.set_ignored_update_version("imagen-v9.9.9")
            controller.update_lock = __import__("threading").Lock()
            controller.update_state = {}
            controller.window = None
            controller.visible = False
            controller._github_json = lambda _path: {
                "tag_name": "imagen-v9.9.9",
                "target_commitish": "imagen",
                "body": "## 更新日志",
                "assets": [{"name": app.RELEASE_ASSET_NAME, "url": "api", "browser_download_url": "web"}],
            }

            controller._check_for_updates_worker(manual=False)

            self.assertTrue(controller.update_state["available"])
            self.assertFalse(controller.update_state["showPrompt"])

    def test_manual_update_check_overrides_ignored_version(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = app.AppController.__new__(app.AppController)
            controller.store = app.Store(Path(temp) / "test.db")
            controller.store.set_ignored_update_version("imagen-v9.9.9")
            controller.update_lock = __import__("threading").Lock()
            controller.update_state = {}
            controller.window = None
            controller.visible = False
            controller._github_json = lambda _path: {
                "tag_name": "imagen-v9.9.9",
                "target_commitish": "imagen",
                "body": "## 更新日志",
                "assets": [{"name": app.RELEASE_ASSET_NAME, "url": "api", "browser_download_url": "web"}],
            }

            controller._check_for_updates_worker(manual=True)

            self.assertTrue(controller.update_state["available"])
            self.assertTrue(controller.update_state["showPrompt"])

    def test_update_check_falls_back_to_imagen_feed_when_list_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = app.AppController.__new__(app.AppController)
            controller.store = app.Store(Path(temp) / "test.db")
            controller.update_lock = __import__("threading").Lock()
            controller.update_state = {}
            controller.window = None
            controller.visible = False
            requested_paths = []

            def github_json(path):
                requested_paths.append(path)
                raise RuntimeError("release list unavailable")

            controller._github_json = github_json
            controller._github_release_feed = lambda: [
                {
                    "tag_name": "imagen-v9.9.9",
                    "target_commitish": "imagen",
                    "body": "- imagen 备用通道版本内容",
                    "assets": [
                        {
                            "name": app.RELEASE_ASSET_NAME,
                            "url": "api",
                            "browser_download_url": "web",
                        }
                    ],
                }
            ]
            controller._check_for_updates_worker(manual=True)

            self.assertEqual(requested_paths, ["/releases?per_page=20"])
            self.assertTrue(controller.update_state["available"])
            self.assertIn("imagen 备用通道版本内容", controller.update_state["releaseNotes"])

    def test_update_check_uses_atom_feed_when_github_api_is_rate_limited(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = app.AppController.__new__(app.AppController)
            controller.store = app.Store(Path(temp) / "test.db")
            controller.update_lock = __import__("threading").Lock()
            controller.update_state = {}
            controller.window = None
            controller.visible = False
            rate_limit_error = app.urllib.error.HTTPError(
                "https://api.github.com/releases", 403, "rate limit exceeded", {}, None
            )
            controller._github_json = __import__("unittest.mock").mock.Mock(
                side_effect=rate_limit_error
            )
            controller._github_release_feed = __import__("unittest.mock").mock.Mock(
                return_value=[
                    {
                        "tag_name": "imagen-v9.9.9",
                        "target_commitish": "imagen",
                        "body": "- 来自 Release feed",
                        "draft": False,
                        "prerelease": False,
                        "assets": [
                            {
                                "name": app.RELEASE_ASSET_NAME,
                                "url": "",
                                "browser_download_url": "https://github.com/download/exe",
                            }
                        ],
                    }
                ]
            )

            controller._check_for_updates_worker(manual=True)

            controller._github_json.assert_called_once_with("/releases?per_page=20")
            controller._github_release_feed.assert_called_once_with()
            self.assertTrue(controller.update_state["available"])
            self.assertIn("来自 Release feed", controller.update_state["releaseNotes"])

    def test_github_release_feed_builds_download_urls_and_markdown(self):
        payload = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <link rel="alternate" href="https://github.com/Binceenigne/easyapitool/releases/tag/imagen-v9.9.9" />
            <title>imagen-v9.9.9</title>
            <content type="html">&lt;h2&gt;9.9.9&lt;/h2&gt;&lt;ul&gt;&lt;li&gt;修复检查更新&lt;/li&gt;&lt;/ul&gt;</content>
          </entry>
          <entry>
            <link rel="alternate" href="https://github.com/Binceenigne/easyapitool/releases/tag/v9.9.8-beta" />
            <title>v9.9.8-beta</title>
          </entry>
        </feed>""".encode("utf-8")

        releases = app.parse_github_release_feed(payload)

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]["tag_name"], "imagen-v9.9.9")
        self.assertEqual(releases[0]["target_commitish"], "imagen")
        self.assertEqual(releases[0]["body"], "## 9.9.9\n- \u4fee\u590d\u68c0\u67e5\u66f4\u65b0")
        assets = {asset["name"]: asset for asset in releases[0]["assets"]}
        self.assertEqual(
            assets[app.RELEASE_ASSET_NAME]["browser_download_url"],
            f"https://github.com/{app.GITHUB_REPOSITORY}/releases/download/imagen-v9.9.9/{app.RELEASE_ASSET_NAME}",
        )
        self.assertIn(f"{app.RELEASE_ASSET_NAME}.sha256", assets)

    def test_update_check_combines_all_uninstalled_release_notes(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = app.AppController.__new__(app.AppController)
            controller.store = app.Store(Path(temp) / "test.db")
            controller.update_lock = __import__("threading").Lock()
            controller.update_state = {}
            controller.window = None
            controller.visible = False
            controller._github_json = lambda _path: [
                {
                    "tag_name": "imagen-v9.9.9",
                    "target_commitish": "imagen",
                    "body": "- 最新版本内容",
                    "assets": [
                        {
                            "name": app.RELEASE_ASSET_NAME,
                            "url": "api",
                            "browser_download_url": "web",
                        }
                    ],
                },
                {
                    "tag_name": "imagen-v9.9.8",
                    "target_commitish": "imagen",
                    "body": "- 中间版本内容",
                    "assets": [],
                },
                {
                    "tag_name": f"imagen-v{app.APP_VERSION}",
                    "target_commitish": "imagen",
                    "body": "- 已安装版本内容",
                    "assets": [],
                },
                {
                    "tag_name": "v99.0.0",
                    "target_commitish": "main",
                    "body": "- 主分支版本，不应出现",
                    "assets": [{"name": app.RELEASE_ASSET_NAME}],
                },
            ]

            controller._check_for_updates_worker(manual=True)

            notes = controller.update_state["releaseNotes"]
            self.assertTrue(controller.update_state["available"])
            self.assertIn("## 9.9.9", notes)
            self.assertIn("最新版本内容", notes)
            self.assertIn("## 9.9.8", notes)
            self.assertIn("中间版本内容", notes)
            self.assertNotIn("已安装版本内容", notes)
            self.assertNotIn("主分支版本", notes)

    def test_limit_change_notifications_report_increase_and_decrease_once(self):
        notifications = []
        controller = app.AppController.__new__(app.AppController)
        controller.notify = lambda title, message, severity=0: notifications.append(
            (title, message, severity)
        )
        payload = {
            "_limit_changes": {
                "quota": {"previous": 100, "current": 150},
                "7d": {"previous": 77, "current": 67},
                "5h": {"previous": 27, "current": 44},
            }
        }

        controller._notify_limit_changes("生产密钥", payload, {"quota", "7d"})

        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[0][0], "生产密钥 · 限制调整")
        self.assertEqual(
            notifications[0][1],
            "您的总额度上限已从 100 USD 提高到 150 USD",
        )
        self.assertEqual(notifications[0][2], 0)
        self.assertEqual(
            notifications[1][1],
            "您的7d 速率限制已从 77 USD 降低到 67 USD",
        )
        self.assertEqual(notifications[1][2], 2)

    def test_limit_change_notifications_report_added_and_removed_limits(self):
        notifications = []
        controller = app.AppController.__new__(app.AppController)
        controller.notify = lambda title, message, severity=0: notifications.append(
            (title, message, severity)
        )
        payload = {
            "_limit_changes": {
                "5h": {"previous": 0, "current": 27},
                "1d": {"previous": 44, "current": 0},
            }
        }

        controller._notify_limit_changes("生产密钥", payload, {"5h", "1d"})

        self.assertEqual(
            [item[1] for item in notifications],
            [
                "您的5h 速率限制已新增为 27 USD",
                "您的1d 速率限制已取消，原限制为 44 USD",
            ],
        )
        self.assertEqual([item[2] for item in notifications], [1, 2])

    def test_release_asset_download_uses_curl_and_falls_back_to_browser_url(self):
        controller = app.AppController.__new__(app.AppController)
        controller.update_state = {}
        controller.window = None
        controller.visible = False

        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "update.download"

            class FakeProcess:
                def __init__(self, command):
                    self.returncode = 35 if command[-1] == "https://api.example/asset" else 0
                    if self.returncode == 0:
                        destination.write_bytes(b"new-binary")

                def poll(self):
                    return self.returncode

                def communicate(self):
                    return b"", b"TLS EOF" if self.returncode else b""

            with patch("app.shutil.which", return_value="curl.exe"), patch(
                "app.urllib.request.getproxies", return_value={}
            ), patch("app.subprocess.Popen", side_effect=lambda command, **_kwargs: FakeProcess(command)) as curl:
                result = controller._download_release_file(
                    ["https://api.example/asset", "https://download.example/asset"],
                    destination,
                    "正在连接下载源",
                    len(b"new-binary"),
                )

            self.assertEqual(result.read_bytes(), b"new-binary")

        self.assertEqual(curl.call_count, 2)
        self.assertEqual(curl.call_args_list[0].args[0][-1], "https://api.example/asset")
        self.assertEqual(curl.call_args_list[1].args[0][-1], "https://download.example/asset")
        self.assertIn("--continue-at", curl.call_args_list[0].args[0])
        self.assertIn("--retry-all-errors", curl.call_args_list[0].args[0])

    def test_release_asset_download_reports_file_progress(self):
        controller = app.AppController.__new__(app.AppController)
        controller.update_state = {}
        controller.window = None
        controller.visible = False
        updates = []
        controller._set_update_state = lambda **changes: updates.append(changes)

        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "update.download"

            class FakeProcess:
                returncode = 0

                def __init__(self):
                    self.polls = 0

                def poll(self):
                    self.polls += 1
                    if self.polls == 1:
                        destination.write_bytes(b"12345")
                        return None
                    destination.write_bytes(b"1234567890")
                    return 0

                def communicate(self):
                    return b"", b""

            with patch("app.shutil.which", return_value="curl.exe"), patch(
                "app.urllib.request.getproxies", return_value={}
            ), patch("app.subprocess.Popen", return_value=FakeProcess()), patch(
                "app.time.sleep"
            ):
                controller._download_release_file(
                    ["https://download.example/asset"],
                    destination,
                    "正在连接下载源",
                    10,
                )

        self.assertTrue(any(update.get("percent") == 50 for update in updates))
        self.assertEqual(updates[-1]["percent"], 99)

    def test_launch_updater_handles_missing_process_and_retries_replacement(self):
        controller = app.AppController.__new__(app.AppController)
        controller.exit_app = __import__("unittest.mock").mock.Mock()
        downloaded = Path("downloaded.exe")
        updater = SimpleNamespace(poll=lambda: None)

        with tempfile.TemporaryDirectory() as temp:
            def start_updater(arguments, **_kwargs):
                ready_path = Path(arguments[arguments.index("-Ready") + 1])
                ready_path.write_text("ready", encoding="ascii")
                return updater

            with patch.object(controller_updates, "app_data_dir", return_value=Path(temp)), patch.object(
                controller_updates.subprocess, "Popen", side_effect=start_updater
            ) as popen:
                controller._launch_updater(downloaded)

            script = (Path(temp) / "apply-update.ps1").read_text(encoding="utf-8")

        self.assertIn("Get-Process -Id $Id -ErrorAction SilentlyContinue", script)
        self.assertIn("Set-Content -LiteralPath $Ready", script)
        self.assertIn("if ($process)", script)
        self.assertIn("Wait-ForProcessExit $ProcessId", script)
        self.assertIn("Wait-ForProcessExit $BootloaderProcessId", script)
        self.assertIn("for ($attempt = 1; $attempt -le 60; $attempt++)", script)
        self.assertIn("Copy-Item -LiteralPath $Source -Destination $Target -Force", script)
        self.assertIn("Start-Process -FilePath $Target", script)
        self.assertIn("$env:API_TOOLS_RESTART_READY = $Restarted", script)
        self.assertIn("Test-Path -LiteralPath $Restarted", script)
        self.assertIn("for ($launchAttempt = 1; $launchAttempt -le 2; $launchAttempt++)", script)
        self.assertEqual(script.count("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'"), 1)
        self.assertLess(
            script.index("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'"),
            script.index("Start-Process -FilePath $Target"),
        )
        self.assertIn("-BootloaderProcessId", popen.call_args.args[0])
        self.assertIn("-Restarted", popen.call_args.args[0])
        self.assertIn("-Log", popen.call_args.args[0])
        controller.exit_app.assert_called_once_with()

    def test_download_completion_waits_for_restart_confirmation(self):
        controller = app.AppController.__new__(app.AppController)
        controller.update_lock = __import__("threading").Lock()
        controller.update_state = {
            "release": {
                "version": "9.9.9",
                "downloadSize": 10,
                "downloadApiUrl": "download-api",
                "downloadUrl": "download-web",
                "checksumApiUrl": "checksum-api",
                "checksumUrl": "checksum-web",
            }
        }
        controller.window = None
        controller.visible = False

        with tempfile.TemporaryDirectory() as temp, patch.object(
            controller_image_files, "app_data_dir", return_value=Path(temp)
        ), patch.object(
            controller,
            "_download_release_file",
            side_effect=lambda _urls, destination, _message, _size: destination.write_bytes(
                b"new-binary"
            ) or destination,
        ), patch.object(
            controller, "_download_text", return_value=app.sha256_bytes(b"new-binary")
        ), patch.object(controller, "_launch_updater") as launch_updater:
            controller._download_update_worker()

            downloaded = Path(controller.update_state["downloadedPath"])
            self.assertTrue(downloaded.is_file())

        self.assertEqual(controller.update_state["status"], "ready")
        self.assertEqual(controller.update_state["percent"], 100)
        self.assertEqual(
            controller.update_state["message"],
            "下载完成，点击重启以应用更新",
        )
        launch_updater.assert_not_called()

    def test_update_download_hides_tls_transport_details(self):
        controller = app.AppController.__new__(app.AppController)
        controller.update_lock = __import__("threading").Lock()
        controller.update_state = {
            "release": {
                "version": "9.9.9",
                "downloadUrl": "https://download.example/asset",
            }
        }
        controller.window = None
        controller.visible = False

        with tempfile.TemporaryDirectory() as temp, patch.object(
            controller_image_files, "app_data_dir", return_value=Path(temp)
        ), patch.object(
            controller,
            "_download_release_file",
            side_effect=app.NetworkTransportError("SSL UNEXPECTED_EOF_WHILE_READING"),
        ):
            controller._download_update_worker()

        self.assertEqual(controller.update_state["status"], "failed")
        self.assertEqual(
            controller.update_state["message"],
            "更新失败：无法连接下载服务器，请检查网络或代理后重试",
        )
        self.assertNotIn("SSL", controller.update_state["message"])

    def test_restart_update_launches_verified_download(self):
        controller = app.AppController.__new__(app.AppController)
        controller.update_lock = __import__("threading").Lock()
        controller.window = None
        controller.visible = False

        with tempfile.TemporaryDirectory() as temp:
            downloaded = Path(temp) / "API_TOOLS-9.9.9.exe"
            downloaded.write_bytes(b"verified")
            controller.update_state = {
                "status": "ready",
                "downloadedPath": str(downloaded),
            }
            with patch.object(controller, "_launch_updater") as launch_updater:
                result = controller.restart_update()

        self.assertEqual(result, {"ok": True})
        launch_updater.assert_called_once_with(downloaded)

    def test_deferred_update_is_applied_on_later_exit(self):
        controller = app.AppController.__new__(app.AppController)
        controller.window = None
        controller.visible = False

        with tempfile.TemporaryDirectory() as temp:
            downloaded = Path(temp) / "API_TOOLS-9.9.9.exe"
            downloaded.write_bytes(b"verified")
            controller.update_state = {
                "status": "ready",
                "downloadedPath": str(downloaded),
            }
            controller._push_update_state = lambda: None
            result = controller.defer_update_restart()

            with patch.object(controller, "_launch_updater") as launch_updater:
                controller.exit_app()

        self.assertTrue(result["ok"])
        self.assertFalse(result["update"]["showPrompt"])
        launch_updater.assert_called_once_with(downloaded)

    def test_launch_updater_keeps_app_open_when_updater_fails_to_start(self):
        controller = app.AppController.__new__(app.AppController)
        controller.exit_app = __import__("unittest.mock").mock.Mock()
        updater = SimpleNamespace(poll=lambda: 1)

        with tempfile.TemporaryDirectory() as temp, patch(
            "app.app_data_dir", return_value=Path(temp)
        ), patch("app.subprocess.Popen", return_value=updater):
            with self.assertRaisesRegex(RuntimeError, "更新程序启动失败"):
                controller._launch_updater(Path("downloaded.exe"))

        controller.exit_app.assert_not_called()

    def test_restart_app_waits_for_ready_before_closing_current_window(self):
        mock = __import__("unittest.mock").mock
        controller = app.AppController.__new__(app.AppController)
        controller.stopping = __import__("threading").Event()
        controller.refresh_wakeup = __import__("threading").Event()
        controller.tray = SimpleNamespace(stop=mock.Mock())
        controller.window = SimpleNamespace(destroy=mock.Mock())

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def start_restarter(arguments, **_kwargs):
                (root / "restart.ready").write_text("ready", encoding="ascii")
                return SimpleNamespace(poll=lambda: None, arguments=arguments)

            with patch.object(controller_window_commands, "app_data_dir", return_value=root), patch.object(
                controller_window_commands.subprocess, "Popen", side_effect=start_restarter
            ) as popen:
                result = controller.restart_app()

            script = (root / "restart-app.ps1").read_text(encoding="utf-8")

        self.assertEqual(result, {"ok": True})
        self.assertIn("Wait-Process", script)
        self.assertIn("Start-Process -FilePath $Executable", script)
        self.assertLess(
            script.index("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'"),
            script.index("Start-Process -FilePath $Executable"),
        )
        self.assertIn("-ProcessId", popen.call_args.args[0])
        self.assertTrue(controller.stopping.is_set())
        self.assertTrue(controller.refresh_wakeup.is_set())
        controller.tray.stop.assert_called_once_with()
        controller.window.destroy.assert_called_once_with()

    def test_legacy_app_preference_call_preserves_title_bar_mode(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            set_update_frequency=lambda value: value,
            set_close_action=lambda value: value,
            get_title_bar_mode=lambda: "minimal",
            get_background_ui_mode=lambda: "delayed",
        )
        controller.get_state = lambda: {
            "titleBarMode": "minimal",
            "backgroundUiMode": "delayed",
        }

        with patch.object(controller_window_commands, "set_startup_enabled", return_value=False):
            result = controller.update_app_preferences("startup", "ask", False)

        self.assertEqual(result["titleBarMode"], "minimal")
        self.assertEqual(result["backgroundUiMode"], "delayed")

    def test_background_ui_mode_option_is_wired_to_app_preferences(self):
        page = frontend_source()

        self.assertIn('id="backgroundUiMode"', page)
        self.assertIn('value="delayed">5 分钟后进入低开销模式（默认）', page)
        self.assertIn('value="active">始终保持活跃（启动更快）', page)
        self.assertIn('value="low_power">立刻进入低消耗模式（内存占用更少）', page)
        self.assertIn("window.appState.titleBarMode, backgroundUiMode", page)

    def test_update_modal_defaults_to_pending_notes_and_can_expand_full_history(self):
        page = frontend_source()

        self.assertIn('id="toggleFullChangelogButton"', page)
        self.assertIn("查看完整更新日志", page)
        self.assertIn("window.appState.showFullChangelog", page)
        self.assertIn("current.fullReleaseNotes", page)
        self.assertIn("current.status === 'checking'", page)

    def test_updater_replaces_target_when_original_process_is_already_gone(self):
        controller = app.AppController.__new__(app.AppController)
        controller.exit_app = __import__("unittest.mock").mock.Mock()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "API_TOOLS.cmd"
            downloaded = root / "API_TOOLS-new.cmd"
            target.write_text("@echo off\r\nrem old-version\r\n", encoding="ascii")
            downloaded.write_text(
                "@echo off\r\n"
                "rem new-version\r\n"
                "if defined API_TOOLS_RESTART_READY echo ready>\"%API_TOOLS_RESTART_READY%\"\r\n",
                encoding="ascii",
            )
            processes = []
            real_popen = __import__("subprocess").Popen

            def start_updater(arguments, **kwargs):
                process = real_popen(arguments, **kwargs)
                processes.append(process)
                return process

            with patch.object(controller_updates, "app_data_dir", return_value=root), patch.object(
                controller_updates.sys, "executable", str(target)
            ), patch.object(controller_updates.os, "getpid", return_value=2147483000), patch.object(
                controller_updates.subprocess, "Popen", side_effect=start_updater
            ):
                controller._launch_updater(downloaded)

            self.assertEqual(processes[0].wait(timeout=15), 0)
            self.assertIn("new-version", target.read_text(encoding="ascii"))
            self.assertFalse(downloaded.exists())
            self.assertFalse((root / "update.log").exists())
            controller.exit_app.assert_called_once_with()

    def test_custom_threshold_is_used_in_notification_title(self):
        notifications = []
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_thresholds=lambda: {"warn": 37, "danger": 18, "critical": 7},
            alert_severity=lambda _key_id, _metric: 0,
            set_alert_severity=lambda *_args: None,
        )
        controller.notify = lambda title, message, severity=0: notifications.append(
            (title, message, severity)
        )

        controller._check_alerts(
            "key-1",
            "自定义密钥",
            {"quota": {"limit": 100, "remaining": 6}},
        )

        self.assertEqual(len(notifications), 1)
        self.assertIn("7% 严重", notifications[0][0])
        self.assertNotIn("5%", notifications[0][0])
        self.assertEqual(notifications[0][2], 3)

    @patch.object(controller_quota_state, "Notification")
    def test_notify_uses_severity_icon(self, notification):
        controller = app.AppController.__new__(app.AppController)

        controller.notify("额度告警", "仅剩 8%", severity=3)

        self.assertTrue(
            notification.call_args.kwargs["icon"].endswith(
                "resources\\icons\\api_tools_critical.png"
            )
        )

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
                "append_image_stream_debug",
                "cancel_image_generation",
                "check_for_updates",
                "choose_edit_images",
                "complete_initialization",
                "copy_generated_image",
                "delete_key",
                "delete_image_set",
                "defer_update_restart",
                "dismiss_update_prompt",
                "download_update",
                "generate_image",
                "get_asset_status",
                "get_state",
                "initialize_assets",
                "ignore_update_version",
                "import_reference_image",
                "load_generated_image",
                "list_image_sets",
                "native_drag",
                "open_devtools",
                "open_generated_pictures",
                "polish_prompt",
                "refresh_now",
                "report_startup",
                "restart_app",
                "restart_update",
                "resolve_close_action",
                "save_edited_image",
                "set_always_on_top",
                "set_window_size",
                "set_window_background",
                "update_app_preferences",
                "update_refresh_intervals",
                "update_rate_limit_progress_mode",
                "update_thresholds",
                "window_action",
            },
        )
        self.assertNotIn("store", public_names)
        self.assertNotIn("window", public_names)
        self.assertIn("open_generated_pictures", app.RPC_METHODS)
        self.assertIn("append_image_stream_debug", app.RPC_METHODS)
        self.assertIn("cancel_image_generation", app.RPC_METHODS)

    def test_append_image_stream_debug_writes_sanitized_json_lines(self):
        controller = app.AppController.__new__(app.AppController)
        controller.image_stream_debug_lock = __import__("threading").Lock()
        with tempfile.TemporaryDirectory() as temp, patch.object(
            controller_image_files, "app_data_dir", return_value=Path(temp)
        ):
            result = controller.append_image_stream_debug(
                [
                    {
                        "event": "sample",
                        "setId": "set-1",
                        "actualBlurPx": 12.5,
                        "uri": "data:image/png;base64,not-logged",
                        "layers": [
                            {
                                "actualBlurPx": 8.25,
                                "previewUri": "data:image/png;base64,also-not-logged",
                            }
                        ],
                    },
                    "invalid",
                ]
            )
            log_path = Path(result["path"])
            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertTrue(result["ok"])
        self.assertEqual(result["written"], 1)
        self.assertEqual(records[0]["actualBlurPx"], 12.5)
        self.assertEqual(records[0]["layers"][0]["actualBlurPx"], 8.25)
        self.assertNotIn("uri", records[0])
        self.assertNotIn("previewUri", records[0]["layers"][0])

    def test_append_image_stream_debug_rotates_full_log(self):
        controller = app.AppController.__new__(app.AppController)
        controller.image_stream_debug_lock = __import__("threading").Lock()
        with tempfile.TemporaryDirectory() as temp, patch.object(
            controller_image_files, "app_data_dir", return_value=Path(temp)
        ), patch.object(controller_image_files, "IMAGE_STREAM_DEBUG_LOG_MAX_BYTES", 16):
            log_path = Path(temp) / "image-stream-blur.jsonl"
            log_path.write_text("old-log\n", encoding="utf-8")
            result = controller.append_image_stream_debug(
                [{"event": "sample", "actualBlurPx": 3.5}]
            )
            previous = (Path(temp) / "image-stream-blur.previous.jsonl").read_text(
                encoding="utf-8"
            )
            current = log_path.read_text(encoding="utf-8")

        self.assertTrue(result["ok"])
        self.assertEqual(previous, "old-log\n")
        self.assertEqual(json.loads(current)["actualBlurPx"], 3.5)

    def test_load_generated_image_allows_only_managed_output(self):
        controller = app.AppController.__new__(app.AppController)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            managed = root / "image-generations" / "partials" / "preview.png"
            managed.parent.mkdir(parents=True)
            Image.new("RGB", (8, 8), "blue").save(managed)
            outside = root / "outside.png"
            Image.new("RGB", (8, 8), "red").save(outside)

            with patch.object(controller_image_files, "app_data_dir", return_value=root), patch.object(
                controller_image_files, "generated_pictures_dir", return_value=root / "pictures"
            ):
                allowed = controller.load_generated_image(str(managed))
                blocked = controller.load_generated_image(str(outside))

        self.assertTrue(allowed["ok"])
        self.assertTrue(allowed["dataUrl"].startswith("data:image/png;base64,"))
        self.assertFalse(blocked["ok"])
        self.assertIn("只能读取", blocked["error"])

    def test_image_to_windows_dib_removes_bitmap_file_header(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.png"
            Image.new("RGB", (8, 6), "blue").save(source)

            dib = app.image_to_windows_dib(source)

        self.assertEqual(int.from_bytes(dib[:4], "little"), 40)
        self.assertNotEqual(dib[:2], b"BM")

    def test_copy_generated_image_allows_only_persisted_final_output(self):
        controller = app.AppController.__new__(app.AppController)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pictures_root = root / "Pictures" / app.APP_NAME
            pictures_root.mkdir(parents=True)
            final_image = pictures_root / "final.png"
            Image.new("RGB", (8, 8), "green").save(final_image)
            partial_image = root / "image-generations" / "partials" / "partial.png"
            partial_image.parent.mkdir(parents=True)
            Image.new("RGB", (8, 8), "yellow").save(partial_image)

            with patch.object(controller_image_files, "generated_pictures_dir", return_value=pictures_root), patch.object(
                controller_image_files, "copy_image_to_windows_clipboard"
            ) as copy_to_clipboard:
                copied = controller.copy_generated_image(str(final_image))
                blocked = controller.copy_generated_image(str(partial_image))

        self.assertTrue(copied["ok"])
        self.assertFalse(blocked["ok"])
        self.assertIn("最终图片", blocked["error"])
        copy_to_clipboard.assert_called_once_with(final_image.resolve())

    def test_controller_lists_and_deletes_persisted_image_sets(self):
        controller = app.AppController.__new__(app.AppController)
        controller.active_image_sets = set()
        with tempfile.TemporaryDirectory() as temp:
            pictures_root = Path(temp) / "Pictures" / app.APP_NAME
            source = Path(temp) / "source.png"
            Image.new("RGB", (12, 8), "teal").save(source)
            store = image_editor.ImageSessionStore(pictures_root)
            store.begin_round(
                "session-1",
                "set-1",
                "专业产品图，三分构图",
                1,
                0,
                {
                    "reasoningMode": "high",
                    "reasoningModel": "gpt-5.6-sol",
                    "reasoningEffort": "medium",
                    "reasoningSummary": "识别产品展示场景并比较构图方案。",
                    "reasoningDurationMs": 9_876,
                    "originalPrompt": "制作产品图",
                },
            )
            store.persist_result(
                "session-1",
                "set-1",
                0,
                source,
                {"width": 12, "height": 8, "format": "png", "actualSize": "12x8"},
            )
            store.complete_round("session-1", "set-1")

            with patch.object(controller_image_files, "generated_pictures_dir", return_value=pictures_root):
                listed = controller.list_image_sets()
                deleted = controller.delete_image_set("session-1", "set-1")
                listed_after = controller.list_image_sets()

        self.assertTrue(listed["ok"])
        self.assertEqual(listed["sets"][0]["prompt"], "专业产品图，三分构图")
        self.assertEqual(listed["sets"][0]["originalPrompt"], "制作产品图")
        self.assertEqual(listed["sets"][0]["reasoningMode"], "high")
        self.assertEqual(listed["sets"][0]["reasoningModel"], "gpt-5.6-sol")
        self.assertEqual(listed["sets"][0]["reasoningEffort"], "medium")
        self.assertEqual(listed["sets"][0]["reasoningSummary"], "识别产品展示场景并比较构图方案。")
        self.assertEqual(listed["sets"][0]["reasoningDurationMs"], 9_876)
        self.assertEqual(listed["sets"][0]["reasoningStatus"], "completed")
        self.assertEqual(listed["sets"][0]["effectivePrompt"], "专业产品图，三分构图")
        self.assertTrue(deleted["ok"])
        self.assertEqual(listed_after["sets"], [])

    def test_controller_rejects_deleting_running_image_set(self):
        controller = app.AppController.__new__(app.AppController)
        controller.active_image_sets = {"set-running"}

        result = controller.delete_image_set("session-1", "set-running")

        self.assertFalse(result["ok"])
        self.assertIn("生成中", result["error"])

    def test_controller_rejects_deleting_any_round_from_active_session(self):
        controller = app.AppController.__new__(app.AppController)
        controller.active_image_sets = set()
        controller.active_image_sessions = {"session-active"}
        controller.image_session_activity_lock = __import__("threading").Lock()

        result = controller.delete_image_set("session-active", "set-parent")

        self.assertFalse(result["ok"])
        self.assertIn("图片会话仍在生成", result["error"])

    def test_controller_marks_stale_running_image_set_as_interrupted(self):
        controller = app.AppController.__new__(app.AppController)
        controller.active_image_sets = set()
        with tempfile.TemporaryDirectory() as temp:
            pictures_root = Path(temp) / "Pictures" / app.APP_NAME
            store = image_editor.ImageSessionStore(pictures_root)
            store.begin_round("session-stale", "set-stale", "未完成", 2, 0, {})

            with patch.object(controller_image_files, "generated_pictures_dir", return_value=pictures_root):
                listed = controller.list_image_sets()
                deleted = controller.delete_image_set("session-stale", "set-stale")

        image_set = listed["sets"][0]
        self.assertEqual(image_set["status"], "interrupted")
        self.assertEqual([item["status"] for item in image_set["items"]], ["failed", "failed"])
        self.assertEqual([item["error"] for item in image_set["items"]], ["生成已中断", "生成已中断"])
        self.assertTrue(deleted["ok"])

    def test_rpc_server_dispatches_only_allowlisted_controller_methods(self):
        class FakeConnection:
            def __init__(self, request):
                self.request = request
                self.responses = []
                self.closed = False

            def recv(self):
                return self.request

            def send(self, response):
                self.responses.append(response)

            def close(self):
                self.closed = True

        controller = SimpleNamespace(
            get_state=lambda: {"keys": ["key-1"]},
            open_generated_pictures=lambda: {"ok": True, "path": "Pictures/API_TOOLS"},
            append_image_stream_debug=lambda records: {
                "ok": True,
                "written": len(records),
            },
        )
        server = app.ControllerRpcServer(controller, "pipe", b"secret")
        allowed = FakeConnection({"method": "get_state", "args": []})
        open_pictures = FakeConnection({"method": "open_generated_pictures", "args": []})
        append_debug = FakeConnection(
            {"method": "append_image_stream_debug", "args": [[{"event": "sample"}]]}
        )
        blocked = FakeConnection({"method": "__dict__", "args": []})

        server._handle_connection(allowed)
        server._handle_connection(open_pictures)
        server._handle_connection(append_debug)
        server._handle_connection(blocked)

        self.assertEqual(allowed.responses, [{"ok": True, "result": {"keys": ["key-1"]}}])
        self.assertEqual(
            open_pictures.responses,
            [{"ok": True, "result": {"ok": True, "path": "Pictures/API_TOOLS"}}],
        )
        self.assertEqual(
            append_debug.responses,
            [{"ok": True, "result": {"ok": True, "written": 1}}],
        )
        self.assertTrue(blocked.responses[0]["ok"] is False)
        self.assertIn("不允许", blocked.responses[0]["error"])
        self.assertTrue(allowed.closed)
        self.assertTrue(open_pictures.closed)
        self.assertTrue(append_debug.closed)
        self.assertTrue(blocked.closed)

    def test_rpc_server_streams_generation_events_before_result(self):
        class FakeConnection:
            def __init__(self):
                self.responses = []
                self.closed = False

            def recv(self):
                return {"method": "generate_image", "args": ["key", "prompt", [], {}]}

            def send(self, response):
                self.responses.append(response)

            def close(self):
                self.closed = True

        def generate_image(*_args, event_callback=None):
            event_callback({"type": "item_partial", "setId": "set-1", "itemIndex": 0})
            return {"ok": True, "setId": "set-1", "items": []}

        connection = FakeConnection()
        controller = SimpleNamespace(generate_image=generate_image)
        server = app.ControllerRpcServer(controller, "pipe", b"secret")

        server._handle_connection(connection)

        self.assertEqual(connection.responses[0]["event"]["type"], "item_partial")
        self.assertEqual(connection.responses[1]["result"]["setId"], "set-1")
        self.assertTrue(connection.closed)

    def test_rpc_server_streams_prompt_polish_events_before_result(self):
        class FakeConnection:
            def __init__(self):
                self.responses = []

            def recv(self):
                return {"method": "polish_prompt", "args": ["key", "prompt"]}

            def send(self, response):
                self.responses.append(response)

            def close(self):
                pass

        def polish_prompt(*_args, event_callback=None):
            event_callback({"type": "prompt_polish_delta", "delta": "better"})
            return {"ok": True, "prompt": "better"}

        connection = FakeConnection()
        server = app.ControllerRpcServer(
            SimpleNamespace(polish_prompt=polish_prompt), "pipe", b"secret"
        )
        server._handle_connection(connection)

        self.assertEqual(connection.responses[0]["event"]["type"], "prompt_polish_delta")
        self.assertEqual(connection.responses[1]["result"]["prompt"], "better")

    def test_remote_web_api_routes_data_calls_and_keeps_window_calls_local(self):
        mock = __import__("unittest.mock").mock
        rpc = SimpleNamespace(call=mock.Mock(), call_with_events=mock.Mock())
        rpc.call.side_effect = lambda method, *args: (
            {"keys": [], "isForeground": False}
            if method == "get_state"
            else {"method": method, "args": args}
        )
        rpc.call_with_events.side_effect = lambda method, *args, **_kwargs: {
            "method": method,
            "args": args,
        }
        controller = SimpleNamespace(
            window_action=lambda action: {"local": action},
            complete_initialization=lambda: {"local": "init"},
            choose_edit_images=lambda: {"local": "choose"},
            save_edited_image=lambda path: {"local": path},
            copy_generated_image=lambda path: {"copied": path},
            push_image_generation_event=mock.Mock(),
        )
        api = app.RemoteWebApi(controller, rpc)

        state = api.get_state()
        refresh = api.refresh_now("trace-1")
        generated = api.generate_image("key-1", "combine", ["a.png", "b.png"], {"quality": "low"})
        polished = api.polish_prompt("key-1", "rough")
        listed = api.list_image_sets()
        deleted_set = api.delete_image_set("session-1", "set-1")
        choose = api.choose_edit_images()
        save = api.save_edited_image("result.png")
        copied = api.copy_generated_image("result.png")
        window_result = api.window_action("minimize")

        self.assertTrue(state["isForeground"])
        self.assertEqual(refresh, {"method": "refresh_now", "args": ("trace-1",)})
        self.assertEqual(
            generated,
            {
                "method": "generate_image",
                "args": ("key-1", "combine", ["a.png", "b.png"], {"quality": "low"}),
            },
        )
        self.assertEqual(
            polished,
            {"method": "polish_prompt", "args": ("key-1", "rough")},
        )
        self.assertIs(
            rpc.call_with_events.call_args.kwargs["on_event"],
            controller.push_image_generation_event,
        )
        self.assertEqual(choose, {"local": "choose"})
        self.assertEqual(listed, {"method": "list_image_sets", "args": ()})
        self.assertEqual(
            deleted_set,
            {"method": "delete_image_set", "args": ("session-1", "set-1")},
        )
        self.assertEqual(save, {"local": "result.png"})
        self.assertEqual(copied, {"copied": "result.png"})
        self.assertEqual(window_result, {"local": "minimize"})
        self.assertNotIn(
            __import__("unittest.mock").mock.call("window_action", "minimize"),
            rpc.call.call_args_list,
        )
        self.assertNotIn(
            __import__("unittest.mock").mock.call("choose_edit_images"),
            rpc.call.call_args_list,
        )
        self.assertNotIn(
            __import__("unittest.mock").mock.call("save_edited_image", "result.png"),
            rpc.call.call_args_list,
        )

    def test_ui_controller_low_power_hide_destroys_window_immediately(self):
        mock = __import__("unittest.mock").mock
        rpc = SimpleNamespace(
            call=mock.Mock(
                return_value={
                    "ok": True,
                    "backgroundUiMode": "low_power",
                    "visibilityToken": 2,
                }
            )
        )
        controller = app.UiController.__new__(app.UiController)
        controller.rpc_client = rpc
        controller.visible = True
        controller.stopping = __import__("threading").Event()
        controller.refresh_wakeup = __import__("threading").Event()
        controller.release_timer = None
        controller.window = SimpleNamespace(destroy=mock.Mock())

        with patch.object(app.AppController, "hide_window") as local_hide, patch(
            "app.threading.Timer"
        ) as timer:
            controller.hide_window()

        local_hide.assert_called_once_with(controller)
        self.assertTrue(controller.stopping.is_set())
        rpc.call.assert_called_once_with("notify_ui_hidden")
        timer.assert_called_once_with(0.01, controller.window.destroy)
        timer.return_value.start.assert_called_once_with()

    def test_ui_controller_active_hide_keeps_webview_process_alive(self):
        mock = __import__("unittest.mock").mock
        rpc = SimpleNamespace(
            call=mock.Mock(
                return_value={
                    "ok": True,
                    "backgroundUiMode": "active",
                    "visibilityToken": 3,
                }
            )
        )
        controller = app.UiController.__new__(app.UiController)
        controller.rpc_client = rpc
        controller.release_timer = None

        with patch.object(app.AppController, "hide_window") as local_hide, patch(
            "app.threading.Timer"
        ) as timer:
            controller.hide_window()

        local_hide.assert_called_once_with(controller)
        rpc.call.assert_called_once_with("notify_ui_hidden")
        timer.assert_not_called()

    def test_ui_controller_delayed_hide_claims_release_after_five_minutes(self):
        mock = __import__("unittest.mock").mock
        rpc = SimpleNamespace(
            call=mock.Mock(
                return_value={
                    "ok": True,
                    "backgroundUiMode": "delayed",
                    "visibilityToken": 7,
                }
            )
        )
        controller = app.UiController.__new__(app.UiController)
        controller.rpc_client = rpc
        controller.release_timer = None

        with patch.object(app.AppController, "hide_window"), patch(
            "app.threading.Timer"
        ) as timer:
            controller.hide_window()

        timer.assert_called_once_with(
            300,
            controller._release_if_still_hidden,
            args=(7,),
        )
        timer.return_value.start.assert_called_once_with()

    def test_stale_delayed_release_is_rejected_after_ui_reopens(self):
        controller = app.AppController.__new__(app.AppController)
        controller.visible = True
        controller.ui_visibility_token = 4
        controller.refresh_wakeup = __import__("threading").Event()
        controller.store = SimpleNamespace(get_background_ui_mode=lambda: "delayed")

        hidden = controller.notify_ui_hidden()
        controller.set_ui_visible(True)
        claim = controller.claim_ui_release(hidden["visibilityToken"])

        self.assertFalse(claim["release"])

    def test_valid_delayed_release_destroys_hidden_ui(self):
        mock = __import__("unittest.mock").mock
        controller = app.UiController.__new__(app.UiController)
        controller.rpc_client = SimpleNamespace(
            call=mock.Mock(return_value={"ok": True, "release": True})
        )
        controller._destroy_ui = mock.Mock()

        controller._release_if_still_hidden(9)

        controller.rpc_client.call.assert_called_once_with("claim_ui_release", 9)
        controller._destroy_ui.assert_called_once_with()

    def test_background_visibility_switches_refresh_cadence_without_window(self):
        controller = app.AppController.__new__(app.AppController)
        controller.visible = True
        controller.ui_visibility_token = 0
        controller.refresh_wakeup = __import__("threading").Event()

        hidden = controller.set_ui_visible(False)

        self.assertEqual(
            hidden,
            {"ok": True, "visible": False, "visibilityToken": 1},
        )
        self.assertFalse(controller.visible)
        self.assertTrue(controller.refresh_wakeup.is_set())

    def test_hidden_refresh_loop_uses_background_interval_and_records_without_ui_push(self):
        mock = __import__("unittest.mock").mock

        class StopSequence:
            def __init__(self):
                self.values = iter((False, False, True))

            def is_set(self):
                return next(self.values)

        controller = app.AppController.__new__(app.AppController)
        controller.visible = False
        controller.foreground_interval = 60
        controller.background_interval = 300
        controller.stopping = StopSequence()
        controller.refresh_wakeup = SimpleNamespace(
            wait=mock.Mock(return_value=False),
            clear=mock.Mock(),
        )
        controller.refresh_all = mock.Mock()

        controller._refresh_loop()

        controller.refresh_wakeup.wait.assert_called_once_with(300)
        controller.refresh_all.assert_called_once_with(push_ui=False)

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
        controller.active_title_bar_mode = "minimal"
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
            get_background_ui_mode=lambda: "delayed",
            get_title_bar_mode=lambda: "minimal",
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
        self.assertEqual(state["backgroundUiMode"], "delayed")
        self.assertEqual(state["titleBarMode"], "minimal")
        self.assertEqual(state["activeTitleBarMode"], "minimal")

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

        with patch.object(controller_workers_window, "user32", fake_user32), patch.object(
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
        controller.notify = lambda title, message, severity=0: notifications.append(
            (title, message, severity)
        )

        controller._check_alerts("key-1", "test", payload)
        controller._check_alerts("key-1", "test", payload)
        interval["value"] = 16
        controller._check_alerts("key-1", "test", payload)
        interval["value"] = 0.2
        controller._check_alerts("key-1", "test", payload)

        self.assertEqual(len(notifications), 2)
        self.assertIn("速率高负载", notifications[0][0])
        self.assertIn("速率极高负载", notifications[1][0])
        self.assertEqual([item[2] for item in notifications], [1, 2])
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
        controller.notify = lambda title, message, severity=0: notifications.append(
            (title, message, severity)
        )

        controller._check_alerts("key-1", "test", payload)

        self.assertEqual(len(notifications), 1)
        self.assertIn("额度高负载", notifications[0][0])
        self.assertIn("额度 25.00% / 速率 0.25%", notifications[0][1])
        self.assertEqual(notifications[0][2], 1)


if __name__ == "__main__":
    unittest.main()
