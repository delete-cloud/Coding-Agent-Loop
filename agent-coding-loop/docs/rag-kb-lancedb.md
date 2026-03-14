# RAG 知识库（LanceDB Sidecar）

本项目采用“Go 编排 + Python LanceDB sidecar”的方式，把 RAG 检索作为只读工具 `kb_search` 注入到 Coder/Reviewer 的 ReAct 工具集合中。

## 1. 启动 sidecar

在 `Coding-Agent-Loop/agent-coding-loop` 目录下执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r kb/requirements.txt
```

配置 embedding（远端 API 方案）：

```bash
export OPENAI_BASE_URL="https://api.openai.com"
export OPENAI_API_KEY="..."
export OPENAI_EMBEDDING_MODEL="text-embedding-3-small"
```

启动服务：

```bash
python3 kb/server.py --listen 127.0.0.1:8788
```

## 1.1 使用本地 embedding（HuggingFace / ModelScope）

安装额外依赖：

```bash
pip install -r kb/requirements-local-embedding.txt
```

推荐小模型（2026）：

- `Qwen/Qwen3-Embedding-0.6B`（主推，多语言，中文/代码场景更均衡）
- `BAAI/bge-small-zh-v1.5`（更轻量的中文备选）

HuggingFace 下载（默认）：

```bash
export KB_EMBEDDING_PROVIDER="local"
export KB_LOCAL_EMBED_MODEL="Qwen/Qwen3-Embedding-0.6B"
python3 kb/server.py
```

ModelScope 下载（国内推荐 / 网络受限场景）：

```bash
export KB_EMBEDDING_PROVIDER="local"
export KB_EMBEDDING_SOURCE="modelscope"
export KB_LOCAL_EMBED_MODEL="Qwen/Qwen3-Embedding-0.6B"
python3 kb/server.py
```

## 2. 建索引

sidecar 暴露 `/index`，默认会从 `KB_DOC_ROOTS` 扫描并切分为 chunks 后写入 LanceDB。

`/index` 现在是非破坏性的常规入口：

- 请求体未传 `roots` 时，仍会回退到 `KB_DOC_ROOTS` 或当前目录。
- 写入失败时不会再自动 `drop_table()` 销毁旧表。
- 如果当前表状态与写入要求冲突，sidecar 会返回 `409` 和机器可读的 `code=rebuild_required`，调用方需要显式走 `/rebuild`。
- 如果后台正在执行 `/rebuild`，普通 `/index` 会返回 `409` 和 `code=rebuild_in_progress`。

示例（索引当前仓库）：

```bash
curl -sS -X POST http://127.0.0.1:8788/index \
  -H 'content-type: application/json' \
  -d '{"roots":["."],"chunk_size":1200,"overlap":200}'
```

## 2.1 全表重建

`/rebuild` 是唯一允许重建整张表的入口。它和 `/index` 的区别是：

- `roots` 必填，不会回退到 `KB_DOC_ROOTS` 或 `.`。
- sidecar 先构建临时表，再短暂切换正式表名。
- 成功后会保留 1 份最近备份表，供人工回滚。
- 重建期间 `/search` 继续服务；普通 `/index` 和第二个 `/rebuild` 会收到 `409 rebuild_in_progress`。

示例（显式重建 docs + eval/ab/kb）：

```bash
curl -sS -X POST http://127.0.0.1:8788/rebuild \
  -H 'content-type: application/json' \
  -d '{
    "roots": ["docs", "eval/ab/kb"],
    "exts": ["md"],
    "chunk_size": 900,
    "overlap": 120
  }'
```

成功响应至少包含：

- `rebuilt=true`
- `table`
- `backup_table`
- `roots`
- `indexed`
- `db_path`

## 2.2 表名约定

第一版固定使用单槽命名：

- 正式表：`<table>`，默认是 `chunks`
- 临时重建表：`<table>__rebuild_tmp`
- 最近备份表：`<table>__backup`

正常查询始终读取正式表。`/rebuild` 成功后，上一版正式表会变成最近备份表；更老的备份不会继续保留。

## 3. 在 Loop 中使用 kb_search（Agentic RAG）

Go 侧默认 KB URL 为 `http://127.0.0.1:8788`，也可通过环境变量覆盖：

```bash
export AGENT_LOOP_KB_URL="http://127.0.0.1:8788"
```

当 Coder/Reviewer 需要 repo 之外的背景知识时，使用 `kb_search` 工具检索并在输出 notes/findings 中引用返回的 `path#heading`。

## 4. 搜索 API（便于调试）

```bash
curl -sS -X POST http://127.0.0.1:8788/search \
  -H 'content-type: application/json' \
  -d '{"query":"agentic rag rerank","top_k":8,"query_type":"auto"}'
```

## 5. 人工回滚

第一版不提供 `/restore` API。若重建完成后发现新表内容有问题，请人工回滚最近备份表。

建议步骤：

1. 停止 sidecar，避免回滚过程中有新的查询或重建请求。
2. 确认当前表名：

```bash
python3 - <<'PY'
import lancedb
db = lancedb.connect(".agent-loop-artifacts/kb_lancedb")
print(list(db.table_names()))
PY
```

3. 先把当前正式表让开，再把最近备份切回正式表：

```bash
python3 - <<'PY'
import time
import lancedb

db = lancedb.connect(".agent-loop-artifacts/kb_lancedb")
bad_name = f"chunks__rollback_bad_{int(time.time())}"
db.rename_table("chunks", bad_name)
db.rename_table("chunks__backup", "chunks")
print({"rolled_back_to": "chunks", "previous_formal_saved_as": bad_name})
PY
```

4. 重启 sidecar。
5. 用 `/search` 或业务侧 `kb_search` 验证查询结果是否恢复。
