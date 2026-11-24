from __future__ import annotations

import errno
import copy
import functools
import http.client
import socket
import threading
import urllib.parse
import urllib.request
from typing import Any, Callable


class UnsentConnectionError(RuntimeError):
    """A connection was refused before a non-redirected request could be sent."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def abort_response(response: Any) -> threading.Thread:
    """Interrupt a live HTTP/TLS read without waiting for BufferedReader.close.

    Closing an HTTPResponse from another thread can wait on the reader's lock.
    Native socket shutdown first wakes recv/SSL_read, without a TLS unwrap or
    another HTTP request. The normal owner and a daemon cleanup thread can then
    finish closing; the UI cancellation call never waits for an upstream byte.
    """
    pending = [response]
    seen: set[int] = set()
    for _ in range(24):
        if not pending:
            break
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, socket.socket):
            try:
                # Use the native method, not SSLSocket.unwrap/close_notify,
                # which can themselves wait for the blocked peer.
                socket.socket.shutdown(current, socket.SHUT_RDWR)
            except OSError:
                pass
            continue
        for name in ("fp", "_fp", "raw", "_sock", "sock"):
            try:
                child = object.__getattribute__(current, name)
            except (AttributeError, OSError, ValueError):
                continue
            if child is not None:
                pending.append(child)

    def close() -> None:
        try:
            response.close()
        except Exception:
            pass

    closer = threading.Thread(target=close, name="cancel-response-close", daemon=True)
    closer.start()
    return closer


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


def transport_details(request: urllib.request.Request) -> dict[str, Any]:
    """Report the selected route, not the process-wide proxy environment."""
    if is_mirra_request(request):
        return {"transport": "direct", "proxyStrategy": "direct", "effectiveProxy": ""}
    return {"transport": "configured", "proxyStrategy": "configured"}


class _TaskConnection:
    def __init__(self, *args: Any, task_context: Any, **kwargs: Any) -> None:
        object.__setattr__(self, "_task_context", task_context)
        super().__init__(*args, **kwargs)

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name == "sock" and isinstance(value, socket.socket):
            # HTTPConnection assigns the TCP socket before CONNECT/getresponse.
            # Register at that moment, before a server can hold up its headers.
            self._task_context.add_response(self)

    def connect(self) -> None:
        self._task_context.check_cancelled()
        super().connect()
        self._task_context.check_cancelled()

    def getresponse(self) -> Any:
        try:
            response = super().getresponse()
            self._task_context.add_response(response)
            return response
        finally:
            self._task_context.remove_response(self)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._task_context.remove_response(self)


class _TaskHTTPConnection(_TaskConnection, http.client.HTTPConnection):
    pass


class _TaskHTTPSConnection(_TaskConnection, http.client.HTTPSConnection):
    def connect(self) -> None:
        self._task_context.check_cancelled()
        http.client.HTTPConnection.connect(self)
        self._task_context.check_cancelled()
        hostname = self._tunnel_host or self.host
        # Assign the SSL socket before the handshake starts so cancellation
        # also interrupts a peer that accepts TCP but never completes TLS.
        self.sock = self._context.wrap_socket(self.sock, server_hostname=hostname,
                                             do_handshake_on_connect=False)
        self._task_context.check_cancelled()
        self.sock.do_handshake()
        self._task_context.check_cancelled()


class _TaskHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, task_context: Any) -> None:
        super().__init__()
        self._task_context = task_context

    def http_open(self, request: urllib.request.Request) -> Any:
        return self.do_open(functools.partial(_TaskHTTPConnection, task_context=self._task_context), request)


class _TaskHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, task_context: Any, context: Any = None) -> None:
        super().__init__(context=context)
        self._task_context = task_context

    def https_open(self, request: urllib.request.Request) -> Any:
        return self.do_open(functools.partial(_TaskHTTPSConnection, task_context=self._task_context),
                            request, context=self._context)


def _task_opener(task_context: Any, owner: Any = None, *, direct: bool = False) -> Any:
    if direct:
        handlers = [urllib.request.ProxyHandler({}), NoRedirectHandler()]
        context = None
    else:
        original = owner or getattr(urllib.request, "_opener", None) or urllib.request.build_opener()
        context = next((handler._context for handler in original.handlers
                        if isinstance(handler, urllib.request.HTTPSHandler)), None)
        handlers = [copy.copy(handler) for handler in original.handlers
                    if not isinstance(handler, (urllib.request.HTTPHandler, urllib.request.HTTPSHandler))]
    return urllib.request.build_opener(*handlers, _TaskHTTPHandler(task_context),
                                       _TaskHTTPSHandler(task_context, context))


def open_request(
    request: urllib.request.Request,
    *,
    timeout: float,
    opener: Callable[..., Any] | None = None,
    task_context: Any = None,
) -> Any:
    # The gateway performs its own configured egress. A stopped local desktop
    # proxy must not prevent the client from reaching this gateway.
    managed = is_mirra_request(request)
    if task_context is not None:
        task_context.check_cancelled()
        owner = getattr(opener, "__self__", None)
        if managed or opener is None or isinstance(owner, urllib.request.OpenerDirector):
            try:
                return _task_opener(task_context, owner, direct=managed).open(request, timeout=timeout)
            except BaseException:
                task_context.check_cancelled()
                raise
        # Preserve externally supplied opaque callables. Native application
        # openers take the tracked connection path above.
        response = opener(request, timeout=timeout)
        task_context.add_response(response)
        return response
    if managed:
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler())
        return direct.open(request, timeout=timeout)
    return (opener or urllib.request.urlopen)(request, timeout=timeout)
