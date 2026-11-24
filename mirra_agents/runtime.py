from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
import threading
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlsplit

from .retry import AgentCancelled, AgentRunFailure, ProviderResponseError, check_cancelled, classify_error, failure_details, max_attempts, retry_delay, safe_text, wait_async


@dataclass
class AgentRequest:
    base_url: str
    api_key: str
    model: str
    instructions: str
    input: str | list[dict[str, Any]]
    effort: str = ""
    mode: str = ""
    web_search_enabled: bool = False
    web_image_search_enabled: bool | None = None
    web_image_max_results: int = 6
    max_turns: int = 12
    max_output_tokens: int = 8192
    max_attempts: int = 3
    timeout: float = 600
    headers: dict[str, str] | None = None
    proxy_url: str | None = None
    trust_env: bool | None = None
    ssl_context: Any = field(default=None, repr=False)
    http_client: Any = field(default=None, repr=False)
    tool_choice: Any = None
    validate_output: Any = field(default=None, repr=False)
    response_observer: Any = field(default=None, repr=False)
    final_output_feedback: Any = field(default=None, repr=False)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def _sources(response: dict[str, Any]) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        action = item.get("action") or {}
        if action.get("type") in {"open_page", "find_in_page"} and str(action.get("url") or "").startswith(("https://", "http://")):
            found[action["url"]] = {"url": action["url"], "title": ""}
        for source in action.get("sources") or []:
            if isinstance(source, dict) and str(source.get("url") or "").startswith(("https://", "http://")):
                found[source["url"]] = {"url": source["url"], "title": str(source.get("title") or "")}
        for content in item.get("content") or []:
            for source in content.get("annotations") or []:
                if source.get("type") == "url_citation" and str(source.get("url") or "").startswith(("https://", "http://")):
                    found[source["url"]] = {"url": source["url"], "title": str(source.get("title") or "")}
    return list(found.values())


