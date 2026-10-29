"""Prepare pronounceable sentences without manufacturing a pause at every comma."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechPart:
    text: str


def speech_parts(text: str, max_words: int = 48) -> list[SpeechPart]:
    if max_words < 1:
        raise ValueError("Speech sentence size must be positive")
    text = re.sub(
        r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{5,}\b",
        lambda match: " ".join(match[0]),
        text,
    )
    text = re.sub(r"(?<=\d)%", " percent", text)
    text = text.replace("/", " slash ")
    sentences, start, depth = [], 0, 0
    for index, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char not in ".!?।\n" or depth:
            continue
        before, after = text[index - 1 : index], text[index + 1 : index + 2]
        if char == ".":
            if before.isalnum() and after.isalnum():
                continue  # decimal values, email addresses and domains
            word = re.search(r"([\w.]+)$", text[start:index])
            if word and (
                word[1].lower() in {"mr", "mrs", "ms", "dr", "rs", "prof", "st", "no"}
                or len(word[1]) == 1
                or re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", word[1])
            ):
                continue
        sentences.append(text[start : index + 1].strip())
        start = index + 1
    sentences.append(text[start:].strip())

    # A standalone "Yes." or greeting should share its delivery with the next
    # sentence instead of sounding like a separate recording.
    joined = []
    for sentence in sentences:
        if not any(c.isalnum() for c in sentence):
            continue
        if (
            joined
            and len(joined[-1].split()) < 5
            and len((joined[-1] + " " + sentence).split()) <= max_words
        ):
            joined[-1] += " " + sentence
        else:
            joined.append(sentence)

    parts = []
    for sentence in joined:
        words = sentence.split()
        while words:
            cut = min(max_words, len(words))
            if cut < len(words):
                # Only unusually long sentences need another boundary. Prefer
                # existing punctuation or a conjunction outside parentheses.
                depth, punctuation, conjunctions = 0, [], []
                for index, word in enumerate(words[:cut]):
                    if index >= max(1, max_words // 2) and not depth:
                        if words[index - 1].endswith((",", ";", ":")):
                            punctuation.append(index)
                        elif word.lower() in {
                            "and",
                            "but",
                            "if",
                            "unless",
                            "when",
                            "because",
                            "while",
                            "for",
                            "which",
                        }:
                            conjunctions.append(index)
                    depth += word.count("(") + word.count("[") - word.count(")") - word.count("]")
                if punctuation or conjunctions:
                    cut = (punctuation or conjunctions)[-1]
            value = " ".join(words[:cut])
            words = words[cut:]
            if value[-1] not in ",;:.!?।\"'”’":
                value += "," if words else "."
            parts.append(SpeechPart(value))
    return parts
