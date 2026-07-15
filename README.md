# API_TOOLS

Windows API 密钥额度监控工具。桌面壳使用 Python 3.12、pywebview（Edge WebView2）、SQLite、系统托盘和 Windows 通知。

## 功能

- 保持原 HTML 模板的展示层与响应式布局。
- 通过 `OPENAI_BASE_URL` 或默认 EasyClin 地址查询 `/models` 与 `/usage`。
- 前台每 60 秒、窗口隐藏或最小化后每 5 分钟刷新。
- 总额度、5h、1d、7d 剩余比例进入 25% / 10% / 5% 时发送 Windows 通知。
- SQLite 保留最近 30 天的用量采样和每日明细。
- API Key 使用当前 Windows 用户的 DPAPI 加密后再写入 SQLite。
- 关闭按钮可设置为关闭应用、最小化到系统托盘或每次询问。
- 可设置随 Windows 开机自动启动。
- 从 GitHub Release 检查、下载并安装新版本，下载进度使用自适应点阵进度条。
- 更新检查频率支持每次启动、每周或仅手动检测，应用内可查看更新日志。

## 本地数据

数据库位于 `%LOCALAPPDATA%\API_TOOLS\api_tools.db`。密钥不会以明文写入数据库或日志。启动阶段耗时记录在 `%LOCALAPPDATA%\API_TOOLS\startup.log`，用于区分单文件解包、WebView 首屏与首次网络刷新耗时。

## 开发运行

在项目虚拟环境安装 `requirements.txt` 后运行 `app.py`。页面样式只维护 `assets/app.scss`，通过固定版本的 Dart Sass 编译为 `assets/app.css`；开发时可运行 `npm run build:css` 单次编译，或运行 `npm run watch:css` 持续编译。图标首次运行时从国内镜像校验并缓存。

## 构建

构建机需要 Node.js/npm。运行 `build.ps1` 后，脚本会在缺少本地依赖时执行 `npm ci`，随后强制编译 SCSS，并将生成的 CSS、页面资源与 `CHANGELOG.md` 打包为单文件便携版 `dist\API_TOOLS.exe`。同时会生成 `dist\API_TOOLS.exe.sha256`。Python 运行时和 DLL 已嵌入 EXE，目标机器无需安装 Python 或 Node.js，也不要再复制旧目录版中的单独 EXE。目标机器仍需安装 Microsoft Edge WebView2 Runtime（Windows 11 默认包含）。

## 发布与自动更新

GitHub Release 的标签使用 `vMAJOR.MINOR.PATCH`，例如 `v1.0.0`。每个 Release 必须上传以下两个同名资产：

- `API_TOOLS.exe`
- `API_TOOLS.exe.sha256`

应用只从配置的 GitHub 仓库读取最新正式 Release。下载完成后会校验 SHA-256，通过后由独立 PowerShell 进程替换当前 EXE 并重启。每次发布前需要更新 `APP_VERSION` 和 `CHANGELOG.md`，Release 描述填写该版本的简要更新日志。
