"""Bounded adaptations of MIT Antiloop detect.ts; see THIRD_PARTY_NOTICES.md.

Units are Unicode words/characters, never claimed to be model tokenizer tokens.
"""

from __future__ import annotations

import re
from collections import Counter


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", re.sub(r"\\+[nrt]", " ", text.casefold()))


def repetition(text: str, config: dict) -> dict | None:
    text = text[-config.get("window_chars", 8192) :]
    units = words(text)
    if len(units) >= config.get("min_words", 16):
        counts = Counter(units)
        top, frequency = counts.most_common(1)[0]
        longest, run, previous = 0, 0, None
        for unit in units:
            run = run + 1 if unit == previous else 1
            longest = max(longest, run)
            previous = unit
        if longest >= config.get("min_run", 16) or (
            frequency >= config.get("min_frequency", 60)
            and frequency / len(units) >= config.get("min_share", 0.4)
        ):
            return {
                "method": "word_run_or_frequency",
                "repeats": max(longest, frequency),
                "units": len(units),
                "share": frequency / len(units),
            }
    # Bounded periodicity test, including single Thai/CJK character loops.
    compact = "".join(unit for unit in units if unit.isalpha())
    if len(units) <= 2 and len(compact) >= config.get("min_period_chars", 256):
        for width in range(1, min(128, len(compact) // 16) + 1):
            if len(compact) % width == 0 and compact == compact[:width] * (len(compact) // width):
                return {
                    "method": "character_period",
                    "repeats": len(compact) // width,
                    "units": len(compact),
                    "share": 1.0,
                }
    # Sentence-level replay excludes IP/version dots and semicolon-delimited data.
    sentences = [" ".join(words(part)) for part in re.split(r"[.!?。！？]+(?=\s|$)|\r?\n", text)]
    sentences = [part for part in sentences if part and any(c.isalpha() for c in part)]
    if len(sentences) >= 6 and sum(len(part.split()) for part in sentences) >= 8:
        counts = Counter(sentences)
        repeats = max(counts.values())
        if repeats >= 3 and len(counts) / len(sentences) <= 0.5:
            return {
                "method": "sentence_replay",
                "repeats": repeats * 16,
                "units": len(sentences),
                "share": 1 - len(counts) / len(sentences),
            }
    if len(units) >= config.get("block_min_words", 120):
        grams = [tuple(units[index : index + 5]) for index in range(len(units) - 4)]
        counts = Counter(grams)
        ratio = sum(counts[gram] >= 2 for gram in grams) / len(grams)
        if max(counts.values()) >= 3 and ratio >= 0.85:
            return {
                "method": "ngram_coverage",
                "repeats": max(counts.values()) * 16,
                "units": len(units),
                "share": ratio,
            }
    return None


def similarity(a: str, b: str) -> float:
    a, b = " ".join(words(a))[:4096], " ".join(words(b))[:4096]
    if a == b:
        return 1.0
    left = {a[i : i + 3] for i in range(max(0, len(a) - 2))}
    right = {b[i : i + 3] for i in range(max(0, len(b) - 2))}
    return len(left & right) / len(left | right) if left | right else 0.0
