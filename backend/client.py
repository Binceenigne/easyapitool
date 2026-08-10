from __future__ import annotations

from .common import *
class EasyClinClient:
    @staticmethod
    def get_json(base_url: str, secret: str, path: str, timeout: int = 25) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
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
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/event-stream" not in content_type:
                    payload = json.loads(response.read().decode("utf-8"))
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

                def flush_event() -> None:
                    nonlocal event_type, data_lines
                    if not data_lines:
                        event_type = ""
                        return
                    raw_data = "\n".join(data_lines)
                    current_event = event_type
                    event_type = ""
                    data_lines = []
                    if raw_data == "[DONE]":
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
                text = "".join(chunks)
                if not text and not allow_empty_text:
                    raise RuntimeError("Responses 接口未返回文本")
                return text
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(response_body)
                error = payload.get("error") or {}
                message = error.get("message") if isinstance(error, dict) else str(error)
            except json.JSONDecodeError:
                message = response_body[:300]
            raise RuntimeError(f"HTTP {exc.code}: {message or exc.reason}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = exc.reason if hasattr(exc, "reason") else exc
            raise RuntimeError(f"Responses 网络请求失败: {reason}") from None
