from __future__ import annotations

from .common import *
def parse_timestamp(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


GPT_5_6_PRICING = {
    "gpt-5.6-luna": ((0.20, 0.02, 1.20), (0.40, 0.04, 1.80)),
    "gpt-5.6-terra": ((2.00, 0.20, 12.00), (4.00, 0.40, 18.00)),
    "gpt-5.6-sol": ((5.00, 0.50, 30.00), (10.00, 1.00, 45.00)),
}
GPT_5_6_LONG_CONTEXT_TOKENS = 272_000
GPT_IMAGE_2_PRICING = {
    "textInput": 5.00,
    "imageInput": 8.00,
    "imageOutput": 30.00,
}
GPT_IMAGE_2_OUTPUT_COSTS = {
    "low": {"square": 0.006, "portrait": 0.005, "landscape": 0.005},
    "medium": {"square": 0.053, "portrait": 0.041, "landscape": 0.041},
    "high": {"square": 0.211, "portrait": 0.165, "landscape": 0.165},
}


def _usage_cost_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for name in ("total", "cost", "amount", "value", "usd"):
            if name in value:
                parsed = _usage_cost_value(value[name])
                if parsed is not None:
                    return parsed
        return None
    try:
        parsed = float(str(value).strip().lstrip("$"))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _usage_token_value(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        if name not in usage:
            continue
        try:
            return max(0, int(usage[name]))
        except (TypeError, ValueError):
            continue
    return 0


def response_usage_metrics(payload: Any, model: str = "") -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    usage = source.get("usage")
    if not isinstance(usage, dict):
        response = source.get("response")
        usage = response.get("usage") if isinstance(response, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    input_tokens = _usage_token_value(
        usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens"
    )
    output_tokens = _usage_token_value(
        usage,
        "output_tokens", "outputTokens", "completion_tokens", "completionTokens"
    )
    total_tokens = _usage_token_value(usage, "total_tokens", "totalTokens")
    if not total_tokens and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    has_token_usage = any(
        name in usage
        for name in (
            "input_tokens", "inputTokens", "prompt_tokens", "promptTokens",
            "output_tokens", "outputTokens", "completion_tokens", "completionTokens",
            "total_tokens", "totalTokens",
        )
    )
    cost = None
    for container in (usage, source):
        for name in ("cost", "total_cost", "totalCost", "cost_usd", "costUsd"):
            if name not in container:
                continue
            cost = _usage_cost_value(container[name])
            if cost is not None:
                break
        if cost is not None:
            break
    cost_source = "response" if cost is not None else ""
    estimated_call_count = 0
    pricing = GPT_5_6_PRICING.get(str(model or "").lower())
    if cost is None and pricing is not None and has_token_usage:
        details = usage.get("input_tokens_details") or usage.get("inputTokensDetails") or {}
        cached_tokens = _usage_token_value(
            details if isinstance(details, dict) else {}, "cached_tokens", "cachedTokens"
        )
        uncached_tokens = max(0, input_tokens - cached_tokens)
        input_rate, cached_rate, output_rate = pricing[
            1 if input_tokens >= GPT_5_6_LONG_CONTEXT_TOKENS else 0
        ]
        cost = (
            uncached_tokens * input_rate
            + cached_tokens * cached_rate
            + output_tokens * output_rate
        ) / 1_000_000
        cost_source = "estimate"
        estimated_call_count = 1
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "callCount": 1,
        "costUsd": cost or 0.0,
        "costedCallCount": 1 if cost is not None else 0,
        "estimatedCallCount": estimated_call_count,
        "costSource": cost_source,
        "hasTokenUsage": has_token_usage,
        "hasCost": cost is not None,
    }


def image_usage_metrics(
    usage_value: Any,
    model: str,
    quality: str,
    size: str,
    partial_images: int = 0,
) -> dict[str, Any]:
    usage = usage_value if isinstance(usage_value, dict) else {}
    actual = response_usage_metrics({"usage": usage})
    if actual["hasCost"]:
        return actual
    input_tokens = _usage_token_value(usage, "input_tokens", "inputTokens")
    output_tokens = _usage_token_value(usage, "output_tokens", "outputTokens")
    has_reported_output_tokens = output_tokens > 0
    details = usage.get("input_tokens_details") or usage.get("inputTokensDetails") or {}
    if not isinstance(details, dict):
        details = {}
    text_tokens = _usage_token_value(details, "text_tokens", "textTokens")
    image_tokens = _usage_token_value(details, "image_tokens", "imageTokens")
    if not text_tokens and not image_tokens:
        text_tokens = input_tokens
    elif input_tokens > text_tokens + image_tokens:
        text_tokens += input_tokens - text_tokens - image_tokens

    clean_model = str(model or "").lower()
    clean_quality = str(quality or "auto").lower()
    size_match = __import__("re").fullmatch(r"(\d+)x(\d+)", str(size or ""))
    if clean_model == "gpt-image-2" and not output_tokens and clean_quality in GPT_IMAGE_2_OUTPUT_COSTS:
        width, height = (
            (int(value) for value in size_match.groups())
            if size_match
            else (1024, 1024)
        )
        orientation = "square" if width == height else "landscape" if width > height else "portrait"
        output_cost = GPT_IMAGE_2_OUTPUT_COSTS[clean_quality][orientation]
        output_tokens = round(output_cost / GPT_IMAGE_2_PRICING["imageOutput"] * 1_000_000)
    if not has_reported_output_tokens:
        output_tokens += max(0, int(partial_images)) * 100
    has_estimate = clean_model == "gpt-image-2" and bool(
        text_tokens or image_tokens or output_tokens
    )
    cost = (
        text_tokens * GPT_IMAGE_2_PRICING["textInput"]
        + image_tokens * GPT_IMAGE_2_PRICING["imageInput"]
        + output_tokens * GPT_IMAGE_2_PRICING["imageOutput"]
    ) / 1_000_000 if has_estimate else 0.0
    return {
        "inputTokens": input_tokens or text_tokens + image_tokens,
        "outputTokens": output_tokens,
        "totalTokens": (input_tokens or text_tokens + image_tokens) + output_tokens,
        "callCount": 1,
        "costUsd": cost,
        "costedCallCount": 1 if has_estimate else 0,
        "estimatedCallCount": 1 if has_estimate else 0,
        "costSource": "estimate" if has_estimate else "",
        "hasTokenUsage": bool(usage) or bool(output_tokens),
        "hasCost": has_estimate,
    }


def merge_reasoning_usage(current: Any, update: Any) -> dict[str, Any]:
    left = current if isinstance(current, dict) else {}
    right = update if isinstance(update, dict) else {}
    call_count = max(0, int(left.get("callCount") or 0)) + max(
        0, int(right.get("callCount") or 0)
    )
    costed_call_count = max(0, int(left.get("costedCallCount") or 0)) + max(
        0, int(right.get("costedCallCount") or 0)
    )
    estimated_call_count = max(0, int(left.get("estimatedCallCount") or 0)) + max(
        0, int(right.get("estimatedCallCount") or 0)
    )
    has_complete_cost = call_count > 0 and costed_call_count == call_count
    response_cost = max(0.0, safe_float(left.get("costUsd"))) + max(
        0.0, safe_float(right.get("costUsd"))
    )
    return {
        "inputTokens": max(0, int(left.get("inputTokens") or 0))
        + max(0, int(right.get("inputTokens") or 0)),
        "outputTokens": max(0, int(left.get("outputTokens") or 0))
        + max(0, int(right.get("outputTokens") or 0)),
        "totalTokens": max(0, int(left.get("totalTokens") or 0))
        + max(0, int(right.get("totalTokens") or 0)),
        "callCount": call_count,
        "costUsd": response_cost if has_complete_cost else 0.0,
        "costedCallCount": costed_call_count,
        "estimatedCallCount": estimated_call_count,
        "costSource": (
            "estimate" if has_complete_cost and estimated_call_count == call_count
            else "mixed" if has_complete_cost and estimated_call_count
            else "response" if has_complete_cost
            else ""
        ),
        "hasTokenUsage": bool(left.get("hasTokenUsage") or right.get("hasTokenUsage")),
        "hasCost": has_complete_cost,
    }


def load_pressure_from_usage_percent(usage_percent: float) -> float:
    value = max(0.0, safe_float(usage_percent))
    pressure_points = (
        (0, 0), (1, 10), (5, 30), (10, 45),
        (25, 65), (50, 82), (100, 100),
    )
    for (left_value, left_pressure), (right_value, right_pressure) in zip(
        pressure_points, pressure_points[1:]
    ):
        if value <= right_value:
            ratio = (value - left_value) / (right_value - left_value)
            return left_pressure + (right_pressure - left_pressure) * ratio
    return 100.0


def interval_load_components(payload: dict[str, Any], cost: float) -> dict[str, Any]:
    spend = max(0.0, safe_float(cost))
    quota = payload.get("quota") or {}
    quota_limit = safe_float(quota.get("limit"))
    quota_percent = spend / quota_limit * 100 if quota_limit > 0 else 0.0

    rate_percent = 0.0
    for item in payload.get("rate_limits") or []:
        limit = safe_float(item.get("limit"))
        if limit > 0:
            rate_percent = max(rate_percent, spend / limit * 100)

    quota_pressure = load_pressure_from_usage_percent(quota_percent)
    rate_pressure = load_pressure_from_usage_percent(rate_percent)
    if quota_pressure > rate_pressure:
        source = "额度"
    elif rate_pressure > quota_pressure:
        source = "速率"
    elif quota_pressure > 0:
        source = "额度和速率"
    else:
        source = "无"
    return {
        "overall": max(quota_pressure, rate_pressure),
        "quota": quota_pressure,
        "rate": rate_pressure,
        "quotaPercent": quota_percent,
        "ratePercent": rate_percent,
        "source": source,
    }


def interval_load_pressure(payload: dict[str, Any], cost: float, seconds: int) -> float:
    del seconds  # Kept for API compatibility; load is now normalized by quota percentages.
    return interval_load_components(payload, cost)["overall"]


def limit_definitions(payload: dict[str, Any] | None) -> dict[str, float]:
    payload = payload or {}
    quota = payload.get("quota") or {}
    windows = {str(item.get("window")): item for item in payload.get("rate_limits") or []}
    return {
        "quota": safe_float(quota.get("limit")),
        "5h": safe_float((windows.get("5h") or {}).get("limit")),
        "1d": safe_float((windows.get("1d") or {}).get("limit")),
        "7d": safe_float((windows.get("7d") or {}).get("limit")),
    }


def annotate_limit_changes(
    payload: dict[str, Any],
    previous: dict[str, Any] | None,
    changed_at: float | None = None,
) -> set[str]:
    if str(payload.get("mode") or "").lower() == "unrestricted":
        payload.pop("_limit_changes", None)
        return set()
    if previous is None:
        payload.pop("_limit_changes", None)
        return set()

    now = changed_at if changed_at is not None else time.time()
    current_limits = limit_definitions(payload)
    previous_limits = limit_definitions(previous)
    previous_changes = previous.get("_limit_changes") or {}
    changes: dict[str, dict[str, float]] = {}
    changed_names: set[str] = set()

    for name, current in current_limits.items():
        old = previous_limits[name]
        if abs(current - old) > 1e-9:
            changes[name] = {"previous": old, "current": current, "changedAt": now}
            changed_names.add(name)
            continue

        carried = previous_changes.get(name) or {}
        carried_at = safe_float(carried.get("changedAt"))
        if carried_at > 0 and now - carried_at <= LIMIT_CHANGE_DISPLAY_SECONDS:
            changes[name] = {
                "previous": safe_float(carried.get("previous")),
                "current": current,
                "changedAt": carried_at,
            }

    if changes:
        payload["_limit_changes"] = changes
    else:
        payload.pop("_limit_changes", None)
    return changed_names
