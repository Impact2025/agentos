"""Token-optimalisatielaag (Headroom-style) voor gratis AI modellen."""
import re
from typing import List, Dict

_CHUNK_TOKEN_ESTIMATE = 4

def _token_efficiency_score(text: str) -> int:
    words = len(text.split())
    return words * _CHUNK_TOKEN_ESTIMATE

def strip_context_noise(context: str) -> str:
    lines = context.split(chr(10))  # newline
    cleaned = []
    prev_was_empty = False
    for line in lines:
        line = line.rstrip()
        if not line:
            if prev_was_empty:
                continue
            prev_was_empty = True
        else:
            prev_was_empty = False
        cleaned.append(line)
    return chr(10).join(cleaned)

def truncate_to_token_budget(text: str, max_tokens: int, preserve_frontmatter: bool = True) -> str:
    if _token_efficiency_score(text) <= max_tokens:
        return text
    frontmatter = ""
    body = text
    if preserve_frontmatter and text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = "---" + parts[1] + "---\n"
            body = parts[2].strip()
    words = body.split()
    max_words = max_tokens // _CHUNK_TOKEN_ESTIMATE
    if len(words) > max_words:
        truncated = " ".join(words[:max_words])
        body = truncated
    return frontmatter + body if frontmatter else body

def deduplicate_context(notes: dict) -> dict:
    sorted_notes = sorted(notes.items(), key=lambda x: len(x[1]))
    result = {}
    for title, content in sorted_notes:
        is_dup = False
        for _, existing in result.items():
            if content in existing or existing in content:
                is_dup = True
                break
        if not is_dup:
            result[title] = content
    return result

def optimize_prompt_messages(messages: list) -> list:
    return [{"role": m.get("role"), "content": strip_context_noise(m.get("content", ""))} for m in messages]

def estimate_savings(original: str, optimized: str) -> float:
    orig = _token_efficiency_score(original)
    opt = _token_efficiency_score(optimized)
    return round((orig - opt) / orig * 100, 1) if orig else 0.0
