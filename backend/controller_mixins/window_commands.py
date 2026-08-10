from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class WindowCommandsMixin:
    def restart_app(self) -> dict[str, Any]:
        self._flush_window_size()
        script = app_data_dir() / "restart-app.ps1"
        ready = app_data_dir() / "restart.ready"
        ready.unlink(missing_ok=True)
        source_script = "" if getattr(sys, "frozen", False) else str(ENTRY_SCRIPT)
        script.write_text(
            "param([int]$ProcessId,[string]$Executable,[string]$SourceScript,[string]$Ready)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "Set-Content -LiteralPath $Ready -Value 'ready' -Encoding ASCII\n"
            "$process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue\n"
            "if ($process) { $process | Wait-Process -ErrorAction SilentlyContinue }\n"
            "$env:PYINSTALLER_RESET_ENVIRONMENT = '1'\n"
            "if ($SourceScript) { Start-Process -FilePath $Executable -ArgumentList @($SourceScript) }\n"
            "else { Start-Process -FilePath $Executable }\n"
            "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force\n",
            encoding="utf-8",
        )
        restarter = subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", str(script),
                "-ProcessId", str(os.getpid()), "-Executable", str(Path(sys.executable).resolve()),
                "-SourceScript", source_script, "-Ready", str(ready),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 5
        while not ready.exists() and restarter.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            raise RuntimeError("重启程序启动失败")
        ready.unlink(missing_ok=True)
        self.stopping.set()
        self.refresh_wakeup.set()
        if self.tray:
            self.tray.stop()
        if self.window:
            self.window.destroy()
        return {"ok": True}

    def window_action(self, action: str) -> dict[str, Any]:
        if not self.window:
            return {"ok": False}
        if action == "minimize":
            self.visible = False
            hwnd = self._window_handle()
            if hwnd:
                user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
            else:
                threading.Thread(target=self.window.minimize, name="window-minimize", daemon=True).start()
        elif action == "maximize":
            hwnd = self._window_handle()
            if hwnd and user32.IsZoomed(hwnd):
                self.window.restore()
            else:
                self.window.maximize()
            hwnd = self._window_handle()
            self.maximized = bool(hwnd and user32.IsZoomed(hwnd))
            self._set_window_corner(self.maximized)
            self._push_window_state()
            self.visible = True
        elif action == "close":
            selected = self._handle_close_request()
            return {"ok": True, "action": selected}
        self.refresh_wakeup.set()
        return {"ok": True, "visible": self.visible, "maximized": self.maximized}

    def hide_window(self) -> None:
        self.visible = False
        self.refresh_wakeup.set()
        ui_hide_callback = getattr(self, "ui_hide_callback", None)
        if ui_hide_callback:
            ui_hide_callback()
            return
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is not None:
            try:
                from System import Action

                native_form.BeginInvoke(Action(native_form.Hide))
                return
            except Exception:
                pass
        if self.window:
            threading.Thread(target=self.window.hide, name="window-hide", daemon=True).start()

    def show_window(self) -> None:
        self.visible = True
        self.refresh_wakeup.set()
        ui_show_callback = getattr(self, "ui_show_callback", None)
        if ui_show_callback:
            ui_show_callback()
            return

        def show_native_window() -> None:
            if not self.window:
                return
            native_form = getattr(self.window, "native", None)
            if native_form is not None:
                native_form.Show()
                native_form.Activate()
            else:
                self.window.show()
            hwnd = self._window_handle()
            if hwnd and user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)

        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is not None:
            try:
                from System import Action

                native_form.BeginInvoke(Action(show_native_window))
            except Exception:
                threading.Thread(target=show_native_window, name="window-show", daemon=True).start()
        else:
            threading.Thread(target=show_native_window, name="window-show", daemon=True).start()

    def set_window_background(self, mode: Any) -> dict[str, Any]:
        color_hex = "#020617" if str(mode).lower() == "dark" else "#ffffff"
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is None:
            return {"ok": False}

        def apply_background() -> None:
            from System.Drawing import Color, ColorTranslator

            native_form.BackColor = ColorTranslator.FromHtml(color_hex)
            native_webview = getattr(native_form, "webview", None)
            if native_webview is not None:
                native_webview.DefaultBackgroundColor = Color.FromArgb(
                    255,
                    int(color_hex[1:3], 16),
                    int(color_hex[3:5], 16),
                    int(color_hex[5:7], 16),
                )

        try:
            from System import Action

            native_form.BeginInvoke(Action(apply_background))
            return {"ok": True, "color": color_hex}
        except Exception:
            return {"ok": False}

    def push_state_to_ui(self) -> None:
        if not self.window or not self.visible:
            return
        state = json.dumps(self.get_state(), ensure_ascii=False)
        try:
            self.window.evaluate_js(f"window.applyBackendState({state});")
        except Exception:
            pass

    def exit_app(self) -> None:
        self._flush_window_size()
        pending_update = getattr(self, "pending_update_path", None)
        if pending_update and Path(pending_update).is_file():
            self.pending_update_path = None
            self._launch_updater(Path(pending_update))
            return
        self.stopping.set()
        self.refresh_wakeup.set()
        if self.tray:
            self.tray.stop()
        if self.window:
            self.window.destroy()

    def resolve_close_action(self, action: Any) -> dict[str, Any]:
        clean = str(action).lower()
        if clean not in {"exit", "tray"}:
            return {"ok": False, "error": "无效的关闭操作"}
        self._handle_close_request(clean)
        return {"ok": True, "action": clean}