def _web_images(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep documented image_result metadata, including SDK extension fields.

    These are search candidates, never claimed to be downloaded/inspected or
    selected. The visual-reference tools own that separate verification step.
    """
    found: dict[str, dict[str, Any]] = {}
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "web_search_call":
            continue
        for result in item.get("results") or []:
            if not isinstance(result, dict) or result.get("type") != "image_result":
                continue
            url = str(result.get("image_url") or "").strip()
            if url.startswith(("https://", "http://")) and url not in found:
                found[url] = {**result, "image_url": url, "callId": str(item.get("id") or "")}
    return list(found.values())


def _web_details(item: dict[str, Any]) -> dict[str, Any]:
    action = item.get("action") or {}
    details: dict[str, Any] = {"action": str(action.get("type") or "search")}
    for key in ("query", "queries", "url", "pattern"):
        if action.get(key) is not None:
            details[key] = action[key]
    details["arguments"] = {"provider": "OpenAI", **details}
    details["sources"] = _sources({"output": [item]})
    details["images"] = _web_images({"output": [item]})
    return details


def _tool_result_summary(result: Any, secrets: tuple[str, ...]) -> dict[str, Any]:
    """Expose counts and a short decision, without transporting image data twice."""
    payload = result if isinstance(result, dict) else None
    items = result if isinstance(result, (list, tuple)) else [result]
    if payload is None:
        for item in items[:4]:
            text = item if isinstance(item, str) else item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if not isinstance(text, str) or len(text) > 262144:
                continue
            try:
                value = json.loads(text)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                payload = value
                break
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {}
    selected = payload.get("selectedIds", payload.get("selected_candidate_ids"))
    candidates = payload.get("candidates")
    failures = payload.get("failures", payload.get("errors"))
    if isinstance(candidates, list):
        summary["candidateCount"] = len(candidates)
    if isinstance(selected, list):
        summary["selectedIds"] = [safe_text(identifier, secrets)[:128] for identifier in selected[:16]]
    if isinstance(failures, list):
        summary["failureCount"] = len(failures)
    if isinstance(payload.get("rationale"), str):
        summary["rationale"] = safe_text(payload["rationale"], secrets)[:400]
    problem = payload.get("error")
    if payload.get("ok") is False or problem:
        summary["ok"] = False
        message = problem.get("message") if isinstance(problem, dict) else problem
        summary["error"] = safe_text(message or "工具未返回可用结果", secrets)[:400]
        if isinstance(problem, dict) and problem.get("code"):
            summary["code"] = safe_text(problem["code"], secrets)[:120]
    count = len(selected) if isinstance(selected, list) else len(candidates) if isinstance(candidates, list) else payload.get("resultCount")
    return {**({"resultCount": count} if isinstance(count, int) and not isinstance(count, bool) else {}),
            **({"result": summary} if summary else {})}


async def run_agent_stream(request: AgentRequest, *, cancel_event: Any = None, tools: list[Any] | None = None) -> AsyncIterator[dict[str, Any]]:
    """Run the official SDK tool loop, with bounded retries per failed model turn.

    A failed streaming attempt can already have displayed partial text. Its raw
    events are not committed to Runner until the turn completes, so retries do
    not duplicate SDK output items or re-execute previously completed tools.
    Progress still reaches the UI immediately through this event queue.
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    maximum = max_attempts("agent", request.max_attempts)
    state: dict[str, Any] = {"turn": 0, "attempt": 0, "attempts": 0, "responses": [], "web": False,
                             "runner": None, "web_calls": {}, "local_calls": {}}

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    def statistics() -> dict[str, int]:
        web, local = len(state["web_calls"]), len(state["local_calls"])
        return {"turnCount": state["turn"], "requestCount": state["attempts"],
                "toolCallCount": web + local, "webSearchCallCount": web, "localToolCallCount": local}

    async def web_started(call_id: str, turn: int, attempt: int, item: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not call_id:
            return None  # Wait for a real provider ID instead of inventing a second tool call.
        key = (turn, attempt, call_id)
        record = state["web_calls"].get(key)
        if record is None:
            record = {"callId": call_id, "turn": turn, "attempt": attempt, "status": "running", "details": {}}
            state["web_calls"][key] = record
            state["web"] = True
            if item is not None:
                record["details"] = _web_details(item)
            await emit({"type": "web_search_started", "callId": call_id, "turn": turn, "attempt": attempt,
                        **record["details"]})
        elif item is not None:
            for name, value in _web_details(item).items():
                if value or name not in record["details"]:
                    record["details"][name] = value
        return record

    async def web_finished(record: dict[str, Any], *, failed: bool = False, cause: str = "", code: str = "", retrying: bool = False) -> None:
        if record["status"] != "running":
            return
        record["status"] = "failed" if failed else "completed"
        await emit({"type": "web_search_failed" if failed else "web_search_completed",
                    "callId": record["callId"], "turn": record["turn"], "attempt": record["attempt"],
                    **record["details"], **({"cause": cause, "code": code, "retrying": retrying} if failed else {})})

    async def finish_response_web(response: dict[str, Any], turn: int, attempt: int) -> None:
        for item in response.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "web_search_call":
                continue
            record = await web_started(str(item.get("id") or ""), turn, attempt, item)
            if record is not None:
                failed = item.get("status") != "completed"
                await web_finished(record, failed=failed,
                                   cause="上游搜索未完整结束" if failed else "",
                                   code="web_search_incomplete" if failed else "")
        for (call_turn, call_attempt, _), record in state["web_calls"].items():
            if call_turn == turn and call_attempt == attempt and record["status"] == "running":
                completed = record.get("providerCompleted", False)
                await web_finished(record, failed=not completed,
                                   cause="上游响应未包含该搜索的完整结果" if not completed else "",
                                   code="web_search_incomplete" if not completed else "")

    async def fail_running_tools(details: dict[str, Any], *, turn: int | None = None, attempt: int | None = None, retrying: bool = False) -> None:
        for record in state["web_calls"].values():
            if (turn is None or record["turn"] == turn) and (attempt is None or record["attempt"] == attempt):
                await web_finished(record, failed=True, cause=details["cause"], code=details["code"], retrying=retrying)
        if turn is None:  # Model-turn retries must never replay or fail completed local function tools.
            for record in state["local_calls"].values():
                if record["status"] == "running":
                    record["status"] = "failed"
                    await emit({"type": "tool_failed", **{key: value for key, value in record.items() if key != "status"},
                                "cause": details["cause"], "code": details["code"]})

    async def execute() -> None:
        client = None
        try:
            from agents import Agent, ModelSettings, OpenAIResponsesModel, RunConfig, Runner, WebSearchTool
            from agents.lifecycle import RunHooks
            from openai import AsyncOpenAI
            from openai.types.shared import Reasoning
            import httpx2

            check_cancelled(cancel_event)
            if not request.base_url or not request.api_key or not request.model:
                raise ValueError("Agent 缺少接口地址、密钥或模型配置")
            host = (urlsplit(request.base_url).hostname or "").lower()
            managed = request.api_key.startswith("sk-mirra-") or host in {"39.105.74.222", "imggen.djyx.me", "imggen.djyx.pw", "127.0.0.1", "localhost", "::1"}
            http_client = request.http_client or httpx2.AsyncClient(trust_env=not managed if request.trust_env is None else request.trust_env, verify=request.ssl_context or ssl.create_default_context(), follow_redirects=False, timeout=request.timeout, **({"proxy": request.proxy_url} if request.proxy_url else {}))
            headers = {**(request.headers or {})}
            if request.mode:
                headers["X-Mirra-Reasoning-Mode"] = request.mode
            client = AsyncOpenAI(api_key=request.api_key, base_url=request.base_url.rstrip("/") + "/", max_retries=0, timeout=request.timeout, default_headers=headers, http_client=http_client)

            class RetryingResponsesModel(OpenAIResponsesModel):
                async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
                    state["turn"] += 1
                    turn = state["turn"]
                    for attempt in range(1, maximum + 1):
                        check_cancelled(cancel_event)
                        state["attempt"], state["attempts"] = attempt, state["attempts"] + 1
                        await emit({"type": "turn_started", "turn": turn, "attempt": attempt, "maxAttempts": maximum, "reset": attempt > 1})
                        buffered, response = [], None
                        stream = super().stream_response(*args, **kwargs)
                        try:
                            async for event in stream:
                                check_cancelled(cancel_event)
                                buffered.append(event)
                                kind = getattr(event, "type", "")
                                if kind == "response.output_text.delta":
                                    await emit({"type": "text_delta", "delta": event.delta, "turn": turn, "attempt": attempt})
                                elif kind in {"response.web_search_call.in_progress", "response.web_search_call.searching"}:
                                    await web_started(str(getattr(event, "item_id", "")), turn, attempt)
                                elif kind == "response.web_search_call.completed":
                                    record = await web_started(str(getattr(event, "item_id", "")), turn, attempt)
                                    if record is not None:
                                        record["providerCompleted"] = True
                                elif kind in {"response.output_item.added", "response.output_item.done"}:
                                    item = _dump(getattr(event, "item", None))
                                    if isinstance(item, dict) and item.get("type") == "web_search_call":
                                        record = await web_started(str(item.get("id") or ""), turn, attempt, item)
                                        if record is not None and kind == "response.output_item.done":
                                            failed = item.get("status") != "completed"
                                            await web_finished(record, failed=failed,
                                                               cause="上游搜索未完整结束" if failed else "",
                                                               code="web_search_incomplete" if failed else "")
                                elif kind == "response.completed":
                                    response = _dump(event.response)
                                elif kind in {"response.failed", "response.incomplete"}:
                                    failed = _dump(getattr(event, "response", None)) or {}
                                    problem = failed.get("error") or failed.get("incomplete_details") or {}
                                    code = str(problem.get("code") or problem.get("reason") or "response_failed")
                                    cause = str(problem.get("message") or ("模型输出达到令牌上限，未完成结果" if code == "max_output_tokens" else f"上游返回 {kind}：{code}"))
                                    raise ProviderResponseError(code, cause, status=problem.get("status"), request_id=failed.get("_request_id") or failed.get("id"))
                            if response is None:
                                raise ConnectionError("Agent 响应中断，未收到完整完成事件")
                            await finish_response_web(response, turn, attempt)
                            if request.validate_output is not None:
                                request.validate_output(response)
                            if request.response_observer is not None:
                                request.response_observer(response)
                            state["responses"].append(response)
                            await emit({"type": "turn_completed", "turn": turn, "attempt": attempt,
                                        "usage": response.get("usage") or {}, "response": response, **statistics()})
                            for event in buffered:
                                yield event
                            return
                        except (AgentCancelled, asyncio.CancelledError):
                            details = failure_details(AgentCancelled("任务已取消"), "agent", attempt, maximum, (request.api_key,))
                            await fail_running_tools(details, turn=turn, attempt=attempt)
                            raise
                        except Exception as error:
                            retryable, _, _, _, _ = classify_error(error)
                            delay = retry_delay(error, attempt)
                            details = failure_details(error, "agent", attempt, maximum, (request.api_key, request.proxy_url or ""))
                            retrying = retryable and attempt < maximum and delay <= 120
                            await fail_running_tools(details, turn=turn, attempt=attempt, retrying=retrying)
                            if not retryable or attempt == maximum or delay > 120:
                                raise AgentRunFailure(details) from error
                            await emit({"type": "retry", **details, "turn": turn, "attempt": attempt + 1, "delayMs": round(delay * 1000)})
                            await wait_async(delay, cancel_event)
                        finally:
                            with contextlib.suppress(Exception):
                                await stream.aclose()

            class Hooks(RunHooks):
                async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
                    call_id = str(getattr(context, "tool_call_id", "") or getattr(getattr(context, "tool_call", None), "call_id", ""))
                    if not call_id:
                        call_id = f"local-tool-{state['turn']}-{len(state['local_calls']) + 1}"
                    record = {"turn": state["turn"], "tool": tool.name, "callId": call_id,
                              "arguments": getattr(context, "tool_arguments", ""), "status": "running"}
                    state["local_calls"][call_id] = record
                    await emit({"type": "tool_started", **{key: value for key, value in record.items() if key != "status"}})

                async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: Any) -> None:
                    call_id = str(getattr(context, "tool_call_id", "") or getattr(getattr(context, "tool_call", None), "call_id", ""))
                    record = state["local_calls"].get(call_id)
                    if record is None and not call_id:
                        record = next((item for item in state["local_calls"].values() if item["tool"] == tool.name and item["status"] == "running"), None)
                    if record is not None:
                        record["status"] = "completed"
                        await emit({"type": "tool_completed", **{key: value for key, value in record.items() if key != "status"},
                                    **_tool_result_summary(result, (request.api_key, request.proxy_url or ""))})

            model_tools = list(tools or [])
            image_search = request.web_search_enabled and request.web_image_search_enabled is not False
            if request.web_search_enabled:
                model_tools.append(WebSearchTool(search_content_types=["image", "text"],
                    image_settings={"max_results": max(1, min(12, int(request.web_image_max_results))), "caption": True}) if image_search else WebSearchTool())
            includes = ["web_search_call.action.sources"] if request.web_search_enabled else None
            if image_search:
                includes.append("web_search_call.results")
            settings = ModelSettings(store=False, max_tokens=request.max_output_tokens, preserve_raw_usage=True, parallel_tool_calls=True, reasoning=Reasoning(effort=request.effort) if request.effort else None, tool_choice=request.tool_choice, response_include=includes, extra_body={"max_tool_calls": 3} if request.web_search_enabled else None)
            agent = Agent(name="Mirra visual planner", instructions=request.instructions, model=RetryingResponsesModel(request.model, client), model_settings=settings, tools=model_tools)
            turn_budget = max(1, min(32, request.max_turns))
            next_input = request.input
            while True:
                check_cancelled(cancel_event)
                remaining = turn_budget - state["turn"]
                if remaining <= 0:
                    raise ProviderResponseError("max_turns", f"Agent 已达到 {turn_budget} 轮处理上限，工作流尚未完成")
                result = Runner.run_streamed(agent, next_input, max_turns=remaining, hooks=Hooks(), run_config=RunConfig(tracing_disabled=True))
                state["runner"] = result
                async for _ in result.stream_events():
                    check_cancelled(cancel_event)
                check_cancelled(cancel_event)
                text = str(result.final_output or "").strip()
                if not text:
                    raise RuntimeError("Agent 没有返回完整结果")
                feedback = request.final_output_feedback(text) if request.final_output_feedback is not None else None
                if not isinstance(feedback, str) or not feedback.strip():
                    break
                if state["turn"] >= turn_budget:
                    raise ProviderResponseError("max_turns", f"Agent 已达到 {turn_budget} 轮处理上限，工作流尚未完成：{feedback.strip()}")
                # Continue from the complete SDK transcript. Successful hosted
                # and local tool calls remain in context; this is workflow
                # feedback, not a retry from the original user prompt.
                next_input = [*result.to_input_list(), {"role": "user", "content": feedback.strip()}]
                agent.model_settings.tool_choice = None
                await emit({"type": "workflow_feedback", "feedback": feedback.strip(), "maxTurns": turn_budget, **statistics()})
            raw_usage = [response.get("usage") or {} for response in state["responses"]]
            usage: dict[str, Any] = {}
            for part in raw_usage:
                for key, value in part.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[key] = usage.get(key, 0) + value
                    elif isinstance(value, dict):
                        usage.setdefault(key, {})
                        for detail_key, detail_value in value.items():
                            if isinstance(detail_value, (int, float)):
                                usage[key][detail_key] = usage[key].get(detail_key, 0) + detail_value
            usage["requests"] = len(raw_usage)
            combined = {"output": [item for response in state["responses"] for item in response.get("output") or []]}
            await emit({"type": "result", "text": text, "usage": usage, "webSearchUsed": state["web"], "attempts": state["attempts"], "responseId": state["responses"][-1].get("id") if state["responses"] else None, "responses": state["responses"], "sources": _sources(combined), "webImages": _web_images(combined), **statistics()})
        except (AgentCancelled, asyncio.CancelledError) as error:
            details = failure_details(AgentCancelled("任务已取消"), "agent", max(1, state["attempt"]), maximum, (request.api_key,))
            await fail_running_tools(details)
            await emit({"type": "error", "error": details, **statistics()})
        except Exception as error:
            if type(error).__name__ == "MaxTurnsExceeded":
                error = ProviderResponseError("max_turns", f"Agent 已达到处理轮数上限：{error}")
            details = failure_details(error, "agent", max(1, state["attempt"]), maximum, (request.api_key, request.proxy_url or ""))
            await fail_running_tools(details)
            await emit({"type": "error", "error": details, **statistics()})
        finally:
            try:
                if client is not None:
                    await client.close()
            except (Exception, asyncio.CancelledError):
                # Cleanup cannot replace the result/error already delivered,
                # or prevent the consumer from seeing end-of-stream.
                pass
            finally:
                queue.put_nowait(None)

    task = asyncio.create_task(execute())
    cancelling = False
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set() and not cancelling:
                cancelling = True
                if state["runner"] is not None:
                    state["runner"].cancel()
                task.cancel()
            try:
                event = await asyncio.wait_for(queue.get(), timeout=.05)
            except asyncio.TimeoutError:
                continue
            if event is None:
                break
            yield event
    finally:
        if not task.done():
            if state["runner"] is not None:
                state["runner"].cancel()
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def run_agent(request: AgentRequest, *, cancel_event: Any = None, tools: list[Any] | None = None, on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    async def consume() -> dict[str, Any]:
        final: dict[str, Any] | None = None
        async for event in run_agent_stream(request, cancel_event=cancel_event, tools=tools):
            if on_event is not None:
                on_event(event)
            if event["type"] == "result":
                final = event
            elif event["type"] == "error":
                if event["error"]["code"] == "cancelled":
                    raise AgentCancelled(event["error"]["message"])
                raise AgentRunFailure(event["error"])
        if final is None:
            raise RuntimeError("Agent SDK 未返回结果")
        return final
    return asyncio.run(consume())
