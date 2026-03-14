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

    resolved_roots = []
    for root in roots or []:
        try:
            resolved_roots.append(pathlib.Path(root).resolve())
        except Exception:
            continue
    if len(resolved_roots) > 1:
        try:
            common_base = pathlib.Path(os.path.commonpath([str(rp) for rp in resolved_roots]))
            return fp.relative_to(common_base).as_posix()
        except Exception:
            pass
    elif len(resolved_roots) == 1:
        stable_base = _detect_project_base(resolved_roots[0])
        if stable_base is not None:
            try:
                return fp.relative_to(stable_base).as_posix()
            except Exception:
                pass

    best_rel = None
    best_len = -1
    best_root = None
    for rp in resolved_roots:
        try:
            rel = fp.relative_to(rp)
        except Exception:
            continue
        if len(rp.parts) > best_len:
            best_len = len(rp.parts)
            best_root = rp
            best_rel = rel.as_posix()
    if best_rel:
        if best_root is not None:
            return f"{best_root.name}/{best_rel}"
        return best_rel
    return fp.name


def _detect_project_base(path: pathlib.Path):
    markers = ("go.mod", "pyproject.toml", "package.json", "Cargo.toml", ".git")
    current = path.resolve()
    for candidate in (current, *current.parents):
        try:
            if any((candidate / marker).exists() for marker in markers):
                return candidate
        except Exception:
            continue
    return None


def _extract_md_heading(text):
    m = re.search(r"(?m)^(#{1,6})\s+(.+)$", text)
    if not m:
        return ""
    return m.group(2).strip()


DEFAULT_LOCAL_EMBED_MODEL = "Qwen/Qwen3-Embedding-0.6B"


class KBRebuildRequired(ValueError):
    pass


class KBRebuildInProgress(ValueError):
    pass


def _is_rebuild_required_error(err):
    text = str(err or "").strip().lower()
    if not text:
        return False
    needles = (
        "schema",
        "mismatch",
        "incompatible",
        "column",
        "field",
        "type",
        "vector",
    )
    return any(needle in text for needle in needles)


def _flag_is_set(flag):
    if flag is None:
        return False
    if hasattr(flag, "is_set"):
        return bool(flag.is_set())
    return bool(flag)


