# Mirra Go 网关

从 `gateway` 目录运行 `go test ./...` 和 `go build -o mirra-gateway .`。复制 `.env.example` 为 `.env`，用 `./mirra-gateway -env .env` 启动。系统环境变量优先于 `.env`。沿用原手机服务的 `API_TOOLS_SERVICE_TOKEN` 可继续使用已签发的设备令牌。部署时使用 `deploy/imggen-gateway.service`，并将工作目录和发布目录与现有服务对齐。

管理页监听 `127.0.0.1:8771`，初始 PIN 为 `362514`。远端管理使用 `ssh -N -L 8771:127.0.0.1:8771 root@39.105.74.222`，然后打开 `http://127.0.0.1:8771/`。密钥签发后完整值只显示一次。分发密钥以 `sk-mirra-` 开头，自定义密钥填写此前缀之后的部分。数据目录保存加密后的上游凭证、分发密钥的哈希、请求记录和小时分配。备份时保留数据库与对应的 `MIRRA_STORAGE_SECRET`。如果该变量留空，网关用手机服务令牌派生加密密钥。

多把上游 Key 填入相同的 API 地址和池名，例如 `https://api.openai.com/v1` 和 `openai`。选择任意成员即可把分发密钥绑定到池。在同一个整点小时内，手机与桌面使用同一分发密钥会命中同一上游。每个小时首次调用时，网关根据当前预计的请求量和上一小时的实测负载重新分配，负载相同时沿用原上游。停用池中的成员后，绑定它的分发密钥会重新分配到启用的成员。

手机接口位于 `/api/v1`，现有 Nginx `/v1/` 继续映射到它。桌面和 SDK 的 OpenAI 兼容地址在外部是 `/openai/v1`，内部是 `/v1`。网关转发图片生成、编辑、变体和 Responses 请求，支持 JSON、multipart 和 SSE。用 `GET /openai/v1/usage` 查看分发密钥的配额和 API 回执用量。Nginx 配置在转发时覆盖 `X-Real-IP`，启用 `MIRRA_TRUST_LOCAL_PROXY` 后，网关只信任来自环回地址的该头部。

### 管理工作区

总览、上游号池、分发密钥、用量明细和代理配置分别有独立导航。号池可以批量创建成员、追加成员、测试连通性、停用成员或重命名。代理支持跟随 `.env`、直连和自定义 HTTP(S) / SOCKS5(SOCKS5H)；自定义代理可以只用于 OpenAI 官方接口。测试连接仅访问官方模型列表。保存后对新请求立即生效，代理凭证在 SQLite 中加密。

SOCKS5 和 SOCKS5H 支持标准地址 `socks5://user:pass@host:1080` 以及代理商导出地址 `socks5://host:1080:user:pass`。IPv6 主机地址使用方括号。

在「模型配置」选择来源账户后，网关通过该账户的 `GET /models` 和当前出站代理获取模型目录。可配置开放的生图模型、客户端默认生图模型、SVG 矢量化模型与深度，以及 Luna、Terra、Sol 的思维模型与深度。设置保存在 SQLite 中。Android 前台每分钟刷新并接收模型变更事件。Windows 在既有的前后台刷新周期读取 `GET /openai/v1/usage` 的 `appModels`。预览从发布目录的签名 Android 安装包读取客户端页面，展示当前草稿及真实底部导航。

### USD 窗口和回执

三个窗口及累计额度均以 USD 为单位，0 表示不限。与 Sub2 的窗口起算规则一致，5 小时从首个请求起算，日窗口在北京时间零点刷新，7 天窗口以起算日的北京时间零点为锚。窗口到期后，下一次请求或读取会刷新状态。管理员可调整各限额、分别重置累计或分时用量，或选择一个窗口重置。修改上限保留已用额。重置后的新窗口从下一次请求起算。重置之前已受理的请求在之后结算仍计入历史账目。数据库保留请求、token、结算金额与额度变动记录。

`POST /admin/keys` 返回操作结果和完整的 `usage`。管理端使用 `POST /admin/keys/{id}/reset`，提交 `scope` 和上次快照的 `revision`。范围可以是 `5h`、`1d`、`7d`、`windows`、`total` 或 `all`。刷新、修改、重置共用服务端计算的 `remaining`、`remainingPercent`、`reset_at` 和 USD 单位。客户端使用 `GET /openai/v1/usage` 读取状态、统计和近期变动，用 `GET /openai/v1/usage/events?after=0` 按游标顺序读变动，每页最多 64 条。手机端收到额度变动 SSE 后刷新状态，任务完成也返回额度快照。统计曲线按请求发生时间归档，支持 10 分钟和 1 小时的费用明细。

`MIRRA_PRICING_FILE` 指定费率表，`MIRRA_USD_RESERVE_PER_REQUEST` 指定有限额密钥每次请求的预留金额。附带的 `openai-standard.json` 覆盖 GPT Image 2、2.5/2.6 Flare 和 Sunburst，以及三个默认推理模型。值为每百万 token 的 USD。标准文本费用区分缓存读取和 30 分钟、12 小时缓存写入。2026-11-19 核对的标准费率见 [OpenAI Pricing](https://developers.openai.com/api/docs/pricing)。

实际可计价回执到达后，网关按微美元结算；缺少可计价回执、使用额外工具或特殊处理费率时保留预留金额。实际费用超过预留时按实际费用入账，并据此限制后续请求。网关额度和供应商账单分别结算。

`.env` 提供初始模型设置及第三方密钥的兼容配置。签发的 Mirra 分发密钥使用工作区保存的配置。模型能力和套餐权限由上游决定。
