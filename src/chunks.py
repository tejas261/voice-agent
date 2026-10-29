"""Small sentence-aware retrieval windows; original source text stays unchanged."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextWindow:
    text: str
    start: int
    end: int


def split_windows(text: str, size: int = 160, overlap: int = 24) -> list[TextWindow]:
    """Split on word boundaries, preferring sentence ends and bounded overlap.

    Short clauses stay intact. Offsets refer to the original string so repeated
    windows can be traced to one source instead of becoming duplicate citations.
    """
    if size < 8 or not 0 <= overlap < size // 2:
        raise ValueError("Chunk overlap must be smaller than half the chunk size")
    words = list(re.finditer(r"\S+", text))
    windows = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        if end < len(words):
            boundary = next(
                (
                    i + 1
                    for i in range(end - 1, start + size // 2 - 1, -1)
                    if re.search(r"[.!?।][\"')\]]*$", words[i].group())
                ),
                end,
            )
            end = boundary
        left, right = words[start].start(), words[end - 1].end()
        windows.append(TextWindow(text[left:right], left, right))
        if end == len(words):
            break
        start = max(start + 1, end - overlap)
    return windows
