# Mirra Go 网关

从 `gateway` 目录运行 `go test ./...` 和 `go build -o mirra-gateway .`。复制 `.env.example` 为 `.env`，用 `./mirra-gateway -env .env` 启动。系统环境变量优先于 `.env`。沿用原手机服务的 `API_TOOLS_SERVICE_TOKEN` 可继续使用已签发的设备令牌。部署时使用 `deploy/imggen-gateway.service`，并将工作目录和发布目录与现有服务对齐。

管理页监听 `127.0.0.1:8771`，初始 PIN 为 `362514`。远端管理使用 `ssh -N -L 8771:127.0.0.1:8771 root@39.105.74.222`，然后打开 `http://127.0.0.1:8771/`。密钥签发后完整值只显示一次。分发密钥以 `sk-mirra-` 开头，自定义密钥填写此前缀之后的部分。数据目录保存加密后的上游凭证、分发密钥的哈希、请求记录和小时分配。备份时保留数据库与对应的 `MIRRA_STORAGE_SECRET`。如果该变量留空，网关用手机服务令牌派生加密密钥。

多把上游 Key 填入相同的 API 地址和池名，例如 `https://api.openai.com/v1` 和 `openai`。选择任意成员即可把分发密钥绑定到池。在同一个整点小时内，手机与桌面使用同一分发密钥会命中同一上游。每个小时首次调用时，网关根据当前预计的请求量和上一小时的实测负载重新分配，负载相同时沿用原上游。停用池中的成员后，绑定它的分发密钥会重新分配到启用的成员。

手机接口位于 `/api/v1`，现有 Nginx `/v1/` 继续映射到它。桌面和 SDK 的 OpenAI 兼容地址在外部是 `/openai/v1`，内部是 `/v1`。网关转发图片生成、编辑、变体和 Responses 请求，支持 JSON、multipart 和 SSE。用 `GET /openai/v1/usage` 查看分发密钥的配额和 API 回执用量。Nginx 配置在转发时覆盖 `X-Real-IP`，启用 `MIRRA_TRUST_LOCAL_PROXY` 后，网关只信任来自环回地址的该头部。

5 小时和 7 天请求限额统计已准入的上游调用。一次生成多张或使用思维模式可能产生多次调用。USD 模式可设置累计和滚动 5 小时、24 小时、7 天额度。开启前填写 `MIRRA_USD_RESERVE_PER_REQUEST` 和 `MIRRA_PRICING_FILE`。请求开始时按配置预留，完成后根据上游返回的 token 和配置的费率核算。缺少可计价用量、使用外部工具或特殊服务费率时，保留预留金额。若实际金额高于预留，网关记入实际核算额并据此限制后续请求。此金额用于网关限额，OpenAI 账单以平台结算为准。

费率文件是按模型 ID 索引的 JSON。费率单位为每百万 token 的 USD，填字符串以保留小数精度。文本模型需要 `textInput` 和 `output`。图像模型还需要 `image: true` 和 `imageInput`。可选 `textCached` 和 `imageCached` 用于上游明确报告的缓存 token。下面的值仅演示文件格式。

```json
{
  "example-image-model": {
    "image": true,
    "textInput": "5.00",
    "imageInput": "8.00",
    "output": "30.00"
  }
}
```

SVG、SVG 作者模型和思维模型在 `.env` 配置。手机端仅在 `MIRRA_SVG_ENABLED=true` 时提供 SVG 选项。官方套餐权限与模型参数由当前上游决定。
