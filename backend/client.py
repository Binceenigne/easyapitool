from __future__ import annotations

from .common import *
from .network_log import log_network_error, new_network_request_id
from .transport import open_request, is_mirra_request, connection_was_refused, UnsentConnectionError


class EasyClinClient:
    @staticmethod
    def get_json(base_url: str, secret: str, path: str, timeout: int = 25) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        request.add_unredirected_header("Authorization", f"Bearer {secret}")
        try:
            with open_request(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(body)
                message = (parsed.get("error") or {}).get("message") or parsed.get("message")
            except json.JSONDecodeError:
                message = body[:200]
            raise RuntimeError(f"HTTP {exc.code}: {message or exc.reason}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}") from None

    def fetch(self, base_url: str, secret: str) -> tuple[dict[str, Any], list[str] | None]:
        usage = self.get_json(base_url, secret, "/usage?days=30")
        try:
            models = self.get_json(base_url, secret, "/models")
        except RuntimeError:
            return usage, None
        model_ids: list[str] = []
        seen: set[str] = set()
        for item in models.get("data") or []:
            model_id = item.get("id") if isinstance(item, dict) else item
            model_id = str(model_id or "").strip()
            if model_id and model_id not in seen:
                seen.add(model_id)
                model_ids.append(model_id)
        return usage, model_ids

    @staticmethod
    def _response_output_text(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        direct = payload.get("output_text")
        if isinstance(direct, str):
            return direct
        response = payload.get("response")
        if isinstance(response, dict):
            nested = EasyClinClient._response_output_text(response)
            if nested:
                return nested
        parts: list[str] = []
        for output in payload.get("output") or []:
            if not isinstance(output, dict):
                continue
            for content in output.get("content") or []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if content.get("type") in {"output_text", "text"} and isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    @staticmethod
    def stream_response(
        base_url: str,
        secret: str,
        model: str,
        instructions: str,
        input_text: Any,
        on_delta: Any = None,
        timeout: int = 240,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        include: list[str] | None = None,
        max_tool_calls: int | None = None,
        parallel_tool_calls: bool | None = None,
        on_completed: Any = None,
        allow_empty_text: bool = False,
        reasoning_mode: str | None = None,
    ) -> str:
        url = f"{base_url.rstrip('/')}/responses"
        payload = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "stream": True,
        }
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if include:
            payload["include"] = include
        if max_tool_calls is not None:
            payload["max_tool_calls"] = max(1, int(max_tool_calls))
        if parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = bool(parallel_tool_calls)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_id = new_network_request_id()
        request_started_at = time.perf_counter()
        request_details = {
            "model": model,
            "reasoningEffort": reasoning_effort or "",
            "stream": True,
            "inputType": type(input_text).__name__,
            "inputItemCount": len(input_text) if isinstance(input_text, list) else 1,
            "toolNames": [
                str(tool.get("name") or tool.get("type") or "")
                for tool in tools or []
                if isinstance(tool, dict)
            ],
            "requestBytes": len(body),
            "timeoutSeconds": timeout,
        }
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            },
            method="POST",
        )
        request.add_unredirected_header("Authorization", f"Bearer {secret}")
        if reasoning_mode:
            request.add_header("X-Mirra-Reasoning-Mode", str(reasoning_mode))
        for attempt in range(2):
            try:
                response_context = open_request(request, timeout=timeout)
                break
            except urllib.error.HTTPError as exc:
                response_body = exc.read().decode("utf-8", "replace")
                log_network_error(
                    "responses_http_error",
                    request_id=request_id,
                    method="POST",
                    url=url,
                    error=exc,
                    status=exc.code,
                    elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                    attempt=attempt + 1,
                    response_headers=exc.headers,
                    response_body=response_body,
                    details=request_details,
                    secrets=(secret,),
                )
                clean_body = response_body.strip()
                generic_forbidden = exc.code == 403 and (
                    not clean_body
                    or clean_body.lower() == "forbidden"
                    or clean_body.lower().startswith("<html")
                    or clean_body.lower().startswith("<!doctype html")
                )
                if generic_forbidden and attempt == 0:
                    continue
                if generic_forbidden:
                    raise RuntimeError(
                        f"HTTP 403: 思维服务暂时拒绝请求，请稍后重试（诊断 ID: {request_id}）"
                    ) from None
                try:
                    error_payload = json.loads(response_body)
                    error = error_payload.get("error") or {}
                    message = error.get("message") if isinstance(error, dict) else str(error)
                except json.JSONDecodeError:
                    message = response_body[:300]
                raise RuntimeError(
                    f"HTTP {exc.code}: {message or exc.reason}（诊断 ID: {request_id}）"
                ) from None
            except (urllib.error.URLError, TimeoutError) as exc:
                log_network_error(
                    "responses_connect_error",
                    request_id=request_id,
                    method="POST",
                    url=url,
                    error=exc,
                    elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                    attempt=attempt + 1,
                    details=request_details,
                    secrets=(secret,),
                )
                reason = exc.reason if hasattr(exc, "reason") else exc
                error_type = UnsentConnectionError if is_mirra_request(request) and connection_was_refused(exc) else RuntimeError
                raise error_type(
                    f"Responses 网络请求失败: {reason}（诊断 ID: {request_id}）"
                ) from None
        response_headers = getattr(response_context, "headers", None)
        response_status = getattr(response_context, "status", None)
        try:
            with response_context as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/event-stream" not in content_type:
                    payload = json.loads(response.read().decode("utf-8"))
                    if str(payload.get("status") or "") in {"failed", "incomplete", "cancelled"}:
                        error = payload.get("error") or {}
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        raise RuntimeError(message or f"Responses 响应未完成（诊断 ID: {request_id}）")
                    if on_completed is not None:
                        on_completed(payload)
                    text = EasyClinClient._response_output_text(payload)
                    if not text and not allow_empty_text:
                        raise RuntimeError("Responses 接口未返回文本")
                    if on_delta is not None:
                        on_delta(text)
                    return text

                chunks: list[str] = []
                event_type = ""
                data_lines: list[str] = []
                completed_stream = False

                def flush_event() -> None:
                    nonlocal event_type, data_lines, completed_stream
                    if not data_lines:
                        event_type = ""
                        return
                    raw_data = "\n".join(data_lines)
                    current_event = event_type
                    event_type = ""
                    data_lines = []
                    if raw_data == "[DONE]":
                        completed_stream = True
                        return
                    try:
                        payload = json.loads(raw_data)
                    except json.JSONDecodeError:
                        return
                    payload_type = str(payload.get("type") or current_event or "")
                    if payload_type == "response.output_text.delta":
                        delta = payload.get("delta")
                        if isinstance(delta, str) and delta:
                            chunks.append(delta)
                            if on_delta is not None:
                                on_delta(delta)
                        return
                    if payload_type in {"error", "response.failed", "response.incomplete"}:
                        error = payload.get("error") or payload.get("response", {}).get("error") or {}
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        raise RuntimeError(message or "Responses 接口执行失败")
                    if payload_type == "response.completed":
                        completed_stream = True
                    if payload_type == "response.completed" and not chunks:
                        completed_text = EasyClinClient._response_output_text(payload)
                        if completed_text:
                            chunks.append(completed_text)
                            if on_delta is not None:
                                on_delta(completed_text)
                    if payload_type == "response.completed" and on_completed is not None:
                        completed = payload.get("response")
                        on_completed(completed if isinstance(completed, dict) else payload)

                for raw_line in response:
                    line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                    if not line:
                        flush_event()
                    elif line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                flush_event()
                if not completed_stream:
                    raise RuntimeError(f"Responses 响应中断，未收到完整完成事件（诊断 ID: {request_id}）")
                text = "".join(chunks)
                if not text and not allow_empty_text:
                    raise RuntimeError("Responses 接口未返回文本")
                return text
        except Exception as exc:
            log_network_error(
                "responses_stream_error",
                request_id=request_id,
                method="POST",
                url=url,
                error=exc,
                status=response_status,
                elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                attempt=attempt + 1,
                response_headers=response_headers,
                details=request_details,
                secrets=(secret,),
            )
            if not isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
                raise
            reason = exc.reason if hasattr(exc, "reason") else exc
            raise RuntimeError(
                f"Responses 网络请求失败: {reason}（诊断 ID: {request_id}）"
            ) from None
