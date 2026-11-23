from __future__ import annotations

import asyncio
import contextlib
import ssl
import threading
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlsplit

from .retry import AgentCancelled, AgentRunFailure, ProviderResponseError, check_cancelled, classify_error, failure_details, max_attempts, retry_delay, wait_async


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
        for source in action.get("sources") or []:
            if isinstance(source, dict) and str(source.get("url") or "").startswith(("https://", "http://")):
                found[source["url"]] = {"url": source["url"], "title": str(source.get("title") or "")}
        for content in item.get("content") or []:
            for source in content.get("annotations") or []:
                if source.get("type") == "url_citation" and str(source.get("url") or "").startswith(("https://", "http://")):
                    found[source["url"]] = {"url": source["url"], "title": str(source.get("title") or "")}
    return list(found.values())


async def run_agent_stream(request: AgentRequest, *, cancel_event: Any = None, tools: list[Any] | None = None) -> AsyncIterator[dict[str, Any]]:
    """Run the official SDK tool loop, with bounded retries per failed model turn.

    A failed streaming attempt can already have displayed partial text. Its raw
    events are not committed to Runner until the turn completes, so retries do
    not duplicate SDK output items or re-execute previously completed tools.
    Progress still reaches the UI immediately through this event queue.
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    maximum = max_attempts("agent", request.max_attempts)
    state: dict[str, Any] = {"turn": 0, "attempt": 0, "attempts": 0, "responses": [], "web": False, "runner": None}

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

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
                                    state["web"] = True
                                    await emit({"type": "web_search_started", "callId": getattr(event, "item_id", ""), "turn": turn})
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
                            if request.validate_output is not None:
                                request.validate_output(response)
                            state["responses"].append(response)
                            searched = any(item.get("type") == "web_search_call" for item in response.get("output") or [])
                            state["web"] = state["web"] or searched
                            if searched:
                                    await emit({"type": "web_search_completed", "turn": turn, "sources": _sources(response)})
                            await emit({"type": "turn_completed", "turn": turn, "attempt": attempt, "usage": response.get("usage") or {}, "response": response})
                            for event in buffered:
                                yield event
                            return
                        except (AgentCancelled, asyncio.CancelledError):
                            raise
                        except Exception as error:
                            retryable, _, _, _, _ = classify_error(error)
                            delay = retry_delay(error, attempt)
                            if not retryable or attempt == maximum or delay > 120:
                                raise AgentRunFailure(failure_details(error, "agent", attempt, maximum, (request.api_key, request.proxy_url or ""))) from error
                            details = failure_details(error, "agent", attempt, maximum, (request.api_key, request.proxy_url or ""))
                            await emit({"type": "retry", **details, "turn": turn, "attempt": attempt + 1, "delayMs": round(delay * 1000)})
                            await wait_async(delay, cancel_event)
                        finally:
                            with contextlib.suppress(Exception):
                                await stream.aclose()

            class Hooks(RunHooks):
                async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
                    await emit({"type": "tool_started", "turn": state["turn"], "tool": tool.name, "callId": getattr(context, "tool_call_id", ""), "arguments": getattr(context, "tool_arguments", "")})

                async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: Any) -> None:
                    await emit({"type": "tool_completed", "turn": state["turn"], "tool": tool.name, "callId": getattr(context, "tool_call_id", "")})

            model_tools = list(tools or [])
            if request.web_search_enabled:
                model_tools.append(WebSearchTool())
            settings = ModelSettings(store=False, max_tokens=request.max_output_tokens, preserve_raw_usage=True, parallel_tool_calls=True, reasoning=Reasoning(effort=request.effort) if request.effort else None, tool_choice=request.tool_choice, response_include=["web_search_call.action.sources"] if request.web_search_enabled else None, extra_body={"max_tool_calls": 3} if request.web_search_enabled else None)
            agent = Agent(name="Mirra visual planner", instructions=request.instructions, model=RetryingResponsesModel(request.model, client), model_settings=settings, tools=model_tools)
            result = Runner.run_streamed(agent, request.input, max_turns=max(1, min(32, request.max_turns)), hooks=Hooks(), run_config=RunConfig(tracing_disabled=True))
            state["runner"] = result
            async for _ in result.stream_events():
                check_cancelled(cancel_event)
            check_cancelled(cancel_event)
            text = str(result.final_output or "").strip()
            if not text:
                raise RuntimeError("Agent 没有返回完整结果")
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
            await emit({"type": "result", "text": text, "usage": usage, "webSearchUsed": state["web"], "attempts": state["attempts"], "responseId": state["responses"][-1].get("id") if state["responses"] else None, "responses": state["responses"], "sources": [source for response in state["responses"] for source in _sources(response)]})
        except (AgentCancelled, asyncio.CancelledError) as error:
            await emit({"type": "error", "error": failure_details(AgentCancelled("任务已取消"), "agent", max(1, state["attempt"]), maximum, (request.api_key,))})
        except Exception as error:
            await emit({"type": "error", "error": failure_details(error, "agent", max(1, state["attempt"]), maximum, (request.api_key, request.proxy_url or ""))})
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