class KB:
    def __init__(self, db_path, table_name, embedder):
        import lancedb

        self._db_path = db_path
        self._table_name = table_name
        self._embedder = embedder
        self._lock = threading.Lock()
        self._swap_lock = threading.Lock()
        self._rebuild_state_lock = threading.Lock()
        self._rebuild_in_progress = threading.Event()
        self._db = lancedb.connect(db_path)

    def _ensure_runtime_state(self):
        if not hasattr(self, "_lock") or self._lock is None:
            self._lock = threading.Lock()
        if not hasattr(self, "_swap_lock") or self._swap_lock is None:
            self._swap_lock = threading.Lock()
        if not hasattr(self, "_rebuild_state_lock") or self._rebuild_state_lock is None:
            self._rebuild_state_lock = threading.Lock()
        if not hasattr(self, "_rebuild_in_progress") or self._rebuild_in_progress is None:
            self._rebuild_in_progress = threading.Event()

    def _formal_table_name(self):
        return self._table_name

    def _temp_table_name(self):
        return f"{self._table_name}__rebuild_tmp"

    def _backup_table_name(self):
        return f"{self._table_name}__backup"

    def _stale_backup_table_name(self):
        return f"{self._table_name}__backup_stale"

    def _get_table_named(self, name):
        try:
            return self._db.open_table(name)
        except Exception:
            return None

    def _get_table(self):
        return self._get_table_named(self._formal_table_name())

    def _ensure_table(self, sample_rows, table_name=None):
        tbl = self._get_table_named(table_name or self._formal_table_name())
        if tbl is not None:
            return tbl
        return self._db.create_table(table_name or self._formal_table_name(), sample_rows, mode="overwrite")

    def _drop_table_if_exists(self, name):
        if self._get_table_named(name) is None:
            return False
        try:
            self._db.drop_table(name, ignore_missing=True)
        except TypeError:
            self._db.drop_table(name)
        return True

    def _create_table_overwrite(self, name, rows):
        return self._db.create_table(name, rows, mode="overwrite")

    def _rename_table(self, cur_name, new_name):
        self._db.rename_table(cur_name, new_name)

    def _begin_rebuild(self):
        self._ensure_runtime_state()
        with self._rebuild_state_lock:
            if _flag_is_set(self._rebuild_in_progress):
                raise KBRebuildInProgress("rebuild already in progress")
            self._rebuild_in_progress.set()

    def _end_rebuild(self):
        self._ensure_runtime_state()
        with self._rebuild_state_lock:
            self._rebuild_in_progress.clear()

    def _prepare_rows(self, roots, exts, chunk_size, overlap, max_file_bytes, timeout_s):
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
            return []
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
        return rows

    def index(self, roots, exts, chunk_size, overlap, max_file_bytes, timeout_s):
        self._ensure_runtime_state()
        if _flag_is_set(getattr(self, "_rebuild_in_progress", None)):
            raise KBRebuildInProgress("rebuild already in progress")
        rows = self._prepare_rows(roots, exts, chunk_size, overlap, max_file_bytes, timeout_s)
        if not rows:
            return {"indexed": 0, "db_path": self._db_path, "table": self._table_name}
        with self._lock:
            tbl = self._ensure_table(rows[:1])
            try:
                tbl.add(rows)
            except Exception as e:
                if _is_rebuild_required_error(e):
                    raise KBRebuildRequired(str(e)) from e
                raise
        return {"indexed": len(rows), "db_path": self._db_path, "table": self._table_name}

    def rebuild(self, roots, exts, chunk_size, overlap, max_file_bytes, timeout_s):
        self._ensure_runtime_state()
        roots = [str(r).strip() for r in (roots or []) if str(r).strip()]
        if not roots:
            raise ValueError("rebuild requires explicit non-empty roots")

        self._begin_rebuild()
        formal_name = self._formal_table_name()
        temp_name = self._temp_table_name()
        backup_name = self._backup_table_name()
        stale_backup_name = self._stale_backup_table_name()
        try:
            with self._swap_lock:
                if self._drop_table_if_exists(temp_name) is False and self._get_table_named(temp_name) is not None:
                    raise RuntimeError(f"failed to clear leftover temp table {temp_name}")

            rows = self._prepare_rows(roots, exts, chunk_size, overlap, max_file_bytes, timeout_s)
            if not rows:
                raise ValueError("rebuild produced no rows")

            self._create_table_overwrite(temp_name, rows)

            with self._swap_lock:
                stale_backup_present = self._get_table_named(backup_name) is not None
                if stale_backup_present:
                    self._drop_table_if_exists(stale_backup_name)
                    self._rename_table(backup_name, stale_backup_name)

                promoted_backup = False
                try:
                    if self._get_table_named(formal_name) is not None:
                        self._rename_table(formal_name, backup_name)
                        promoted_backup = True
                    self._rename_table(temp_name, formal_name)
                except Exception:
                    if promoted_backup and self._get_table_named(backup_name) is not None:
                        self._rename_table(backup_name, formal_name)
                    if stale_backup_present and self._get_table_named(stale_backup_name) is not None:
                        self._rename_table(stale_backup_name, backup_name)
                    raise

                self._drop_table_if_exists(stale_backup_name)

            return {
                "rebuilt": True,
                "indexed": len(rows),
                "db_path": self._db_path,
                "table": formal_name,
                "backup_table": backup_name,
                "roots": list(roots),
            }
        finally:
            self._end_rebuild()

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
        self._ensure_runtime_state()
        query = (query or "").strip()
        if not query:
            return {"hits": []}
        with self._swap_lock:
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

    def _send_conflict(self, code, message):
        self._send(409, {"code": code, "error": message})

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
            except KBRebuildInProgress as e:
                self._send_conflict("rebuild_in_progress", str(e))
            except KBRebuildRequired as e:
                self._send_conflict("rebuild_required", str(e))
            except urllib.error.HTTPError as e:
                try:
                    msg = e.read().decode("utf-8")
                except Exception:
                    msg = str(e)
                self._send(500, {"error": msg})
            except Exception as e:
                self._send(500, {"error": str(e)})
            return
        if self.path == "/rebuild":
            body = _read_json(self)
            roots = body.get("roots") or []
            roots = [r.strip() for r in roots if r and r.strip()]
            if not roots:
                self._send(400, {"error": "rebuild requires explicit non-empty roots"})
                return
            exts = body.get("exts") or None
            chunk_size = int(body.get("chunk_size") or int(os.getenv("KB_CHUNK_SIZE", "1200")))
            overlap = int(body.get("overlap") or int(os.getenv("KB_CHUNK_OVERLAP", "200")))
            max_file_bytes = int(body.get("max_file_bytes") or int(os.getenv("KB_MAX_FILE_BYTES", str(512 * 1024))))
            try:
                out = self.kb.rebuild(roots, exts, chunk_size, overlap, max_file_bytes, self.timeout_s)
                self._send(200, out)
            except KBRebuildInProgress as e:
                self._send_conflict("rebuild_in_progress", str(e))
            except KBRebuildRequired as e:
                self._send_conflict("rebuild_required", str(e))
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
