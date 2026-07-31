# WebUI Codex OAuth 登录(device flow)— 设计

日期:2026-07-31
状态:已确认(决策记录在 PR 描述,ADR-lite)

## 背景与目标

o6n 部署的 coding agent 支持 provider=codex(ChatGPT OAuth),但:

- OAuth 登录只有 CLI 入口(`coding-agent oauth login codex`),网页端无登录能力;
- 当前 pod 内 refresh token 已失效(403),恢复只能进 pod 操作;
- `~/.coding-agent/oauth/auth.json` 位于容器 overlay fs,pod 重建即丢。

目标:在 webui 完成 codex device-code 登录全流程,OAuth 记录持久化到数据 PVC,并支持多个并发登录流程的管理。

非目标:其他 provider 的 OAuth(anthropic/copilot redirect 型)、logout/撤销(CLI 已有)、多账号并存存储(见下)。

## 关键决策

1. **形态:只做 codex device flow**。Codex OAuth 原生是 device-code 流程(`CodexOAuthClient.request_device_code` / `poll_device_token` / `exchange_device_code`),天然适合 headless 服务器,无需托管 redirect。
2. **持久化:`OAuthStore` 路径加 env 覆盖,helm 指到数据 PVC**(`/var/lib/coding-agent/data/oauth/auth.json`)。不引入 k8s secret 方案(RBAC + 重启才生效,token 刷新还要写 secret,过重)。
3. **多流程管理:允许并发 in-flight flows**,每个 flow 有服务端生成的 `flow_id`,可独立查询/取消;不做"同一时刻仅一个流程"的互斥。存储层仍是一份 codex 记录(provider 为 key),**最后一次成功登录覆盖旧记录**——单用户内网部署,这是预期行为。
4. **鉴权:与现有 API 一致**(`verify_api_key`,X-API-Key)。

## 服务端设计

### 新端点(挂在 `server/http_server.py`,契约文档同步更新)

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/oauth/codex/start` | 请求 device code,创建 flow,起后台轮询任务 → `{flow_id, verification_url, user_code, expires_in}` |
| GET | `/oauth/codex/flows` | 列出所有 in-flight 及近期完成的 flow(state/时间) |
| GET | `/oauth/codex/flows/{flow_id}` | 单 flow 状态:`{state: pending|authorized|error|expired|cancelled, verification_url?, user_code?, error?}` |
| POST | `/oauth/codex/flows/{flow_id}/cancel` | 中止该 flow 的轮询任务,标记 cancelled |
| GET | `/oauth/codex/status` | 当前是否已连接:`{connected: bool, account?: {...}}`(读 OAuthStore) |

### Flow 管理器(新模块 `server/oauth_flows.py`)

- 内存注册表 `dict[flow_id, FlowState]`;FlowState 含 state、verification_url、user_code、expires_at、error、created_at、task handle。
- 后台任务:`poll_device_token`(client 自带轮询/超时)→ 成功 `exchange_device_code` → 写 `OAuthStore` → state=authorized;异常 → state=error(消息保留,空 str 用 `exception_error_message()` 兜底,复用 #685 的 helper)。
- TTL:device code 过期或 10 分钟未完成 → state=expired,任务退出。已完成/过期/取消的 flow 保留在注册表供查询,服务端重启即清空(不需要持久化 flow 状态)。
- 并发:任意数量 flow 并存;`OAuthStore` 自带文件锁,最后一次写入生效。

### 存储路径

- `OAuthStore.__init__` 已接受 path 参数;新增 env `CODING_AGENT_OAUTH_AUTH_PATH`,默认 `~/.coding-agent/oauth/auth.json` 不变。
- `LLMProviderPlugin` 的 codex 分支与 oauth CLI 共用该解析逻辑(单点:`oauth/store.py` 加 `default_auth_path()`)。
- helm values(SRE 仓库):`CODING_AGENT_OAUTH_AUTH_PATH=/var/lib/coding-agent/data/oauth/auth.json`(data PVC 已挂载,无需新卷)。

## 前端设计(Settings 面板新增 "Codex 连接" 卡片)

- 初始:GET `/oauth/codex/status` → 显示"已连接(account 信息)"或"未连接"。
- 点击"连接 codex"→ POST start → 卡片显示 verification_url(链接,新窗口打开)+ user_code(大号等宽,一键复制)→ 前端每 3s 轮询 `flows/{flow_id}` → authorized:显示成功并刷新 status;error/expired:显示原因 + 重试按钮。
- 多 flow:卡片历史区列出近期 flow(state 徽章),pending 的可取消。
- 失败兜底:轮询期间页面关闭不影响服务端任务;重新打开卡片可从 `GET /oauth/codex/flows` 恢复进行中的 flow。

## 错误处理

| 场景 | 行为 |
|---|---|
| device code 请求失败(网络/401) | start 返回 502 + 错误详情,无 flow 创建 |
| 轮询中超时/用户未授权 | flow → expired,前端提示重试 |
| exchange 失败 | flow → error(带消息) |
| 重复连接(已 connected) | 允许,登录成功即覆盖旧记录(重登录场景) |

## 测试

- 服务端(`tests/ui/test_oauth_flows.py`):start 成功返回 url+code;并发两个 flow 互不干扰;authorized 后写 OAuthStore(mock client);error/expired/cancel 状态流转;status 端点 connected/未连接。
- 前端:卡片状态渲染(未连接/pending/authorized/error)、轮询恢复、取消、复制 code。
- 回归:`tests/coding_agent/plugins/test_llm_provider.py` 的 codex 分支 + oauth store 测试(env 路径覆盖)。

## 交付拆分

1. Coding-Agent-Loop PR:服务端端点 + flow 管理器 + env 路径 + 前端卡片 + 契约文档 + 测试。
2. SRE PR:helm values 加 `CODING_AGENT_OAUTH_AUTH_PATH`。
3. 部署后在 `agent.mesh.kinaz.me` 走一遍真实登录验证。
