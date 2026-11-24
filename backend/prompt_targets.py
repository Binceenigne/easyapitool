from __future__ import annotations

import re
from typing import Any


EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def permits(values: Any, value: str) -> bool:
    return isinstance(values, list) and ("*" in values or value in values)


def format_prompt_instructions(options: dict[str, Any] | None) -> str:
    options = options if isinstance(options, dict) else {}
    base = (
        "生成质量、分辨率、输出格式、透明度及帧数由独立 API 字段控制，"
        "不要在提示词中重新设定这些技术参数。"
    )
    output = str(options.get("output_format") or options.get("outputFormat") or "png").lower()
    if output == "svg":
        return base + (
            "\n输出目标是静态 SVG 矢量图。最终提示词用【主体与轮廓】【路径与分层】"
            "【色彩与留白】【矢量约束】四个简短文本块描述清晰的几何形状、可编辑的曲线、"
            "前后层次、色彩与图形关系。保持边缘准确、文字可读，不依赖摄影噪点或位图纹理。"
            "只输出自然语言设计提示词，不输出 SVG 代码、时间轴或动画要求。"
        )
    if output == "gif":
        try:
            frames = int(options.get("frame_count", options.get("frameCount", 9)))
        except (TypeError, ValueError):
            frames = 9
        if frames not in (4, 9, 12, 16):
            frames = 9
        return base + (
            f"\n输出目标是 {frames} 帧循环 GIF 动图。最终提示词用【画面设定】【运动脚本】"
            "【分帧控制】【循环衔接】【稳定约束】五个简短文本块表达。明确起始姿态、动作方向、"
            "幅度、运动节奏和关键阶段；主体身份、几何、配色、背景和机位应跨帧一致。"
            "连续动作应自然推进，避免静态拼贴、闪烁、跳变、瞬移和文字抖动。"
            "最后一帧自然接回第一帧；保留用户指定的镜头运动，不擅自添加新的运动。"
        )
    return base


def choose_reasoning_pair(
    profile: dict[str, Any] | None,
    preferred_model: str,
    preferred_effort: str,
    *,
    purpose: str,
) -> tuple[str, str]:
    if not isinstance(profile, dict):
        return preferred_model, preferred_effort
    tier = profile.get("tier")
    if isinstance(tier, dict):
        flag = "promptPolish" if purpose == "polish" else "imageEditing"
        if not tier.get(flag):
            raise ValueError("当前密钥档位未开放" + ("提示词润色" if purpose == "polish" else "图片续作"))
    else:
        tier = None
    explicit = profile.get(purpose)
    if purpose == "continuation" and isinstance(explicit, dict):
        # The gateway publishes this exact utility pair for basic image
        # editing. It is separate from the models/depths users may select for
        # planning; never substitute a caller-preferred pair here.
        model, effort = str(explicit.get("model") or ""), str(explicit.get("effort") or "")
        capabilities = (profile.get("reasoningCapabilities") or {}).get(model) or {}
        supported = capabilities.get("efforts")
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model)
                or effort not in ("", *EFFORT_ORDER)
                or supported and effort and effort not in supported):
            raise ValueError("网关未配置有效的图片续作模型和深度")
        return model, effort
    if purpose == "continuation" and tier is not None:
        raise ValueError("网关尚未下发图片续作的模型配置")
    slots = profile.get("reasoning") or {}
    order = ("terra", "luna", "sol") if purpose == "polish" else ("luna", "terra", "sol")
    candidates: list[tuple[str, str]] = []
    if purpose in profile and isinstance(explicit, dict):
        model, effort = str(explicit.get("model") or ""), str(explicit.get("effort") or "")
        if not model or (tier and (not permits(tier.get("reasoningModels"), model)
                                  or not permits(tier.get("reasoningEfforts"), effort))):
            raise ValueError("当前密钥档位没有可用于此操作的思维模型和深度")
        return model, effort
    for slot in order:
        preset = slots.get(slot) or {}
        if preset.get("model"):
            candidates.append((str(preset["model"]), preferred_effort))
    candidates.append((preferred_model, preferred_effort))
    capabilities = profile.get("reasoningCapabilities") or {}
    for model, effort in candidates:
        if tier and not permits(tier.get("reasoningModels"), model):
            continue
        documented = (capabilities.get(model) or {}).get("efforts")
        allowed = [v for v in EFFORT_ORDER if (not documented or v in documented)
                   and (not tier or permits(tier.get("reasoningEfforts"), v))]
        if effort in allowed:
            return model, effort
        if "medium" in allowed:
            return model, "medium"
        if allowed:
            return model, allowed[0]
    raise ValueError("当前密钥档位没有可用于此操作的思维模型和深度")


def validate_reasoning_config(
    profile: dict[str, Any] | None, mode: str, config: dict[str, Any] | None,
) -> None:
    tier = profile.get("tier") if isinstance(profile, dict) else None
    if not isinstance(tier, dict):
        return
    if mode == "advanced" and not tier.get("advanced"):
        raise ValueError("当前密钥档位未开放高级模式")
    if mode not in ("instant", "advanced") and mode not in (profile.get("reasoningModes") or {}):
        raise ValueError("当前密钥档位未开放此思维深度预设")
    if config:
        if not permits(tier.get("reasoningModels"), str(config.get("model") or "")):
            raise ValueError("当前密钥档位未开放此思维模型")
        if not permits(tier.get("reasoningEfforts"), str(config.get("effort") or "")):
            raise ValueError("当前密钥档位未开放此模型思考深度")


def stored_model_profile(store: Any, key_id: str) -> dict[str, Any] | None:
    method = getattr(store, "latest_payload", None)
    snapshot = method(key_id) if callable(method) else None
    profile = snapshot.get("appModels") if isinstance(snapshot, dict) else None
    return profile if isinstance(profile, dict) else None


def apply_reasoning_profile(
    profile: dict[str, Any] | None, mode: str, config: dict[str, Any] | None,
    instant_fallback: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(profile, dict):
        return config
    tier = profile.get("tier")
    slots = profile.get("reasoning") or {}
    modes = profile.get("reasoningModes") or {}
    if mode == "advanced":
        preset = slots.get((config or {}).get("modelKey")) or {}
        if tier and (not tier.get("advanced") or not preset.get("model")):
            raise ValueError("当前密钥档位未开放此高级思维模型")
        if config and preset.get("model"):
            config = {**config, "model": str(preset["model"])}
            cap = (profile.get("reasoningCapabilities") or {}).get(config["model"]) or {}
            allowed = cap.get("efforts") or []
            if allowed and config.get("effort") not in allowed:
                config["effort"] = cap.get("default") if cap.get("default") in allowed else allowed[0]
    else:
        if tier and mode != "instant" and mode not in modes:
            raise ValueError("当前密钥档位未开放此思维深度预设")
        slot = {"flash": "luna", "medium": "luna", "high": "terra", "extra": "terra", "max": "sol"}.get(mode)
        preset = modes.get(mode) or slots.get(slot) or {}
        if preset.get("model"):
            config = dict(config or {**instant_fallback, "mode": "instant"})
            config["model"] = str(preset["model"])
            if "effort" in preset:
                config["effort"] = str(preset["effort"] or "")
    validate_reasoning_config(profile, mode, config)
    return config
