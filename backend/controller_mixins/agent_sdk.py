from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mirra_agents import AgentRequest
from mirra_agents.retry import max_attempts
from mirra_agents.visual_tools import make_visual_reference_tools
from mirra_agents.reference_policy import VISUAL_REFERENCE_POLICY

from ..agent_references import VisualReferenceSession
from ..agent_workspace import ImageWorkspaceSession
from ..common import (
    PROMPT_RESULT_MARKER, resolve_image_reasoning_config,
)
from ..prompt_targets import format_prompt_instructions
from ..usage import response_usage_metrics, merge_reasoning_usage

STAT_FIELDS = ("turnCount", "requestCount", "toolCallCount", "webSearchCallCount", "localToolCallCount")


def run_prompt_agent(controller: Any, record: Any, secret: str, prompt: str,
                     image_paths: tuple[Path, ...], reasoning_mode: str,
                     web_search_enabled: bool, emit: Any,
                     continuation_context: dict[str, Any] | None = None,
                     visible_assets: list[dict[str, Any]] | None = None,
                     task_context: Any = None,
                     reasoning_config: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    config = dict(reasoning_config or resolve_image_reasoning_config(reasoning_mode))
    model, effort = str(config["model"]), str(config["effort"])
    attached_paths = {path.resolve() for path in image_paths}
    visible = [asset for asset in visible_assets or [] if Path(str(asset.get("path") or "")).resolve() in attached_paths]
    workspace: ImageWorkspaceSession | None = None
    turns: dict[int, dict[str, Any]] = {}
    raw_text: dict[int, str] = {}
    published: dict[int, str] = {}
    current_turn = 0
    usage: dict[str, Any] = {}
    sources: dict[str, dict[str, str]] = {}

    def turn_record(number: int) -> dict[str, Any]:
        return turns.setdefault(number, {"turn": number, "title": "正在完善方案", "text": "", "status": "running", "tools": []})

    def visible_text(number: int, flush: bool = False) -> None:
        text = raw_text.get(number, "")
        marker = text.find(PROMPT_RESULT_MARKER)
        end = marker if marker >= 0 else len(text) if flush else max(0, len(text) - len(PROMPT_RESULT_MARKER) + 1)
        summary = text[:end]
        previous = published.get(number, "")
        if len(summary) > len(previous) and summary.startswith(previous):
            delta = summary[len(previous):]
            emit("react_turn_delta", turn=number, delta=delta)
            emit("react_summary_delta", delta=delta)
        published[number] = summary
        turn_record(number)["text"] = summary.strip()

    def tool_event(kind: str, event: dict[str, Any], tool_name: str) -> None:
        number = int(event.get("turn") or current_turn or 1)
        details = {name: value for name, value in event.items() if name != "type"}
        details.update(turn=number, tool=tool_name)
        arguments = details.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        for name in ("query", "queries", "action", "url"):
            if event.get(name):
                arguments[name] = event[name]
        details["arguments"] = arguments
        call_id = str(event.get("callId") or "")
        tools = turn_record(number)["tools"]
        tool = next((item for item in tools if item["callId"] == call_id), None)
        if tool is None:
            tool = {"callId": call_id, "name": tool_name, "arguments": {}, "status": "running", "resultCount": 0}
            tools.append(tool)
        tool["arguments"].update(arguments)
        tool["status"] = "running" if kind == "react_tool_started" else "failed" if kind == "react_tool_failed" else "completed"
        tool["error"] = str(event.get("error") or "")
        tool["resultCount"] = int(event.get("resultCount") or len(event.get("sources") or []) + len(event.get("images") or []))
        details["resultCount"] = tool["resultCount"]
        emit(kind, **details)

    def progress(event: dict[str, Any]) -> None:
        nonlocal current_turn, usage
        kind = event["type"]
        if kind == "turn_started":
            current_turn = int(event["turn"])
            turn = turn_record(current_turn)
            turn["status"] = "running"
            turn["title"] = "正在分析创作需求" if current_turn == 1 else "正在完善方案"
            if event.get("reset"):
                raw_text[current_turn] = published[current_turn] = ""
                turn["text"] = ""
            summary = "\n\n".join(item.get("text", "") for number, item in sorted(turns.items()) if number < current_turn and item.get("text"))
            emit("react_turn_started", turn=current_turn, title=turn["title"], reset=bool(event.get("reset")), summary=summary)
        elif kind == "text_delta":
            raw_text[current_turn] = raw_text.get(current_turn, "") + str(event.get("delta") or "")
            visible_text(current_turn)
        elif kind == "turn_completed":
            response = event.get("response") or {"usage": event.get("usage")}
            full_text = "".join(part.get("text", "") for output in response.get("output") or [] for part in output.get("content") or [] if part.get("type") == "output_text")
            if full_text:
                raw_text[current_turn] = full_text
            visible_text(current_turn, True)
            current = response_usage_metrics(response, model)
            usage = merge_reasoning_usage(usage, current)
            turn = turn_record(current_turn)
            turn.update(status="completed", title="方案已更新", usage=current)
            emit("react_turn_completed", turn=current_turn, title=turn["title"], text=turn["text"], usage=current, reasoningUsage=usage)
        elif kind == "retry":
            emit("request_retry", **{name: value for name, value in event.items() if name != "type"})
        elif kind in {"tool_started", "tool_completed", "tool_failed"}:
            mapped = {"tool_started": "react_tool_started", "tool_completed": "react_tool_completed", "tool_failed": "react_tool_failed"}[kind]
            tool_event(mapped, event, str(event.get("tool") or ""))
        elif kind in {"web_search_started", "web_search_completed", "web_search_failed"}:
            for source in event.get("sources") or []:
                if source.get("url"):
                    sources[source["url"]] = source
            mapped = {"web_search_started": "react_tool_started", "web_search_completed": "react_tool_completed", "web_search_failed": "react_tool_failed"}[kind]
            tool_event(mapped, event, "web_search")

    reference_limit = controller._tier_reference_limit(record)
    max_references = min(reference_limit, max(0, int(config.get("max_references", 0))))
    output_format = str((config.get("outputOptions") or {}).get("output_format") or "").lower()

    def vision_limit() -> int:
        return max(0, reference_limit - (len(workspace.viewed) if workspace is not None else len(image_paths)))

    def adoption_limit() -> int:
        originals = len(workspace.selected) if workspace is not None else len(image_paths)
        return 0 if output_format == "svg" else max(0, min(max_references, reference_limit - originals))

    inspect_limit = min(12, max(0, int(config.get("visual_search_results", 6))), vision_limit())
    planning_budget = int(config["max_turns"])
    io_budget = 3 if (web_search_enabled and inspect_limit > 0) or continuation_context else 0
    total_budget = min(32, planning_budget + io_budget)
    references = VisualReferenceSession(controller.web_search if web_search_enabled else None, inspect_limit=inspect_limit,
        adopt_limit=adoption_limit, vision_limit=vision_limit, emit=emit, task_context=task_context)
    if continuation_context:
        workspace = ImageWorkspaceSession(continuation_context, visible, reference_limit=reference_limit, emit=emit,
            web_inspected_count=lambda: len(references.inspected), web_adopted_count=lambda: len(references.adopted),
            task_context=task_context)

    instructions = (
        "你是图片创作 Agent，根据用户目标自主完成构思、必要查阅与最终生图提示词。"
        "输出简短的公开工作摘要。"
        f"当前思维模式 {reasoning_mode}，思考深度 {effort}，可用模型轮数 {total_budget}。"
    )
    tools = []
    if web_search_enabled:
        instructions += VISUAL_REFERENCE_POLICY
        if inspect_limit > 0:
            tools.extend(make_visual_reference_tools(references.inspect, references.select))
    if workspace is not None:
        instructions += (
            "当前为继续编辑，父集成果和本轮上传已附在输入中，默认沿用为编辑素材。"
            "可自主用 list_image_workspace 查历史与素材路径、view_image_workspace_assets 看图，"
            "或 select_image_workspace_inputs 调整本轮操作和参考图。"
        )
        tools.extend(workspace.tools())
    if reasoning_mode == "instant":
        instructions += "即时模式保留用户原始提示词，直接完成当前编辑要求。"
    instructions += f"工作摘要后单独输出标记 {PROMPT_RESULT_MARKER}，标记后只写可直接提交图片模型的最终提示词。\n" + format_prompt_instructions(config.get("outputOptions"))

    def validate_final(response: dict[str, Any]) -> None:
        if any(item.get("type") == "function_call" for item in response.get("output") or []):
            return
        final = "".join(part.get("text", "") for item in response.get("output") or [] for part in item.get("content") or [] if part.get("type") == "output_text")
        if PROMPT_RESULT_MARKER not in final or not all(part.strip() for part in final.split(PROMPT_RESULT_MARKER, 1)):
            raise ConnectionError("Agent 结果格式不完整：缺少工作摘要或最终生图提示词")

    initial = json.dumps({"currentRequest": prompt, "workspace": workspace.summary(),
        "visibleAssets": [workspace._metadata(str(asset["assetId"])) for asset in visible
                          if str(asset.get("assetId") or "") in workspace.viewed]}, ensure_ascii=False) if workspace else prompt
    emit("react_started", mode=reasoning_mode, model=model, reasoningEffort=effort, webSearchEnabled=web_search_enabled)
    request = AgentRequest(base_url=record["base_url"], api_key=secret, model=model, instructions=instructions,
        input=controller._agent_input(initial, image_paths), effort=effort, mode=reasoning_mode,
        web_search_enabled=web_search_enabled, web_image_search_enabled=web_search_enabled and inspect_limit > 0,
        web_image_max_results=max(1, inspect_limit), max_turns=total_budget,
        max_attempts=max_attempts("agent"),
        validate_output=validate_final, response_observer=references.observe if web_search_enabled else None,
        final_output_feedback=None)
    result = controller._run_sdk_agent(request, cancel_event=getattr(task_context, "cancel_event", None), tools=tools, on_event=progress)
    summary, final_prompt = str(result["text"]).split(PROMPT_RESULT_MARKER, 1)
    if reasoning_mode == "instant":
        final_prompt = prompt
    for source in result.get("sources") or []:
        if source.get("url"):
            sources[source["url"]] = source
    values = {
        "mode": reasoning_mode, "model": model, "reasoningEffort": effort,
        "summary": summary.strip(), "prompt": final_prompt.strip(),
        "webSearchEnabled": web_search_enabled, "webSearchUsed": bool(result.get("webSearchUsed")),
        "webSearchFailed": False, "webSearchResultCount": len(references.candidates) + len(sources),
        "webReferenceCount": len(references.adopted), "webSearchSources": list(sources.values()),
        **references.metadata,
        "reasoningDurationMs": max(1, round((time.perf_counter() - started) * 1000)), "reasoningUsage": usage,
        "reasoningTurns": [turn for _, turn in sorted(turns.items())],
        **{name: int(result.get(name) or 0) for name in STAT_FIELDS},
    }
    emit("react_completed", **values)
    return {**values, "webCandidates": references.adopted, "inspectedCandidates": references.inspected_candidates,
            "webSelectionRationale": references.rationale, "continuationPlan": workspace.plan if workspace else None}
