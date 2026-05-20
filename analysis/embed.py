"""
Phase 3.5 — Embedding 封裝層。

支援 provider：
    llamacpp  — 本機 llama-server --embedding（OpenAI-compatible API）
    openai    — OpenAI text-embedding-* API
    google    — Google gemini-embedding-001 API

預設使用 llamacpp（本機 bge-m3-Q8_0，1024-dim）。

使用前需先啟動 llama-server：
    ~/llama.cpp/build/bin/llama-server \\
        -m ~/llama.cpp/models/bge-m3-Q8_0.gguf \\
        --embedding --port 8081 --ctx-size 8192

公開函數：
    embed_text(text)           — 單筆文字 → list[float]
    embed_batch(texts)         — 批次 → list[list[float]]
    server_health()            — 確認 embedding server 可用
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    LLAMACPP_BASE_URL,
)

logger = logging.getLogger(__name__)

# ── llamacpp ──────────────────────────────────────────────────────────────────


def _embed_llamacpp(texts: list[str]) -> list[list[float]]:
    """
    呼叫本機 llama-server /v1/embeddings（OpenAI-compatible）。

    llama-server 對 batch 的支援視版本而定；此處逐筆呼叫確保相容性。
    """
    url = f"{LLAMACPP_BASE_URL.rstrip('/')}/embeddings"
    results = []
    for text in texts:
        resp = requests.post(
            url,
            json={"model": EMBEDDING_MODEL, "input": text},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        vec = data["data"][0]["embedding"]
        if len(vec) != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding dim mismatch: got {len(vec)}, expected {EMBEDDING_DIM}. "
                f"Check EMBEDDING_DIM in .env (currently {EMBEDDING_DIM})."
            )
        results.append(vec)
    return results


# ── openai ────────────────────────────────────────────────────────────────────


def _embed_openai(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI
    from config.settings import OPENAI_API_KEY

    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in resp.data]


# ── google ────────────────────────────────────────────────────────────────────


def _embed_google(texts: list[str]) -> list[list[float]]:
    from google import genai
    from config.settings import GOOGLE_API_KEY

    client = genai.Client(api_key=GOOGLE_API_KEY)
    results = []
    for text in texts:
        resp = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config={"output_dimensionality": EMBEDDING_DIM},
        )
        results.append(resp.embeddings[0].values)
    return results


# ── 公開 API ──────────────────────────────────────────────────────────────────

_PROVIDERS = {
    "llamacpp": _embed_llamacpp,
    "openai": _embed_openai,
    "google": _embed_google,
}


def embed_batch(
    texts: list[str],
    *,
    provider: Optional[str] = None,
) -> list[list[float]]:
    """
    批次 embedding。

    Args:
        texts:    要嵌入的文字列表
        provider: 覆蓋 settings 的 EMBEDDING_PROVIDER（測試用）

    Returns:
        list of float vectors，每個長度為 EMBEDDING_DIM
    """
    p = provider or EMBEDDING_PROVIDER
    fn = _PROVIDERS.get(p)
    if fn is None:
        raise ValueError(f"Unknown embedding provider: {p!r}. Choose from {list(_PROVIDERS)}")
    return fn(texts)


def embed_text(
    text: str,
    *,
    provider: Optional[str] = None,
) -> list[float]:
    """單筆 embedding，回傳 list[float]。"""
    return embed_batch([text], provider=provider)[0]


def server_health(base_url: Optional[str] = None) -> dict:
    """
    確認 llama-server 是否在線。

    Returns:
        {"ok": bool, "url": str, "error": str | None}
    """
    url = (base_url or LLAMACPP_BASE_URL).rstrip("/")
    try:
        resp = requests.get(f"{url}/health", timeout=3)
        return {"ok": resp.status_code == 200, "url": url, "error": None}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


if __name__ == "__main__":
    print("[embed] Checking server health...")
    h = server_health()
    print(f"  {h}")

    if h["ok"]:
        texts = [
            "PTPRC spatial expression in CRC tumor microenvironment",
            "CD8A T cell infiltration analysis",
            "結直腸癌空間轉錄體分析",
        ]
        print(f"\n[embed] Embedding {len(texts)} texts...")
        vecs = embed_batch(texts)
        for t, v in zip(texts, vecs):
            print(f"  {t[:40]!r:42s} → dim={len(v)}, norm≈{sum(x**2 for x in v)**0.5:.3f}")
    else:
        print("\n[embed] Server not available. Start with:")
        print("  ~/llama.cpp/build/bin/llama-server \\")
        print("    -m ~/llama.cpp/models/bge-m3-Q8_0.gguf \\")
        print("    --embedding --port 8081 --ctx-size 8192")
