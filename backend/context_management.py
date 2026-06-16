from dataclasses import dataclass
from typing import Iterable, Optional


CHARS_PER_TOKEN = 4
OMISSION_TEXT = "\n...(omitted)...\n"


@dataclass
class ContextBlock:
    name: str
    content: str
    token_budget: Optional[int] = None
    preserve: str = "middle"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def tokens_to_chars(token_budget: int) -> int:
    return max(0, token_budget * CHARS_PER_TOKEN)


def truncate_text(text: str, token_budget: int, preserve: str = "middle") -> str:
    if not text or token_budget <= 0:
        return ""

    max_chars = tokens_to_chars(token_budget)
    if len(text) <= max_chars:
        return text

    if max_chars <= len(OMISSION_TEXT) + 20:
        return text[:max_chars].rstrip()

    available = max_chars - len(OMISSION_TEXT)

    if preserve == "head":
        return text[:available].rstrip() + OMISSION_TEXT.strip()

    if preserve == "tail":
        return OMISSION_TEXT.strip() + text[-available:].lstrip()

    head_chars = available // 2
    tail_chars = available - head_chars
    return text[:head_chars].rstrip() + OMISSION_TEXT + text[-tail_chars:].lstrip()


def assemble_context(blocks: Iterable[ContextBlock], token_budget: int, separator: str = "\n\n") -> str:
    selected = []
    used_tokens = 0
    separator_tokens = estimate_tokens(separator)

    for block in blocks:
        if not block.content:
            continue

        remaining = token_budget - used_tokens
        if selected:
            remaining -= separator_tokens
        if remaining <= 0:
            break

        block_budget = min(block.token_budget or remaining, remaining)
        content = truncate_text(block.content, block_budget, block.preserve)
        if not content:
            continue

        used_tokens += estimate_tokens(content)
        if selected:
            used_tokens += separator_tokens
        selected.append(content)

    return separator.join(selected)


def trim_json_list_payload(payload: dict, list_key: str, token_budget: int) -> dict:
    import json

    items = payload.get(list_key)
    if not isinstance(items, list):
        return payload

    while items and estimate_tokens(json.dumps(payload, ensure_ascii=False)) > token_budget:
        items.pop(0)

    return payload
