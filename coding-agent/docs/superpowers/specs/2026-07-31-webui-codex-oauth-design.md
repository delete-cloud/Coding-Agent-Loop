# WebUI Codex OAuth 登录(device flow)+ 多账号 — 设计

日期:2026-07-31(同日修订:加入多账号并存)
状态:已确认(决策记录在 PR 描述,ADR-lite)

## 背景与目标

o6n 部署的 coding agent 支持 provider=codex(ChatGPT OAuth),但:

- OAuth 登录只有 CLI 入口,网页端无登录能力;
- 当前 pod 内 refresh token 已失效(403),恢复只能进 pod 操作;
- `~/.coding-agent/oauth/auth.json` 位于容器 overlay fs,pod 重建即丢;
- 只支持单账号记录,无法在多个 ChatGPT 账号间切换。

目标:在 webui 完成 codex device-code 登录全流程;OAuth 记录持久化到数据 PVC;**多账号并存,新建会话时可选账号**;支持多个并发登录流程的管理。

非目标:其他 provider 的 OAuth(anthropic/copilot redirect 型)、远端 revoke(只删本地记录,CLI 已有 revoke)。

## 关键决策

1. **形态:只做 codex device flow**。Codex OAuth 原生 device-code 流程(`CodexOAuthClient.request_device_code` / `poll_device_token` / `exchange_device_code`),天然适合 headless 服务器。
2. **多账号 = 复合 provider key**:`OAuthStore`/`StoreBackedTokenSource` 本就按 provider_name 做 key,直接用 `codex`(默认账号)和 `codex:<label>`(命名账号)并存,存储层零迁移——现有 `providers.codex` 单记录格式不变。
3. **账号路由:provider 字符串扩展**。`CreateSessionRequest.provider` 从 `ProviderName` 字面量放开为:字面量 ∪ `codex:<label>`(label 校验 `^[a-z0-9][a-z0-9-]{0,30}$`)。`LLMProviderPlugin` 增加 `codex:*` 分支:按完整 key 取 OAuth 记录、按 key 刷新;未连接的 key 报清晰错误("account not connected: codex:work,connect via /oauth/codex/start")。
4. **持久化:`OAuthStore` 路径加 env 覆盖**(`CODING_AGENT_OAUTH_AUTH_PATH`),helm 指到数据 PVC `/var/lib/coding-agent/data/oauth/auth.json`。不引入 k8s secret 方案。
5. **多并发登录流程**:每个 device flow 有服务端 `flow_id`,可独立查询/取消,无互斥。同名账号重复登录 = 覆盖该 label 的记录(重登录场景,预期行为)。
6. **label 来源**:start 时可传 `label`;不传则在登录成功后从 id_token 解析(email 优先,其次 chatgpt_account_id 短码),冲突时追加序号。
7. **鉴权**:与现有 API 一致(`verify_api_key`)。

## 服务端设计

