"""Documented model budgets shared with the gateway and Android SDK bundle.

The registry contains exact model IDs and published snapshots, never guessed
prefixes or dates. Missing limits remain unknown rather than inheriting the
budget of an unrelated model. JSON is loaded as a package resource so this also
works from the frozen desktop application, independently of its working path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache(maxsize=1)
def _registry() -> dict[str, Any]:
    return json.loads(files(__package__).joinpath("model_limits.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ModelLimits:
    model: str
    context_window: int
    max_input_tokens: int | None
    max_output_tokens: int
    reasoning: bool
    server_compaction: bool
    compaction_status: str
    source: str

    @property
    def usable_input_budget(self) -> int:
        """Reserve the model's entire output budget, including reasoning tokens."""
        return min(self.max_input_tokens or self.context_window,
                   self.context_window - self.max_output_tokens)


@dataclass(frozen=True)
class ModelPolicy:
    max_output_tokens: int | None
    context_management: list[dict[str, Any]] | None
    reasoning: bool
    metadata: dict[str, Any]


def get_model_limits(model: str) -> ModelLimits | None:
    registry = _registry()
    identifier = str(model or "").strip().lower()
    canonical = registry["aliases"].get(identifier, identifier)
    entry = registry["models"].get(canonical)
    return ModelLimits(model=canonical, **entry) if entry is not None else None


def resolve_model_policy(model: str, max_output_tokens: int | None = None) -> ModelPolicy:
    """Apply official defaults while preserving an explicitly lower caller cap.

    Automatic compaction uses 90% of the usable *input* window, after reserving
    the full documented output allowance. The service checks rendered tokens;
    accumulated billable usage is deliberately not used as a context counter.
    """
    if max_output_tokens is not None and (isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int) or max_output_tokens <= 0):
        raise ValueError("max_output_tokens 必须是正整数或留空使用模型默认上限")
    limits = get_model_limits(model)
    if limits is None:
        return ModelPolicy(max_output_tokens=max_output_tokens, context_management=None,
                           reasoning=False, metadata={"known": False, "model": model,
                           "outputTokenLimit": max_output_tokens, "compactionEnabled": False,
                           "compactionStatus": "unknown_model"})
    if max_output_tokens is not None and max_output_tokens > limits.max_output_tokens:
        raise ValueError(f"{model} 的输出上限为 {limits.max_output_tokens:,} tokens")
    output_limit = max_output_tokens if max_output_tokens is not None else limits.max_output_tokens
    registry = _registry()
    # Integer arithmetic prevents float rounding from moving a boundary by one.
    threshold = limits.usable_input_budget * 9 // 10 if limits.server_compaction else None
    management = [{"type": "compaction", "compact_threshold": threshold}] if threshold is not None else None
    metadata = {
        "known": True, "model": limits.model, "contextWindow": limits.context_window,
        "maxInputTokens": limits.max_input_tokens, "maxOutputTokens": limits.max_output_tokens,
        "outputTokenLimit": output_limit, "usableInputBudget": limits.usable_input_budget,
        "compactionEnabled": limits.server_compaction, "compactionStatus": limits.compaction_status,
        "compactionRatio": registry["compaction_ratio"], "compactionBasis": registry["compaction_basis"],
        "compactThreshold": threshold, "source": limits.source,
    }
    return ModelPolicy(output_limit, management, limits.reasoning, metadata)


def compacted_history(items: list[Any], updates: dict[str, list[dict[str, Any]]] | None = None) -> list[Any]:
    """Replay the latest opaque compaction and its suffix without editing them.

    This is safe prefix removal only after an actual encrypted compaction item
    exists. No token approximation, ad-hoc summary or trimming of uncompressed
    tools/images is used. Fresh configuration updates are restored from accepted
    raw provider output because Agents SDK 0.25 does not create RunItems for them.
    """
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if not isinstance(item, dict) or item.get("type") != "compaction":
            continue
        if not isinstance(item.get("encrypted_content"), str) or not item["encrypted_content"]:
            continue
        suffix = items[index:]
        recorded = (updates or {}).get(str(item.get("id") or ""))
        if not recorded:
            return suffix
        end = 1
        while end < len(suffix) and isinstance(suffix[end], dict) and suffix[end].get("type") == "configuration_update":
            end += 1
        restored_ids = {entry.get("id") for entry in recorded if entry.get("id")}
        additional = [entry for entry in suffix[1:end] if entry not in recorded and entry.get("id") not in restored_ids]
        return [suffix[0], *recorded, *additional, *suffix[end:]]
    return list(items)


def compaction_updates(output: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Retain only updates following valid, completed provider compaction items."""
    found: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(output):
        if not isinstance(item, dict) or item.get("type") != "compaction" or not item.get("encrypted_content") or not item.get("id"):
            continue
        following = []
        for update in output[index + 1:]:
            if not isinstance(update, dict) or update.get("type") != "configuration_update":
                break
            following.append(update)
        found[str(item["id"])] = following
    return found
