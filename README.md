# API_TOOLS

Windows API 密钥额度监控工具。桌面壳使用 Python 3.12、pywebview（Edge WebView2）、SQLite、系统托盘和 Windows 通知。

当前 `imagen` 分支在主监控器之外额外提供图片生成功能；`main` 分支仅维护额度监控器。图片请求校验、JSON/multipart 传输、流式响应解析和结果落盘集中在 `image_editor.py`，`app.py` 只负责凭据、窗口和 RPC 适配。

## 功能

- 保持原 HTML 模板的展示层与响应式布局。
- 通过 `OPENAI_BASE_URL` 或默认 EasyClin 地址查询 `/models` 与 `/usage`。
- 前台每 60 秒、窗口隐藏或最小化后每 5 分钟刷新。
- 总额度、5h、1d、7d 剩余比例进入 25% / 10% / 5% 时发送 Windows 通知。
- SQLite 保留最近 30 天的用量采样和每日明细。
- API Key 使用当前 Windows 用户的 DPAPI 加密后再写入 SQLite。
- 生图模式默认使用顶部当前选中的 API Key；无参考图时调用 `/images/generations`，添加最多 16 张参考图后调用 `/images/edits`。
- 参考图支持文件选择、剪贴板粘贴和拖拽导入，并以缩略图展示；超过 16 张时仅导入剩余槽位，单张超过 50 MB 时跳过并提示。
- 提示词输入框会随换行增长到左侧父容器的可用高度，达到 75% 时提供全屏编辑入口，达到上限后仅在输入框内部滚动；全屏编辑器从应用内容区开始并提供返回按钮，不覆盖标题栏。
- 每次可生成 1–9 张图片；应用会发起相应数量的独立请求并限制为最多 3 路并发，不会向不支持 `n` 的 EasyClin 接口传递该参数。
- 右侧按每次提交创建一个图片集，每张图片的排队、生成、partial 过程预览、完成或失败状态都会实时更新。
- 左下生成设置使用并排的四段选择条控制生成细节（自动/低/中/高）和保存质量（小/中/大/无损），并可选择比例和 1–9 张生成数量。
- 图片接口始终请求 PNG；本机可按小（JPEG 55）、中（JPEG 75）、大（JPEG 90）、无损 PNG 四档保存。背景在后台固定为自动，审核固定为低，并默认开启流式响应和 3 张 partial 过程预览。
- 最终图片自动保存到 Windows“图片”目录下的 `DJYX_APITOOL` 文件夹，可从结果区直接打开文件夹或将单图另存为其他位置。
- 点击单张图片只打开支持拖拽和滚轮缩放的查看器，不会改变参考图；只有图片集右侧的“继续编辑”按钮会将该轮结果切换为下一轮参考图，返回后恢复进入会话前的草稿。
- 图片集会在应用重启后恢复，并可连同该轮原件、压缩预览和记录一起删除；异常退出遗留的生成任务会标记为“已中断”，已完成图片仍会保留。
- 关闭按钮可设置为关闭应用、最小化到系统托盘或每次询问。
- 可设置随 Windows 开机自动启动。
- 从 GitHub Release 检查、下载并安装新版本，下载进度使用自适应点阵进度条。
- 更新检查频率支持每次启动、每周或仅手动检测，应用内可查看更新日志。

## 本地数据

数据库位于 `%LOCALAPPDATA%\API_TOOLS\api_tools.db`。流式 partial 过程图暂存在 `%LOCALAPPDATA%\API_TOOLS\image-generations\partials`。最终图片会按会话写入 Windows“图片”目录下的 `DJYX_APITOOL\sessions\<session-id>`：根目录的 `manifest.json` 保存提示词、轮次和参数，每个轮次目录分别保存原件与 JPEG 压缩预览。密钥不会以明文写入数据库或日志。启动阶段耗时记录在 `%LOCALAPPDATA%\API_TOOLS\startup.log`，用于区分单文件解包、WebView 首屏与首次网络刷新耗时。

## 生图参数兼容性

EasyClin 会把图片请求转换到内部图片工具，并非所有 Images API 参数都会原样执行。当前实测：`background`、`moderation`、`stream` 生效；`partial_images` 可用但实际返回数量可能不同。应用在后台固定使用 `background=auto`、`moderation=low`、`stream=true` 和 `partial_images=3`。尺寸支持提交符合 GPT Image 2 限制的自定义宽高，但 EasyClin 可能重写请求值，例如 `768x1024` 实测返回 `1254x1254`，因此不能视为严格透传。`quality` 表示模型生成细节/推理强度，主要影响视觉质量、耗时和成本，并不直接指定像素分辨率；EasyClin 也可能覆盖该值，例如 `low` 实测变为 `auto`。`n` 已确认不支持，图片数量由应用编排独立请求实现；`user` 疑似被忽略，因此界面不提供这两个参数。

为保证输出大小行为稳定，应用向 EasyClin 固定发送 `output_format=png`，收到最终图后再由本机执行 PNG 无损保存或 JPEG 90/75/55 压缩。

## 开发运行

在项目虚拟环境安装 `requirements.txt` 后运行 `app.py`。页面样式只维护 `assets/app.scss`，通过固定版本的 Dart Sass 编译为 `assets/app.css`；开发时可运行 `npm run build:css` 单次编译，或运行 `npm run watch:css` 持续编译。图标首次运行时从国内镜像校验并缓存。

## 构建

构建机需要 Node.js/npm。运行 `build.ps1` 后，脚本会在缺少本地依赖时执行 `npm ci`，随后强制编译 SCSS，并将生成的 CSS、页面资源与 `CHANGELOG.md` 打包为单文件便携版 `dist\API_TOOLS.exe`。同时会生成 `dist\API_TOOLS.exe.sha256`。Python 运行时和 DLL 已嵌入 EXE，目标机器无需安装 Python 或 Node.js，也不要再复制旧目录版中的单独 EXE。目标机器仍需安装 Microsoft Edge WebView2 Runtime（Windows 11 默认包含）。

## 发布与自动更新

`imagen` 分支使用独立更新通道，只接受同时满足以下条件的 GitHub Release：

- 标签使用 `imagen-vMAJOR.MINOR.PATCH`，例如 `imagen-v1.0.17`。
- Release 的目标分支（`target_commitish`）必须是 `imagen`。
- `main` 分支使用的 `vMAJOR.MINOR.PATCH` Release 不会被本分支识别。

每个 `imagen` Release 必须上传以下两个同名资产：

- `API_TOOLS.exe`
- `API_TOOLS.exe.sha256`

应用只从配置的 GitHub 仓库读取目标为 `imagen` 且带 `imagen-v` 标签前缀的最新正式 Release。GitHub API 不可用时，Atom 备用通道仍只接受 `imagen-v` 标签。下载完成后会校验 SHA-256，通过后由独立 PowerShell 进程替换当前 EXE 并重启。每次发布前需要更新 `APP_VERSION` 和 `CHANGELOG.md`，Release 描述填写该版本的简要更新日志。
