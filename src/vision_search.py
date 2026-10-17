"""Image description (7.7) and semantic code search (7.8) helpers."""

from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
from typing import Any

import httpx

from src.config import EVREN_BASE_URL, get_ssl_verify

VISION_MODEL = "qwen3-vl-30b"
EMBED_MODEL = "qwen3-embedding-8b"
RERANK_MODEL = "qwen3-reranker-8b"
CHUNK_LINES = 60
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_SKIP_DIRS = {".git", ".evren", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea"}
_TEXT_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".cs", ".rb", ".php",
    ".sh", ".ps1", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".sql",
}


def semantic_search_enabled() -> bool:
    return os.environ.get("EVREN_SEMANTIC_SEARCH", "true").strip().lower() in ("1", "true", "yes", "on")


def _auth() -> dict[str, str]:
    from src.api_key_pool import ApiKeyPool

    pooled = ApiKeyPool(base_url=EVREN_BASE_URL.rstrip("/")).acquire()
    if pooled is None:
        raise RuntimeError("Kullanılabilir API anahtarı yok.")
    return {"Authorization": f"Bearer {pooled.key}", "X-API-Key": pooled.key, "Content-Type": "application/json"}


def _post(path: str, payload: dict, timeout: float = 120.0) -> dict:
    url = EVREN_BASE_URL.rstrip("/") + path
    r = httpx.post(url, json=payload, headers=_auth(), timeout=timeout, verify=get_ssl_verify())
    r.raise_for_status()
    return r.json()


def describe_image(path: Path, display: str) -> str:
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        return f"HATA: Görsel çok büyük ({size // 1024} KB, sınır {MAX_IMAGE_BYTES // 1024} KB)."
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}[path.suffix.lower()]
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    prompt = ("Bu görseli Türkçe betimle. Görselde metin (hata mesajı, kod, log, arayüz yazısı) varsa "
              "aynen, eksiksiz çıkar. Önce kısa betimleme, sonra 'Görünen metin:' başlığı altında metinler.")
    try:
        data = _post("/chat/completions", {
            "model": VISION_MODEL, "max_tokens": 1500, "temperature": 0.1,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
        })
        text = (data["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        return f"HATA: Görsel betimlenemedi ({type(e).__name__}): {e}"
    return f"--- {display} (görsel, {VISION_MODEL} betimlemesi) ---\n{text or '(boş yanıt)'}"


# ---- semantic search -------------------------------------------------------

def _iter_files(root: Path):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in _SKIP_DIRS]
        for fn in fns:
            p = Path(dp) / fn
            if p.suffix.lower() in _TEXT_EXTS:
                try:
                    if p.stat().st_size <= 300_000:
                        yield p
                except OSError:
                    pass


def _embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), 32):
        data = _post("/embeddings", {"model": EMBED_MODEL, "input": texts[i:i + 32]})
        out.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d.get("index", 0)))
    return out


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# Synchronous embedding above this many chunks blocks the step for a minute
# (a first search over this repo took ~70 s); larger jobs go to a thread.
_SYNC_CHUNK_LIMIT = 40
_BUILDING: dict[str, Any] = {}