### 新端点(`server/http_server.py`,契约文档同步更新)

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/oauth/codex/start` | body `{label?}`;请求 device code,创建 flow,起后台轮询 → `{flow_id, verification_url, user_code, expires_in}` |
| GET | `/oauth/codex/flows` | 所有 in-flight 及近期完成的 flow |
| GET | `/oauth/codex/flows/{flow_id}` | `{state: pending|authorized|error|expired|cancelled, verification_url?, user_code?, account_label?, error?}` |
| POST | `/oauth/codex/flows/{flow_id}/cancel` | 中止轮询,标记 cancelled |
| GET | `/oauth/accounts` | 已连接账号列表:`[{provider: "codex"|"codex:<label>", label, email?, plan?, connected_at}]`(读 OAuthStore,解析 id_token claims) |
| DELETE | `/oauth/accounts/{provider_key}` | 删除该 key 的本地记录(**不做远端 revoke**:token 随记录删除,换账号/重登录无影响;需在 OpenAI 账号侧彻底清理时用 CLI `oauth logout codex --revoke` 或账号设置页) |

### Flow 管理器(新模块 `server/oauth_flows.py`)

- 内存注册表 `dict[flow_id, FlowState]`(state、urls、label、expires_at、error、task handle);TTL 10 分钟,过期/完成/取消的保留查询,重启清空。
- 后台任务:poll → exchange → 写 `OAuthStore`(key = `codex` 或 `codex:<label>`)→ authorized;异常 → error(空 str 用 `exception_error_message()` 兜底,复用 #685 helper)。
- 并发任意数量;`OAuthStore` 文件锁保证写入安全。

### Provider 插件改造(`plugins/llm_provider.py`)

- `elif self._provider_name == "codex" or self._provider_name.startswith("codex:")`:取完整 key 的记录;`StoreBackedCodexTokenSource` 参数化 provider key(现硬编码 "codex");refresh 回写同 key。
- 未连接 → `RuntimeError("codex account not connected: <key>")`。
- `schemas.py`:`CreateSessionRequest.provider` 类型放宽 + validator;`ProviderName` 字面量本身不动(其他端点不受影响)。
- 默认账号语义:provider 传 `codex` = 默认账号(记录 key `codex`),不传 provider = 服务端 config 默认(现状不变)。

### 存储路径

- `oauth/store.py` 加 `default_auth_path()`:`CODING_AGENT_OAUTH_AUTH_PATH` env 优先,否则 `~/.coding-agent/oauth/auth.json`。
- `LLMProviderPlugin`、oauth CLI、flow 管理器统一走该函数。
- SRE helm values 加 env(后续 SRE PR)。

## 前端设计

**Settings 面板 — "Codex 账号"卡片**
- 账号列表(`GET /oauth/accounts`):label/email/plan 徽章;每行"断开"(确认后 DELETE)。
- "添加账号"→ start(可选输入 label)→ 显示 verification_url(链接)+ user_code(大号等宽,一键复制)→ 3s 轮询 flow 状态 → 成功刷新账号列表;失败/过期显示原因可重试。
- 进行中的 flow 带取消按钮;页面重开可从 flows 列表恢复 pending 状态。

**新建会话(Header)**
- provider 下拉:静态 9 项之外,`GET /oauth/accounts` 返回的每个 codex 账号追加为 `codex:<label>` 选项;拉取失败则只显示静态 `codex`。
- codex 系 provider 的 model 输入维持手输(Responses API 无 models 列表),datalist 给 gpt-5.5/gpt-5.4/gpt-5.2 预设。

## 错误处理

| 场景 | 行为 |
|---|---|
| device code 请求失败 | start 502 + 详情,不建 flow |
| 轮询超时/用户未授权 | flow → expired,可重试 |
| exchange 失败 | flow → error(带消息) |
| 用未连接的 `codex:<label>` 建会话 | 创建即 400,错误消息指向连接入口 |
| 会话进行中账号被断开 | 下次调用 401 → 刷新失败 → turn 报错(不自动切换账号) |

## 测试

- 服务端(`tests/ui/test_oauth_flows.py`):start/list/cancel/status;并发两 flow 互不干扰;authorized 写入正确 key(显式 label / email 兜底 / 冲突加序号);accounts 列表与 DELETE;`codex:work` 建会话路由到对应记录(mock store);未连接 key 的 400。
- 插件:`llm_provider` codex:* 分支(记录查找、token source key、未连接报错);store `default_auth_path` env 覆盖。
- 前端:账号卡片渲染/添加/断开;provider 下拉动态账号项;轮询恢复。
- 回归:tests/coding_agent/plugins/test_llm_provider.py codex 既有用例(默认 key 行为不变)。

## 交付拆分

1. Coding-Agent-Loop PR:store env 路径 + flow 管理器与端点 + 插件 codex:* 路由 + 前端 + 契约文档 + 测试。
2. SRE PR:helm values 加 `CODING_AGENT_OAUTH_AUTH_PATH`。
3. 部署后在 `agent.mesh.kinaz.me` 真实走一遍:连接两个账号 → 分别建会话验证。
