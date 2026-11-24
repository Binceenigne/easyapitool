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
            description="读取本轮 OpenAI 官方图片搜索的候选，下载并检视真实图片。image_urls 为空时读取当前候选；只能读取实际搜索结果中的URL。工具返回可见图片与候选ID，不能把网页链接当作图片。",
            params_json_schema=INSPECT_SCHEMA,
            on_invoke_tool=invocation(inspect_callback, {"image_urls"}, "image_urls", 12),
            strict_json_schema=True,
            _use_default_failure_error_function=False,
        ),
        FunctionTool(
            name=SELECT_TOOL_NAME,
            description="根据已检视的真实图片，选择本次生图要实际采用的参考图ID，并说明用途。只能选择检视成功的ID，数量受当前档位与剩余参考槽位限制；不需要采用时传空数组并说明原因。",
            params_json_schema=SELECT_SCHEMA,
            on_invoke_tool=invocation(select_callback, {"candidate_ids", "rationale"}, "candidate_ids", 8),
            strict_json_schema=True,
            _use_default_failure_error_function=False,
        ),
    ]
