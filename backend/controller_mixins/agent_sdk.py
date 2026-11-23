from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mirra_agents import AgentRequest
from mirra_agents.retry import AgentCancelled, AgentRunFailure, failure_details, max_attempts

from ..common import (
    IMAGE_CONTINUATION_PLAN_TOOL, PROMPT_RESULT_MARKER, image_continuation_prompt,
    parse_image_continuation_plan, resolve_image_reasoning_config,
)
from ..image_editor import image_preview_data_url
from ..prompt_targets import format_prompt_instructions
from ..usage import response_usage_metrics, merge_reasoning_usage


def run_prompt_agent(controller: Any, record: Any, secret: str, prompt: str, image_paths: tuple[Path, ...], reasoning_mode: str, web_search_enabled: bool, emit: Any, continuation_context: dict[str, Any] | None = None, visible_assets: list[dict[str, Any]] | None = None, task_context: Any = None, reasoning_config: dict[str, Any] | None = None) -> dict[str, Any]:
    from agents import FunctionTool
    from agents.tool import ToolOutputImage, ToolOutputText

    started = time.perf_counter()
    config = dict(reasoning_config or resolve_image_reasoning_config(reasoning_mode))
    model, effort = str(config["model"]), str(config["effort"])
    visible = list(visible_assets or [])
    available = list((continuation_context or {}).get("assets") or [])
    by_id = {str(asset.get("assetId")):asset for asset in available if asset.get("assetId")}
    visible_ids = [str(asset["assetId"]) for asset in visible if asset.get("assetId")]
    required_ids = [str(asset["assetId"]) for asset in visible if asset.get("assetId") and not str(asset.get("description") or "").strip()]
    plan: dict[str, Any] | None = None
    text, published = "", 0
    turn, turn_start = 0, 0
    usage: dict[str, Any] = {}
    search_calls: set[str] = set()
    sources: dict[str, dict[str, str]] = {}

    def check() -> None:
        if task_context is not None:
            task_context.check_cancelled()

    def publish_text(delta: str = "", flush: bool = False) -> None:
        nonlocal text, published
        text += delta
        marker = text.find(PROMPT_RESULT_MARKER)
        stop = marker if marker >= 0 else len(text) if flush else max(0,len(text)-len(PROMPT_RESULT_MARKER)+1)
        if stop > published:
            visible_delta = text[published:stop]
            emit("react_turn_delta",turn=turn,delta=visible_delta)
            emit("react_summary_delta",delta=visible_delta)
            published = stop

    def progress(event: dict[str, Any]) -> None:
        nonlocal text, published, turn, turn_start, usage
        kind = event["type"]
        if kind == "turn_started":
            if event.get("reset"):
                text = text[:turn_start]; published = min(published,turn_start)
            else:
                turn_start = len(text)
            turn = int(event["turn"])
            emit("react_turn_started",turn=turn,title="正在分析创作需求" if turn==1 else "正在完善方案",reset=bool(event.get("reset")),summary=text[:turn_start])
        elif kind == "text_delta":
            publish_text(str(event.get("delta") or ""))
        elif kind == "turn_completed":
            publish_text(flush=True)
            current = response_usage_metrics(event.get("response") or {"usage":event.get("usage")},model)
            usage = merge_reasoning_usage(usage,current)
            emit("react_turn_completed",turn=turn,title="方案已更新",text=text[turn_start:published].strip(),usage=current,reasoningUsage=usage)
        elif kind == "retry":
            emit("request_retry",**{name:value for name,value in event.items() if name!="type"})
        elif kind in {"tool_started","tool_completed"}:
            details = {name:value for name,value in event.items() if name!="type"}
            if isinstance(details.get("arguments"),str):
                try: details["arguments"]=json.loads(details["arguments"])
                except ValueError: details["arguments"]={}
            emit("react_tool_started" if kind=="tool_started" else "react_tool_completed",**details)
        elif kind == "web_search_started":
            identifier = str(event.get("callId") or f"web-search-{turn}")
            if identifier not in search_calls:
                search_calls.add(identifier)
                emit("react_tool_started",turn=turn,callId=identifier,tool="web_search",arguments={"provider":"OpenAI"})
        elif kind == "web_search_completed":
            for source in event.get("sources") or []:
                if source.get("url"): sources[source["url"]]=source
            identifier=str(event.get("callId") or (list(search_calls)[-1] if search_calls else f"web-search-{turn}"))
            search_calls.add(identifier)
            emit("react_tool_completed",turn=turn,callId=identifier,tool="web_search",resultCount=len(event.get("sources") or []))

    async def continuation_tool(context: Any, arguments: str) -> Any:
        nonlocal plan
        check()
        if plan is None:
            plan = parse_image_continuation_plan(json.loads(arguments),list(by_id),visible_ids,required_ids)
            emit("react_continuation_planned",operation=plan["operation"],selectedAssetIds=plan["selectedAssetIds"],rationale=plan["rationale"])
        output: list[Any] = [ToolOutputText(text=json.dumps({"operation":plan["operation"],"selectedAssetIds":plan["selectedAssetIds"],"rationale":plan["rationale"],"message":"已加载所选素材；请依据这些素材与用户要求完成最终方案。"},ensure_ascii=False))]
        for asset_id in plan["selectedAssetIds"]:
            if asset_id in visible_ids:
                continue
            asset = by_id[asset_id]; file = Path(str(asset.get("path") or ""))
            if not file.is_file():
                raise ValueError(f"所选续作素材已不存在：{asset_id}")
            output.extend([ToolOutputText(text=json.dumps({"assetId":asset_id,"description":str(asset.get("description") or "")},ensure_ascii=False)),ToolOutputImage(image_url=image_preview_data_url(file.read_bytes(),max_side=1024))])
        return output

    instructions = (
        "你是用于图片创作的视觉规划 Agent。先理解用户目标和明确约束，判断会影响主体外观、事实、构图或时效的信息缺口。"
        "纯想象创作或已有信息充分时直接设计；信息缺口重要且联网可用时，用 OpenAI 内置 web_search 做针对性检索。"
        "保留用户指定的现有角色、品牌、作品名称及标志性特征，不擅自改成原创替代。"
        "输出简短、可审计的创作工作摘要，不输出隐藏思维链。"
        f"本档深度建议：{config['depth']}。"
    )
    if config.get("advanced"):
        instructions += "高级模式完整执行需求理解、方案比较、必要检索和视觉/事实复核；在当前轮数预算内自行决定步骤。"
    if web_search_enabled:
        instructions += "联网只使用官方托管 web_search。按需检索并依据真实来源优化提示词。网页来源不是已验证的图片素材，不要伪造已加载图片或声称看过来源中的图片。"
    if continuation_context:
        instructions += (
            "当前为跨轮续作。第一轮必须调用 plan_image_continuation，独立判断局部 edit 或 generate 延续创作，并按assetId选择必要素材。"
            "edit必须选图；generate可选图保持一致性。为descriptionRequiredAssetIds所有新读图片提供客观可复用描述。"
            "父轮附带图按visibleAssetIds排序；更早素材先依据目录筛选，工具会加载被选原图，不要默认占用全部参考槽位。"
        )
    instructions += f"工作摘要后单独输出标记 {PROMPT_RESULT_MARKER}，标记后只写可直接提交图片模型的最终提示词。\n"+format_prompt_instructions(config.get("outputOptions"))
    initial = image_continuation_prompt(prompt,continuation_context or {},visible) if continuation_context else prompt
    local_tools=[]
    if continuation_context:
        spec=IMAGE_CONTINUATION_PLAN_TOOL
        local_tools.append(FunctionTool(name=spec["name"],description=spec["description"],params_json_schema=spec["parameters"],on_invoke_tool=continuation_tool,is_enabled=lambda _context,_agent:plan is None,strict_json_schema=True,_use_default_failure_error_function=False))

    def validate_final(response: dict[str, Any]) -> None:
        if any(item.get("type")=="function_call" for item in response.get("output") or []):
            return
        final = "".join(part.get("text","") for item in response.get("output") or [] for part in item.get("content") or [] if part.get("type")=="output_text")
        if PROMPT_RESULT_MARKER not in final or not all(part.strip() for part in final.split(PROMPT_RESULT_MARKER,1)):
            raise ConnectionError("Agent 结果格式不完整：缺少工作摘要或最终生图提示词")
        if continuation_context and plan is None:
            raise ConnectionError("Agent 未返回续作操作与素材规划")

    emit("react_started",mode=reasoning_mode,model=model,reasoningEffort=effort,webSearchEnabled=web_search_enabled)
    request=AgentRequest(base_url=record["base_url"],api_key=secret,model=model,instructions=instructions,input=controller._agent_input(initial,image_paths),effort=effort,mode=reasoning_mode,web_search_enabled=web_search_enabled,max_turns=int(config["max_turns"])+(1 if continuation_context else 0),max_attempts=max_attempts("agent"),tool_choice="plan_image_continuation" if continuation_context else None,validate_output=validate_final)
    result=controller._run_sdk_agent(request,cancel_event=getattr(task_context,"cancel_event",None),tools=local_tools,on_event=progress)
    complete=text or result["text"]
    summary,final_prompt=complete.split(PROMPT_RESULT_MARKER,1)
    summary,final_prompt=summary.strip(),final_prompt.strip()
    duration=max(1,round((time.perf_counter()-started)*1000))
    searched=bool(result.get("webSearchUsed"))
    values={"mode":reasoning_mode,"model":model,"reasoningEffort":effort,"summary":summary,"prompt":final_prompt,"webSearchEnabled":web_search_enabled,"webSearchUsed":searched,"webSearchFailed":False,"webSearchResultCount":len(sources),"webReferenceCount":0,"reasoningDurationMs":duration,"reasoningUsage":usage,"webSearchSources":list(sources.values())}
    emit("react_completed",**values)
    return {**values,"webCandidates":[],"webSelectionRationale":"","continuationPlan":plan}
