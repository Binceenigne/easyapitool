"""Workspace-scoped image tools shared by the desktop and private worker."""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable

from .retry import safe_text


WORKSPACE_TOOL_NAMES = frozenset({"list_image_workspace", "view_image_workspace_assets", "select_image_workspace_inputs"})

WORKSPACE_SCHEMAS = {
    "list_image_workspace": {
        "type": "object", "properties": {
            "kind": {"type": "string", "enum": ["history", "assets"]},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, "required": ["kind", "offset", "limit"], "additionalProperties": False,
    },
    "view_image_workspace_assets": {
        "type": "object", "properties": {
            "asset_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 16},
        }, "required": ["asset_ids"], "additionalProperties": False,
    },
    "select_image_workspace_inputs": {
        "type": "object", "properties": {
            "operation": {"type": "string", "enum": ["edit", "generate"]},
            "asset_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 1600},
        }, "required": ["operation", "asset_ids", "rationale"], "additionalProperties": False,
    },
}


def _valid(name: str, values: Any) -> bool:
    if not isinstance(values, dict) or set(values) != set(WORKSPACE_SCHEMAS[name]["required"]):
        return False
    if name == "list_image_workspace":
        return (values["kind"] in {"history", "assets"}
                and type(values["offset"]) is int and values["offset"] >= 0
                and type(values["limit"]) is int and 1 <= values["limit"] <= 50)
    ids = values["asset_ids"]
    if (not isinstance(ids, list) or len(ids) > 16
            or any(not isinstance(value, str) or not value or len(value) > 128 for value in ids)):
        return False
    if name == "view_image_workspace_assets":
        return bool(ids)
    return (values["operation"] in {"edit", "generate"} and isinstance(values["rationale"], str)
            and 0 < len(values["rationale"].strip()) <= 1600)


def make_workspace_tools(list_callback: Callable[..., Any], view_callback: Callable[..., Any],
                         select_callback: Callable[..., Any]) -> list[Any]:
    from agents import FunctionTool

    def invocation(name: str, callback: Callable[..., Any]) -> Any:
        async def invoke(context: Any, arguments: str) -> Any:
            try:
                values = json.loads(arguments)
                if not _valid(name, values):
                    raise ValueError("工作区工具参数无效，请使用已列出的素材 ID 与分页范围")
            except (ValueError, TypeError) as error:
                return json.dumps({"error": {"code": "invalid_workspace_arguments", "message": safe_text(error)}}, ensure_ascii=False)
            result = callback(context, values)
            return await result if inspect.isawaitable(result) else result
        return invoke

    specs = (
        ("list_image_workspace", "浏览当前图片工作区的历史轮次或素材目录，包含素材ID、路径和来源。", list_callback),
        ("view_image_workspace_assets", "按素材ID查看工作区中的真实图片，可按需调用。", view_callback),
        ("select_image_workspace_inputs", "决定本轮操作和参考素材，可重新选择；默认沿用父集成果和当前上传图片。", select_callback),
    )
    return [FunctionTool(name=name, description=description, params_json_schema=WORKSPACE_SCHEMAS[name],
                         on_invoke_tool=invocation(name, callback), strict_json_schema=True,
                         _use_default_failure_error_function=False) for name, description, callback in specs]
