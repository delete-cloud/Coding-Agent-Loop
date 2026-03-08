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

示例（索引当前仓库）：

```bash
curl -sS -X POST http://127.0.0.1:8788/index \
  -H 'content-type: application/json' \
  -d '{"roots":["."],"chunk_size":1200,"overlap":200}'
```

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
