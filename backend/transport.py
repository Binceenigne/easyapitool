from __future__ import annotations

import errno
import urllib.parse
import urllib.request
from typing import Any, Callable


class UnsentConnectionError(RuntimeError):
    """A connection was refused before a non-redirected request could be sent."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def is_mirra_request(request: urllib.request.Request) -> bool:
    authorization = next(
        (value for name, value in request.header_items() if name.lower() == "authorization"), ""
    )
    if authorization.startswith("Bearer sk-mirra-"):
        return True
    return (urllib.parse.urlsplit(request.full_url).hostname or "").lower() in {
        "39.105.74.222", "imggen.djyx.me", "imggen.djyx.pw"
    }


def connection_was_refused(error: BaseException) -> bool:
    pending: list[Any] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ConnectionRefusedError):
            return True
        if getattr(current, "winerror", None) == 10061 or getattr(current, "errno", None) == errno.ECONNREFUSED:
            return True
        pending.extend([getattr(current, "reason", None), getattr(current, "__cause__", None)])
    return False


def open_request(
    request: urllib.request.Request,
    *,
    timeout: float,
    opener: Callable[..., Any] | None = None,
) -> Any:
    # The gateway performs its own configured egress. A stopped local desktop
    # proxy must not prevent the client from reaching this gateway.
    if is_mirra_request(request):
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler())
        return direct.open(request, timeout=timeout)
    return (opener or urllib.request.urlopen)(request, timeout=timeout)
