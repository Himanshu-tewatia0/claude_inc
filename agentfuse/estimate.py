"""Worst-case token estimates. These are deliberately HIGH: over-estimating only reserves a little
extra headroom temporarily (it is released at settle time); under-estimating would let spend through."""
from __future__ import annotations

import json
import math
from typing import Any, Iterable, Optional, Tuple

IMAGE_TOKENS = 2000       # fixed allowance per image (replace with the provider's counter when available)
PER_MESSAGE_OVERHEAD = 8


def flatten(content: Any) -> Tuple[str, int]:
    """Return (text, image_count) for a str, a list of content blocks, a dict block, or an SDK object."""
    if content is None:
        return "", 0
    if hasattr(content, "model_dump"):
        content = content.model_dump()
    if isinstance(content, str):
        return content, 0
    if isinstance(content, (list, tuple)):
        parts, imgs = [], 0
        for b in content:
            t, i = flatten(b)
            if t:
                parts.append(t)
            imgs += i
        return "\n".join(parts), imgs
    if isinstance(content, dict):
        typ = content.get("type")
        if typ in ("image", "image_url", "input_image"):
            return "", 1
        if typ == "text":
            return str(content.get("text", "")), 0
        if typ == "tool_result":
            return flatten(content.get("content"))
        if typ == "tool_use":
            return str(content.get("name", "")) + " " + json.dumps(content.get("input", {}), ensure_ascii=False), 0
        return json.dumps(content, ensure_ascii=False, default=str), 0  # unknown block: count it all
    return str(content), 0


def message_text(m: Any) -> Tuple[str, int]:
    return flatten(m.get("content") if isinstance(m, dict) else getattr(m, "content", None))


def text_tokens_upper(text: str) -> int:
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other = len(text) - ascii_chars
    return int(math.ceil(ascii_chars / 3 + other * 2))


def estimate_input_tokens(messages: Iterable[Any], system: Any = None, tools: Optional[list] = None) -> int:
    total = 0
    if system:
        s, imgs = flatten(system)
        total += text_tokens_upper(s) + imgs * IMAGE_TOKENS
    for m in messages:
        t, imgs = message_text(m)
        total += text_tokens_upper(t) + imgs * IMAGE_TOKENS + PER_MESSAGE_OVERHEAD
    if tools:
        total += int(math.ceil(len(json.dumps(tools, default=str)) / 3))
    return total
