"""Token-aware text chunking.

PRD 5.4: chunk to fit a configurable token budget, overlap by 200 tokens
to avoid splitting entities across chunk boundaries. The detector then
deduplicates the per-chunk PII results, so overlap is safe.

`tiktoken` provides accurate counts for OpenAI/cl100k tokenizers; for local
LLMs the count is approximate but consistent enough to bound chunk size.
We fall back to a 4-chars-per-token heuristic if tiktoken is unavailable.
"""

from __future__ import annotations

from typing import Iterable

from .config import CHUNK_OVERLAP_TOKENS, CHUNK_TOKENS
from .logging_setup import get_logger

log = get_logger("chunker")

_ENCODER = None


def _encoder():
    global _ENCODER
    if _ENCODER is not None:
        return _ENCODER
    try:
        import tiktoken
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # tiktoken missing or model file unfetchable
        log.warning("tiktoken unavailable, falling back to char heuristic: %s", exc)
        _ENCODER = False
    return _ENCODER


def count_tokens(text: str) -> int:
    enc = _encoder()
    if enc is False:
        return max(1, len(text) // 4)
    return len(enc.encode(text))


def chunk_text(
    text: str,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """Return a list of text chunks, each <= `chunk_tokens` (with overlap)."""
    if not text:
        return []
    enc = _encoder()
    if enc is False:
        return _chunk_by_chars(text, chunk_tokens, overlap_tokens)

    ids = enc.encode(text)
    if len(ids) <= chunk_tokens:
        return [text]

    chunks: list[str] = []
    step = max(1, chunk_tokens - overlap_tokens)
    for i in range(0, len(ids), step):
        slice_ids = ids[i : i + chunk_tokens]
        chunks.append(enc.decode(slice_ids))
        if i + chunk_tokens >= len(ids):
            break
    return chunks


def _chunk_by_chars(text: str, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    # 1 token ~= 4 chars heuristic
    chunk_chars = chunk_tokens * 4
    overlap_chars = overlap_tokens * 4
    if len(text) <= chunk_chars:
        return [text]
    out: list[str] = []
    step = max(1, chunk_chars - overlap_chars)
    for i in range(0, len(text), step):
        out.append(text[i : i + chunk_chars])
        if i + chunk_chars >= len(text):
            break
    return out
