import json
import os
import tempfile
import unittest
from pathlib import Path

from mirra_agents.model_limits import (
    _registry, compacted_history, compaction_updates, get_model_limits, resolve_model_policy,
)


class AgentModelLimitsTests(unittest.TestCase):
    def test_active_models_use_documented_full_budget_with_output_headroom(self):
        for model in ("gpt-6.2-sol", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"):
            with self.subTest(model=model):
                limits = get_model_limits(model)
                self.assertEqual((limits.context_window, limits.max_input_tokens, limits.max_output_tokens),
                                 (1_050_000, 922_000, 128_000))
                policy = resolve_model_policy(model)
                self.assertEqual(policy.max_output_tokens, 128_000)
                self.assertEqual(policy.context_management, [{"type": "compaction", "compact_threshold": 829_800}])
                self.assertEqual(policy.metadata["compactionBasis"], "usable_input_budget")
                self.assertLess(policy.metadata["compactThreshold"] + limits.max_output_tokens, limits.context_window)

    def test_explicit_lower_output_is_retained_without_weakening_headroom(self):
        policy = resolve_model_policy("gpt-6.1-terra", 16_384)
        self.assertEqual(policy.max_output_tokens, 16_384)
        self.assertEqual(policy.metadata["maxOutputTokens"], 128_000)
        self.assertEqual(policy.metadata["outputTokenLimit"], 16_384)
        self.assertEqual(policy.metadata["compactThreshold"], 829_800)
        for cap in (0, -1, True, "8192", 128_001):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                resolve_model_policy("gpt-6-luna", cap)

    def test_exact_snapshots_and_aliases_resolve_without_guessing_new_models(self):
        self.assertEqual(get_model_limits("gpt-5.4-2026-03-05").model, "gpt-5.4")
        self.assertEqual(get_model_limits("gpt-5.6").model, "gpt-5.6-sol")
        for model in ("gpt-6.1-luna", "gpt-6.1-sol-2099-12-31", "gpt-6.2-sol-custom", "private-llm"):
            with self.subTest(model=model):
                self.assertIsNone(get_model_limits(model))
                policy = resolve_model_policy(model)
                self.assertIsNone(policy.max_output_tokens)
                self.assertIsNone(policy.context_management)
                self.assertFalse(policy.metadata["known"])

    def test_older_models_keep_their_limits_without_claiming_unconfirmed_compaction(self):
        policy = resolve_model_policy("gpt-4.1-2025-04-14")
        self.assertEqual(policy.max_output_tokens, 32_768)
        self.assertEqual(policy.metadata["contextWindow"], 1_047_576)
        self.assertIsNone(policy.metadata["maxInputTokens"])
        self.assertEqual(policy.metadata["usableInputBudget"], 1_014_808)
        self.assertEqual(policy.metadata["compactionStatus"], "not_documented")
        self.assertIsNone(policy.context_management)
        self.assertEqual(resolve_model_policy("gpt-5-pro").max_output_tokens, 272_000)

    def test_registry_is_package_data_independent_of_process_working_directory(self):
        old_cwd = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                _registry.cache_clear()
                self.assertEqual(get_model_limits("gpt-6-luna").max_output_tokens, 128_000)
                os.chdir(old_cwd)
        finally:
            os.chdir(old_cwd)
            _registry.cache_clear()

    def test_registry_entries_are_consistent_and_have_official_sources(self):
        registry = _registry()
        for name, entry in registry["models"].items():
            with self.subTest(model=name):
                limits = get_model_limits(name)
                self.assertGreater(limits.usable_input_budget, 1_000)
                self.assertGreater(limits.max_output_tokens, 0)
                self.assertGreaterEqual(limits.context_window, limits.usable_input_budget + limits.max_output_tokens)
                self.assertEqual(entry["source"], "https://developers.openai.com/api/docs/models/" + name)
        for alias, canonical in registry["aliases"].items():
            self.assertIn(canonical, registry["models"])
            self.assertEqual(get_model_limits(alias), get_model_limits(canonical))

    def test_only_actual_encrypted_compaction_permits_prefix_removal(self):
        original = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]},
                    {"type": "function_call", "call_id": "call-a", "name": "inspect", "arguments": "{}"},
                    {"type": "function_call_output", "call_id": "call-a", "output": "actual reference"}]
        self.assertEqual(compacted_history(original), original)
        invalid = [*original, {"type": "compaction", "id": "no-payload"}]
        self.assertEqual(compacted_history(invalid), invalid)
        valid = {"type": "compaction", "id": "cmp-b", "encrypted_content": "opaque", "vendor_state": {"preserve": True}}
        suffix = [valid, {"role": "user", "content": "continue"}]
        self.assertEqual(compacted_history([*original, *suffix]), suffix)
        self.assertEqual(len(original), 3)

    def test_latest_compaction_restores_fresh_update_and_preserves_unknown_fields(self):
        old = {"type": "compaction", "id": "old", "encrypted_content": "old-state"}
        current = {"type": "compaction", "id": "new", "encrypted_content": "new-state", "vendor_field": [1, 2]}
        update = {"type": "configuration_update", "id": "config", "reasoning": {"effort": "high"}, "vendor_extra": {"retain": 1}}
        output = [old, {"role": "assistant", "content": "old"}, current, update]
        updates = compaction_updates(output)
        tool = {"type": "function_call_output", "call_id": "call-a", "output": [{"type": "input_image", "image_url": "actual-bytes"}]}
        expected = [current, update, tool]
        self.assertEqual(compacted_history([*output[:-1], tool], updates), expected)
        self.assertEqual(compacted_history([*output, tool], updates), expected)
        self.assertIn("vendor_extra", json.dumps(expected))


if __name__ == "__main__":
    unittest.main()
