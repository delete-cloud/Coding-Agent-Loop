import argparse
import json
import os
import pathlib
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _json_dumps(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read_json(req):
    length = int(req.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = req.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _openai_embeddings(base_url, api_key, model, texts, timeout_s):
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        url = base + "/embeddings"
    else:
        url = base + "/v1/embeddings"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    payload = {"model": model, "input": texts}
    data = _json_dumps(payload)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read()
    decoded = json.loads(body.decode("utf-8"))
    items = decoded.get("data") or []
    items.sort(key=lambda x: x.get("index", 0))
    out = []
    for it in items:
        emb = it.get("embedding")
        out.append(emb)
    if len(out) != len(texts):
        raise RuntimeError("embedding response size mismatch")
    return out


def _normalize_vec(v):
    if not v:
        return v
    s = 0.0
    for x in v:
        fx = float(x)
        s += fx * fx
    if s <= 0.0:
        return [float(x) for x in v]
    inv = 1.0 / (s ** 0.5)
    return [float(x) * inv for x in v]


class _EmbedderOpenAI:
    def __init__(self, base_url, api_key, model, normalize):
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._model = model or ""
        self._normalize = bool(normalize)

    def ready(self):
        return bool(self._base_url and self._model)

    def embed(self, texts, timeout_s):
        vecs = _openai_embeddings(self._base_url, self._api_key, self._model, texts, timeout_s)
        out = []
        for v in vecs:
            if self._normalize:
                out.append(_normalize_vec(v))
            else:
                out.append([float(x) for x in v])
        return out


class _EmbedderLocal:
    def __init__(self, model_id, source, cache_dir, normalize):
        self._model_id = (model_id or "").strip()
        self._source = (source or "huggingface").strip().lower()
        self._cache_dir = (cache_dir or "").strip()
        self._normalize = bool(normalize)
        self._model = None
        self._lock = threading.Lock()

    def ready(self):
        return bool(self._model_id)

    def _resolve_model_path(self):
        if self._source != "modelscope":
            return self._model_id
        try:
            from modelscope.hub.snapshot_download import snapshot_download
        except Exception as e:
            raise RuntimeError("modelscope is required for KB_EMBEDDING_SOURCE=modelscope") from e
        kwargs = {}
        if self._cache_dir:
            kwargs["cache_dir"] = self._cache_dir
        path = snapshot_download(self._model_id, **kwargs)
        return path

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as e:
                raise RuntimeError("sentence-transformers is required for KB_EMBEDDING_PROVIDER=local") from e
            model_path = self._resolve_model_path()
            self._model = SentenceTransformer(model_path)
            return self._model

    def embed(self, texts, timeout_s):
        _ = timeout_s
        model = self._ensure_model()
        vecs = model.encode(texts, normalize_embeddings=self._normalize)
        out = []
        for row in vecs:
            out.append([float(x) for x in row.tolist()])
        return out


def _chunk_text(text, chunk_size, overlap):
    if chunk_size <= 0:
        return []
    if overlap < 0:
        overlap = 0
    step = max(1, chunk_size - overlap)
    out = []
    i = 0
    n = len(text)
    while i < n:
        j = min(n, i + chunk_size)
        chunk = text[i:j].strip()
        if chunk:
            out.append((i, j, chunk))
        if j >= n:
            break
        i += step
    return out


def _should_index_path(p: pathlib.Path):
    name = p.name.lower()
    if name.startswith("."):
        return False
    if name in {"node_modules", ".git", ".agent-loop-artifacts", "dist", "build", "target", "vendor"}:
        return False
    return True


def _iter_files(roots):
    for root in roots:
        rp = pathlib.Path(root).resolve()
        if not rp.exists():
            continue
        if rp.is_file():
            yield rp
            continue
        for dirpath, dirnames, filenames in os.walk(rp):
            d = pathlib.Path(dirpath)
            dirnames[:] = [x for x in dirnames if _should_index_path(pathlib.Path(x))]
            for fn in filenames:
                fp = d / fn
                if not _should_index_path(fp):
                    continue
                yield fp


def _load_text_file(path: pathlib.Path, max_bytes):
    try:
        with path.open("rb") as f:
            if max_bytes > 0:
                b = f.read(max_bytes)
            else:
                b = f.read()
    except Exception:
        return None
    try:
        return b.decode("utf-8")
    except Exception:
        try:
            return b.decode("utf-8", errors="ignore")
        except Exception:
            return None


def _stable_doc_path(path: pathlib.Path, roots):
    fp = pathlib.Path(path).resolve()
    cwd = pathlib.Path.cwd().resolve()
    try:
        return fp.relative_to(cwd).as_posix()
    except ValueError:
        pass

    best_rel = None
    best_len = -1
    for root in roots or []:
        try:
            rp = pathlib.Path(root).resolve()
            rel = fp.relative_to(rp)
        except Exception:
            continue
        if len(rp.parts) > best_len:
            best_len = len(rp.parts)
            best_rel = rel.as_posix()
    if best_rel:
        return best_rel
    return fp.name


def _extract_md_heading(text):
    m = re.search(r"(?m)^(#{1,6})\s+(.+)$", text)
    if not m:
        return ""
    return m.group(2).strip()


DEFAULT_LOCAL_EMBED_MODEL = "Qwen/Qwen3-Embedding-0.6B"


class KB:
    def __init__(self, db_path, table_name, embedder):
        import lancedb

        self._db_path = db_path
        self._table_name = table_name
        self._embedder = embedder
        self._lock = threading.Lock()
        self._db = lancedb.connect(db_path)

    def _get_table(self):
        try:
            return self._db.open_table(self._table_name)
        except Exception:
            return None

    def _ensure_table(self, sample_rows):
        tbl = self._get_table()
        if tbl is not None:
            return tbl
        return self._db.create_table(self._table_name, sample_rows, mode="overwrite")

    def index(self, roots, exts, chunk_size, overlap, max_file_bytes, timeout_s):
        exts = [e.lower().lstrip(".") for e in (exts or []) if e]
        if not exts:
            exts = ["md", "txt", "go", "rs", "py", "js", "ts", "tsx", "java", "cpp", "h", "hpp", "c", "yaml", "yml", "json", "toml", "typ"]
        docs = []
        now_ms = int(time.time() * 1000)
        for fp in _iter_files(roots):
            if fp.is_dir():
                continue
            ext = fp.suffix.lower().lstrip(".")
            if ext not in exts:
                continue
            txt = _load_text_file(fp, max_file_bytes)
            if not txt:
                continue
            heading = _extract_md_heading(txt) if ext == "md" else ""
            for start, end, chunk in _chunk_text(txt, chunk_size, overlap):
                rel = _stable_doc_path(fp, roots)
                docs.append(
                    {
                        "id": f"{rel}:{start}:{end}",
                        "path": rel,
                        "heading": heading,
                        "start": int(start),
                        "end": int(end),
                        "text": chunk,
                        "updated_at": now_ms,
                    }
                )
        if not docs:
            return {"indexed": 0, "db_path": self._db_path, "table": self._table_name}
        vectors = []
        batch = 64
        for i in range(0, len(docs), batch):
            texts = [d["text"] for d in docs[i : i + batch]]
            vecs = self._embedder.embed(texts, timeout_s)
            vectors.extend(vecs)
        rows = []
        for d, v in zip(docs, vectors):
            rows.append(
                {
                    "id": d["id"],
                    "path": d["path"],
                    "heading": d["heading"],
                    "start": d["start"],
                    "end": d["end"],
                    "text": d["text"],
                    "vector": v,
                    "updated_at": d["updated_at"],
                }
            )
        with self._lock:
            tbl = self._ensure_table(rows[:1])
            try:
                tbl.add(rows)
            except Exception:
                self._db.drop_table(self._table_name)
                tbl = self._ensure_table(rows)
                tbl.add(rows)
        return {"indexed": len(rows), "db_path": self._db_path, "table": self._table_name}

    def _collect_rows(self, searcher, top_k, where):
        if searcher is None:
            raise RuntimeError("searcher is required")
        if where:
            try:
                searcher = searcher.where(where)
            except Exception:
                pass
        try:
            arrow = searcher.limit(int(top_k)).to_arrow()
            return arrow.to_pylist()
        except Exception as first_err:
            try:
                df = searcher.limit(int(top_k)).to_pandas()
                return df.to_dict(orient="records")
            except Exception:
                raise first_err

    def search(self, query, top_k, query_type, where, timeout_s):
        query = (query or "").strip()
        if not query:
            return {"hits": []}
        with self._lock:
            tbl = self._get_table()
            if tbl is None:
                return {"hits": []}
        qtype = (query_type or "").strip().lower()
        if qtype not in {"auto", "hybrid", "vector", "text"}:
            qtype = "auto"

        attempts = []
        if qtype in {"auto", "hybrid"}:
            attempts.append(lambda: tbl.search(query, query_type="hybrid"))
        if qtype in {"auto", "vector"}:
            attempts.append(lambda: tbl.search(self._embedder.embed([query], timeout_s)[0]))
        if qtype in {"auto", "text"}:
            attempts.append(lambda: tbl.search(query))

        rows = []
        for make_searcher in attempts:
            try:
                rows = self._collect_rows(make_searcher(), top_k, where)
            except Exception:
                rows = []
                continue
            if rows:
                break

        hits = []
        for r in rows:
            hits.append(
                {
                    "id": r.get("id", ""),
                    "path": r.get("path", ""),
                    "heading": r.get("heading", ""),
                    "start": r.get("start", 0),
                    "end": r.get("end", 0),
                    "text": r.get("text", ""),
                    "score": r.get("_score", r.get("_distance", None)),
                }
            )
        return {"hits": hits}


class Handler(BaseHTTPRequestHandler):
    kb = None
    timeout_s = 30

    def _send(self, code, payload):
        b = _json_dumps(payload)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/index":
            body = _read_json(self)
            roots = body.get("roots") or []
            if not roots:
                roots = os.getenv("KB_DOC_ROOTS", ".").split(",")
            roots = [r.strip() for r in roots if r and r.strip()]
            exts = body.get("exts") or None
            chunk_size = int(body.get("chunk_size") or int(os.getenv("KB_CHUNK_SIZE", "1200")))
            overlap = int(body.get("overlap") or int(os.getenv("KB_CHUNK_OVERLAP", "200")))
            max_file_bytes = int(body.get("max_file_bytes") or int(os.getenv("KB_MAX_FILE_BYTES", str(512 * 1024))))
            try:
                out = self.kb.index(roots, exts, chunk_size, overlap, max_file_bytes, self.timeout_s)
                self._send(200, out)
            except urllib.error.HTTPError as e:
                try:
                    msg = e.read().decode("utf-8")
                except Exception:
                    msg = str(e)
                self._send(500, {"error": msg})
            except Exception as e:
                self._send(500, {"error": str(e)})
            return
        if self.path == "/search":
            body = _read_json(self)
            query = body.get("query") or ""
            top_k = int(body.get("top_k") or 8)
            query_type = body.get("query_type") or "auto"
            where = body.get("where") or ""
            try:
                out = self.kb.search(query, top_k, query_type, where, self.timeout_s)
                self._send(200, out)
            except urllib.error.HTTPError as e:
                try:
                    msg = e.read().decode("utf-8")
                except Exception:
                    msg = str(e)
                self._send(500, {"error": msg})
            except Exception as e:
                self._send(500, {"error": str(e)})
            return
        self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default=os.getenv("KB_LISTEN", "127.0.0.1:8788"))
    ap.add_argument("--db", default=os.getenv("KB_DB_PATH", ".agent-loop-artifacts/kb_lancedb"))
    ap.add_argument("--table", default=os.getenv("KB_TABLE", "chunks"))
    ap.add_argument("--embedding-provider", default=os.getenv("KB_EMBEDDING_PROVIDER", "openai"))
    ap.add_argument("--embedding-source", default=os.getenv("KB_EMBEDDING_SOURCE", "huggingface"))
    ap.add_argument("--cache-dir", default=os.getenv("KB_EMBEDDING_CACHE_DIR", ""))
    ap.add_argument("--normalize", default=os.getenv("KB_EMBEDDING_NORMALIZE", "true"))
    ap.add_argument("--openai-base-url", default=os.getenv("OPENAI_BASE_URL", "").rstrip("/"))
    ap.add_argument("--openai-api-key", default=os.getenv("OPENAI_API_KEY", ""))
    ap.add_argument("--embedding-model", default=os.getenv("OPENAI_EMBEDDING_MODEL", os.getenv("OPENAI_MODEL", "")))
    ap.add_argument("--local-model", default=(os.getenv("KB_LOCAL_EMBED_MODEL") or DEFAULT_LOCAL_EMBED_MODEL))
    args = ap.parse_args()

    provider = (args.embedding_provider or "").strip().lower()
    normalize = str(args.normalize).strip().lower() not in {"0", "false", "no", "off"}
    if provider == "openai":
        embedder = _EmbedderOpenAI(args.openai_base_url, args.openai_api_key, args.embedding_model, normalize)
        if not embedder.ready():
            raise SystemExit("OPENAI_BASE_URL and OPENAI_EMBEDDING_MODEL (or OPENAI_MODEL) are required for KB_EMBEDDING_PROVIDER=openai")
    elif provider == "local":
        embedder = _EmbedderLocal(args.local_model, args.embedding_source, args.cache_dir, normalize)
        if not embedder.ready():
            raise SystemExit(
                "KB_LOCAL_EMBED_MODEL is required for KB_EMBEDDING_PROVIDER=local "
                f"(recommended: {DEFAULT_LOCAL_EMBED_MODEL})"
            )
    else:
        raise SystemExit("KB_EMBEDDING_PROVIDER must be openai or local")

    Handler.kb = KB(args.db, args.table, embedder)
    host, port_s = args.listen.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port_s)), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
