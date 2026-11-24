"""Host-provided visual inspection tools; discovery stays in OpenAI web search."""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable

from .retry import safe_text

INSPECT_TOOL_NAME = "inspect_visual_references"
SELECT_TOOL_NAME = "select_visual_references"
VISUAL_TOOL_NAMES = (INSPECT_TOOL_NAME, SELECT_TOOL_NAME)

INSPECT_SCHEMA = {
    "type": "object",
    "properties": {"image_urls": {"type": "array", "items": {"type": "string"}, "maxItems": 12}},
    "required": ["image_urls"],
    "additionalProperties": False,
}
SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "rationale": {"type": "string"},
    },
    "required": ["candidate_ids", "rationale"],
    "additionalProperties": False,
}


def make_visual_reference_tools(inspect_callback: Callable[..., Any], select_callback: Callable[..., Any]) -> list[Any]:
    """Callbacks receive ``(ToolContext, parsed_arguments)`` and may be async.

    The inspection callback must accept URLs only from this run's actual hosted
    image results, validate downloads, and return ToolOutputImage with verified
    bytes. The selection callback must accept only successfully inspected IDs.
    The host enforces its current reference and format permissions.
    """
    from agents import FunctionTool

    def invocation(callback: Callable[..., Any], names: set[str], array_key: str, maximum: int):
        async def call(context: Any, arguments: str) -> Any:
            try:
                fields = json.loads(arguments)
                if not isinstance(fields, dict) or set(fields) != names:
                    raise ValueError("工具参数字段不完整或包含未知字段")
                values = fields[array_key]
                if not isinstance(values, list) or len(values) > maximum or any(
                    not isinstance(value, str) or not value.strip() or len(value) > 4096 for value in values
                ):
                    raise ValueError("参考图参数必须为允许数量内的非空字符串数组")
                if "rationale" in fields and (not isinstance(fields["rationale"], str) or not fields["rationale"].strip() or len(fields["rationale"]) > 1600):
                    raise ValueError("请简短说明采用或不采用这些参考图的原因")
            except (ValueError, TypeError, KeyError) as error:
                return json.dumps({"error": {"code": "invalid_reference_arguments", "message": safe_text(error)}}, ensure_ascii=False)
            output = callback(context, fields)
            return await output if inspect.isawaitable(output) else output
        return call

    return [
        FunctionTool(
            name=INSPECT_TOOL_NAME,
            description="查看本轮图片搜索的真实候选，返回图片、ID及读取结果。image_urls 使用搜索返回的原图URL；留空时查看尚未检视的候选。",
            params_json_schema=INSPECT_SCHEMA,
            on_invoke_tool=invocation(inspect_callback, {"image_urls"}, "image_urls", 12),
            strict_json_schema=True,
            _use_default_failure_error_function=False,
        ),
        FunctionTool(
            name=SELECT_TOOL_NAME,
            description="按任务需要选择已检视的图片ID作为生图参考，并简述用途；不需要时可传空数组。数量受当前档位和可用参考图槽位限制。",
            params_json_schema=SELECT_SCHEMA,
            on_invoke_tool=invocation(select_callback, {"candidate_ids", "rationale"}, "candidate_ids", 8),
            strict_json_schema=True,
            _use_default_failure_error_function=False,
        ),
    ]