def _pending_chunks(root: Path) -> int:
    try:
        files = json.loads((root / ".evren" / "index" / "index.json").read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError):
        files = {}
    n = 0
    for p in _iter_files(root):
        rel = p.relative_to(root).as_posix()
        try:
            if rel in files and files[rel].get("mtime") == p.stat().st_mtime:
                continue
            n += max(1, sum(1 for _ in p.open(encoding="utf-8", errors="replace")) // CHUNK_LINES + 1)
        except OSError:
            continue
        if n > _SYNC_CHUNK_LIMIT:
            break
    return n


def _start_background_index(root: Path) -> bool:
    """Builds the index in a daemon thread; True while a build is running."""
    import threading

    key = str(root.resolve())
    th = _BUILDING.get(key)
    if th is not None and th.is_alive():
        return True

    def work():
        try:
            _update_index(root)
        except Exception:
            pass

    th = threading.Thread(target=work, daemon=True, name="evren-index")
    _BUILDING[key] = th
    th.start()
    return True


def _update_index(root: Path) -> dict[str, Any]:
    idx_dir = root / ".evren" / "index"
    idx_dir.mkdir(parents=True, exist_ok=True)
    idx_file = idx_dir / "index.json"
    index: dict[str, Any] = {"files": {}}
    try:
        index = json.loads(idx_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    files = index.setdefault("files", {})
    seen = set()
    todo: list[tuple[str, float, list[dict]]] = []
    for p in _iter_files(root):
        rel = p.relative_to(root).as_posix()
        seen.add(rel)
        mtime = p.stat().st_mtime
        if rel in files and files[rel].get("mtime") == mtime:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        chunks = []
        for s in range(0, len(lines), CHUNK_LINES):
            body = "\n".join(lines[s:s + CHUNK_LINES])
            if body.strip():
                chunks.append({"start": s + 1, "end": min(len(lines), s + CHUNK_LINES), "text": body})
        todo.append((rel, mtime, chunks))
    removed = [rel for rel in files if rel not in seen]
    for rel in removed:
        del files[rel]
    flat = [(rel, c) for rel, _m, cs in todo for c in cs]
    vecs = _embed([f"{rel}\n{c['text']}"[:6000] for rel, c in flat]) if flat else []
    for rel, mtime, _cs in todo:
        files[rel] = {"mtime": mtime, "chunks": []}
    for (rel, c), v in zip(flat, vecs):
        files[rel]["chunks"].append({"start": c["start"], "end": c["end"], "text": c["text"][:1500], "vec": v})
    if todo or removed:
        idx_file.write_text(json.dumps(index), encoding="utf-8")
    return index


def search_code(query: str, root: Path) -> str:
    if not semantic_search_enabled():
        return "HATA: Anlamsal arama kapalı (EVREN_SEMANTIC_SEARCH=true ile açılır)."
    if not query.strip():
        return "HATA: 'query' boş."
    try:
        key = str(root.resolve())
        building = _BUILDING.get(key)
        if (building is not None and building.is_alive()) or _pending_chunks(root) > _SYNC_CHUNK_LIMIT:
            _start_background_index(root)
            return (
                "BİLGİ: Anlamsal indeks arka planda hazırlanıyor (ilk kullanım). Bu adımda "
                "find_files(query=\"a|b\") kullan; birkaç dakika sonra search_code tekrar dene."
            )
        index = _update_index(root)
        qv = _embed([query])[0]
        scored = []
        for rel, f in index["files"].items():
            for c in f["chunks"]:
                scored.append((_cos(qv, c["vec"]), rel, c))
        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[:20]
        if not top:
            return "Sonuç yok (indekslenecek metin dosyası bulunamadı)."
        order = list(range(len(top)))
        try:
            rr = _post("/rerank", {"model": RERANK_MODEL, "query": query,
                                   "documents": [f"{r}\n{c['text']}" for _s, r, c in top], "top_n": 8}, timeout=60.0)
            res = rr.get("results") or rr.get("data") or []
            ranked = [x["index"] for x in sorted(res, key=lambda x: x.get("relevance_score", x.get("score", 0)), reverse=True)]
            if ranked:
                order = ranked
        except Exception:
            pass  # fall back to cosine order
        out = []
        for i in order[:8]:
            _sc, rel, c = top[i]
            snippet = "\n".join(c["text"].splitlines()[:6])
            out.append(f"{rel}:{c['start']}-{c['end']}\n{snippet}")
        return "En ilgili parçalar (view_file ile ayrıntıya bak):\n\n" + "\n\n".join(out)
    except Exception as e:
        return f"HATA: Anlamsal arama başarısız ({type(e).__name__}): {e}"
