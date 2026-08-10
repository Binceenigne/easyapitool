from __future__ import annotations

from .common import *
from .platform import RPC_METHODS

class ControllerRpcServer:
    def __init__(self, controller: "AppController", address: str, authkey: bytes) -> None:
        self.controller = controller
        self.address = address
        self.authkey = authkey
        self.listener: Listener | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.listener = Listener(self.address, family="AF_PIPE", authkey=self.authkey)
        self.thread = threading.Thread(target=self._serve, name="controller-rpc", daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.controller.stopping.is_set():
            try:
                connection = self.listener.accept() if self.listener else None
            except (OSError, EOFError):
                break
            if connection is None:
                continue
            threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name="controller-rpc-request",
                daemon=True,
            ).start()

    def _handle_connection(self, connection: Any) -> None:
        try:
            request = connection.recv()
            method_name = str(request.get("method") or "") if isinstance(request, dict) else ""
            arguments = request.get("args", []) if isinstance(request, dict) else []
            if method_name not in RPC_METHODS:
                raise ValueError("不允许的后台调用")
            method = getattr(self.controller, method_name)
            if method_name in {"generate_image", "polish_prompt"}:
                send_lock = threading.Lock()

                def send_event(event: dict[str, Any]) -> None:
                    try:
                        with send_lock:
                            connection.send({"ok": True, "event": event})
                    except (OSError, EOFError, BrokenPipeError):
                        pass

                result = method(*arguments, event_callback=send_event)
                with send_lock:
                    connection.send({"ok": True, "result": result})
            else:
                connection.send({"ok": True, "result": method(*arguments)})
        except Exception as exc:
            try:
                connection.send({"ok": False, "error": str(exc)})
            except (OSError, EOFError):
                pass
        finally:
            connection.close()

    def stop(self) -> None:
        listener = self.listener
        self.listener = None
        if listener:
            listener.close()


class ControllerRpcClient:
    def __init__(self, address: str, authkey: bytes) -> None:
        self.address = address
        self.authkey = authkey

    def call(self, method: str, *arguments: Any) -> Any:
        connection = Client(self.address, family="AF_PIPE", authkey=self.authkey)
        try:
            connection.send({"method": method, "args": list(arguments)})
            response = connection.recv()
        finally:
            connection.close()
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "后台服务调用失败")
        return response.get("result")

    def call_with_events(
        self,
        method: str,
        *arguments: Any,
        on_event: Any = None,
    ) -> Any:
        connection = Client(self.address, family="AF_PIPE", authkey=self.authkey)
        try:
            connection.send({"method": method, "args": list(arguments)})
            while True:
                response = connection.recv()
                if "event" in response:
                    if on_event is not None:
                        on_event(response["event"])
                    continue
                break
        finally:
            connection.close()
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "后台服务调用失败")
        return response.get("result")
