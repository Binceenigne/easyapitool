from __future__ import annotations

import asyncio
import email.utils
import errno
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


class AgentRunFailure(RuntimeError):
    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details
        super().__init__(details["message"])


class AgentCancelled(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int | None = None, request_id: str | None = None, headers: Any = None) -> None:
        self.body = {"error": {"code": code, "message": message}}
        self.status_code, self.request_id = status, request_id
        self.headers = headers or {}
        super().__init__(message)


def safe_text(value: Any, secrets: tuple[str, ...] = ()) -> str:
    text = str(value or "")
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", text)
    text = re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    return text[:1600]


def max_attempts(stage: str, requested: Any = None) -> int:
    value = requested if requested is not None else os.environ.get(f"MIRRA_{stage.upper()}_MAX_ATTEMPTS", "3")
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


def _error_body(error: BaseException) -> dict[str, Any]:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        return nested if isinstance(nested, dict) else body
    return {}


def classify_error(error: BaseException) -> tuple[bool, int | None, str, str | None, float | None]:
    body = _error_body(error)
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if not isinstance(status, int):
        match = re.search(r"(?:HTTP\s*|status(?: code)?[=: ]+)(\d{3})\b", str(error), re.I)
        status = int(match.group(1)) if match else None
    code = str(body.get("code") or getattr(error, "error_code", None) or "")
    cause = str(body.get("message") or error)
    combined = (code + " " + cause).lower()
    headers = getattr(error, "headers", None) or getattr(getattr(error, "response", None), "headers", None) or {}
    request_id = getattr(error, "request_id", None) or headers.get("x-request-id") or headers.get("x-mirra-diagnostic-id")
    match = re.search(r"诊断\s*ID[:： ]+([A-Za-z0-9_-]+)", cause)
    request_id = request_id or (match.group(1) if match else None)
    retry_after = None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value:
        try:
            retry_after = max(0.0, float(value))
        except (ValueError, TypeError):
            try:
                retry_after = max(0.0, (email.utils.parsedate_to_datetime(str(value)) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    forbidden = ("rpm_limit_exceeded", "insufficient_quota", "quota_exceeded", "quota exceeded", "permission", "content_filter", "content policy", "safety", "max_output_tokens", "max turns", "max_turns", "invalid_api_key", "额度", "档位", "权限", "无效参数", "不支持", "取消", "生成已停止", "cancel")
    local_io_error = isinstance(error, OSError) and getattr(error, "errno", None) in {errno.EACCES, errno.EPERM, errno.ENOSPC, errno.ENOENT, errno.EROFS, errno.ENAMETOOLONG}
    if local_io_error or isinstance(error, (AgentCancelled, asyncio.CancelledError)) or code in {"invalid_request_error", "invalid_prompt", "unsupported_parameter", "model_not_found"} or any(word in combined for word in forbidden) or status in {400, 401, 402, 403, 404, 405, 413, 422}:
        return False, status, code or ("cancelled" if isinstance(error, (AgentCancelled, asyncio.CancelledError)) or "cancel" in combined or "取消" in combined or "生成已停止" in combined else "request_rejected"), request_id, retry_after
    transient = status in {408, 409, 425, 429, 500, 502, 503, 504} or code in {"server_error", "rate_limit_exceeded", "timeout", "upstream_error", "incomplete_stream"} or isinstance(error, (TimeoutError, ConnectionError, OSError))
    transient = transient or any(word in combined for word in ("timeout", "timed out", "connection", "connecterror", "readerror", "readtimeout", "stream", "响应中断", "响应不完整", "未收到完整", "网络", "连接", "超时", "invalid image", "无效图片数据", "未返回图片数据"))
    return transient, status, code or ("upstream_error" if status else "transport_error" if transient else "agent_error"), request_id, retry_after


def failure_details(error: BaseException, stage: str, attempts: int, maximum: int, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    if isinstance(error, AgentRunFailure):
        return dict(error.details)
    retryable, status, code, request_id, _ = classify_error(error)
    cause = safe_text(_error_body(error).get("message") or error, secrets)
    identifier = safe_text(request_id or uuid.uuid4().hex[:16], secrets)
    label = "Agent" if stage == "agent" else "图片生成"
    exhausted = retryable and attempts >= maximum
    message = f"{label}{'重试已用尽' if exhausted else '失败'}（尝试 {attempts}/{maximum}）：{cause}（诊断 ID: {identifier}）"
    return {"stage": stage, "code": code, "message": message, "attempts": attempts, "maxAttempts": maximum, "cause": cause, "lastCause": cause, "status": status, "diagnosticId": identifier, "retryable": retryable}


def retry_delay(error: BaseException, attempt: int) -> float:
    _, _, _, _, advised = classify_error(error)
    # Honor provider admission hints; a very long hint ends with an explicit
    # error rather than retrying earlier than the provider requested.
    if advised is not None:
        return advised
    return min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.15)


def check_cancelled(cancel_event: Any = None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise AgentCancelled("任务已取消")


async def wait_async(delay: float, cancel_event: Any = None) -> None:
    stop = time.monotonic() + delay
    while time.monotonic() < stop:
        check_cancelled(cancel_event)
        await asyncio.sleep(min(0.05, max(0, stop - time.monotonic())))
    check_cancelled(cancel_event)


def retry_sync(operation: Callable[[int], Any], *, stage: str, cancel_event: Any = None, attempts: int | None = None, on_retry: Callable[[dict[str, Any]], None] | None = None, secrets: tuple[str, ...] = ()) -> Any:
    maximum = max_attempts(stage, attempts)
    for attempt in range(1, maximum + 1):
        check_cancelled(cancel_event)
        try:
            return operation(attempt)
        except AgentCancelled:
            raise
        except AgentRunFailure:
            raise
        except Exception as error:
            retryable, _, _, _, _ = classify_error(error)
            delay = retry_delay(error, attempt)
            if not retryable or attempt == maximum or delay > 120:
                details = failure_details(error, stage, attempt, maximum, secrets)
                if delay > 120:
                    details["cause"] = details["lastCause"] = safe_text(f"上游要求等待 {delay:g} 秒后重试。{details['cause']}", secrets)
                    details["message"] = f"{details['message']}；Retry-After 超出本任务自动等待上限"
                raise AgentRunFailure(details) from error
            details = failure_details(error, stage, attempt, maximum, secrets)
            if on_retry is not None:
                on_retry({"type": "retry", **details, "attempt": attempt + 1, "delayMs": round(delay * 1000)})
            stop = time.monotonic() + delay
            while time.monotonic() < stop:
                check_cancelled(cancel_event)
                time.sleep(min(0.05, max(0, stop - time.monotonic())))
    raise AssertionError("retry loop did not return")
