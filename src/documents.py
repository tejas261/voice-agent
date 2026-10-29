"""Read policy clauses from their source, with optional Hindi/alias annotations."""

import hashlib
import json
import re
from pathlib import Path

from pydantic import TypeAdapter

from src.models import FAQ


def retrieval_passage(entry: FAQ, language: str = "en") -> str:
    """Keep a field's meaning with its value; never change the cited source."""
    title = (
        entry.title.removeprefix(f"{entry.source_file} · ") if entry.source_file else entry.title
    )
    section = re.match(r"SECTION: (.+)\n", entry.source_text or "")
    heading = title
    if section and section[1] != "Policy":
        heading += " · " + section[1].replace("_", " ").title()
    answer = entry.answer_hi if language == "hi" and entry.answer_hi else entry.answer_en
    body = f"{heading}: {answer}"
    return f"{entry.document_context}\n{body}" if entry.document_context else body


def load_documents(path: Path) -> tuple[list[FAQ], bytes]:
    raw = path.read_bytes()
    if path.suffix.lower() == ".json":
        return TypeAdapter(list[FAQ]).validate_json(raw), raw
    if path.suffix.lower() not in {".txt", ".md"}:
        raise ValueError("Knowledge base must be JSON, TXT or Markdown")

    annotations_path = path.with_suffix(".answers.json")
    annotation_bytes = annotations_path.read_bytes() if annotations_path.exists() else b"{}"
    annotations = json.loads(annotation_bytes)
    documents = []
    section = "Policy"
    preamble = []
    section_started = False
    for line in raw.decode("utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]+):\s*(.+)$", line.strip())
        if not match:
            continue
        label, text = match.groups()
        if label == "SECTION":
            section = text
            section_started = True
            continue
        if not section_started:
            preamble.append(f"{label.replace('_', ' ').title()}: {text}")
        key = label.lower()
        annotation = annotations.get(key, {})
        # An updated source must never keep an outdated translation or paraphrase.
        current_hash = hashlib.sha256(line.strip().encode()).hexdigest()
        translated = (
            annotation.get("answer_hi") if annotation.get("source_sha256") == current_hash else None
        )
        documents.append(
            FAQ(
                id=key,
                title=f"{path.name} · {label.replace('_', ' ').title()}",
                questions=[label.replace("_", " ").lower(), *annotation.get("questions", [])],
                answer_en=re.sub(r"\s*\[[\d., ]+\]", "", text).strip(),
                answer_hi=translated,
                voice_answer_en=(
                    annotation.get("voice_answer_en")
                    if annotation.get("source_sha256") == current_hash
                    else None
                ),
                source_text=f"SECTION: {section}\n{line.strip()}",
                source_file=path.name,
                context_ids=annotation.get("context_ids", []),
            )
        )
    if not documents:
        raise ValueError("No policy clauses found; use LABEL: text lines or the FAQ JSON format")
    # Structured documents put their identity/type before the first section.
    # Carry that parent context into every search passage, keeping answers and
    # original source excerpts unchanged. Unsectioned FAQ files need no parent.
    context = "\n".join(preamble)[:2000] if section_started else None
    for document in documents:
        document.document_context = context
    originals = {document.id: document for document in documents}
    for key, annotation in annotations.items():
        source_ids = annotation.get("source_ids")
        if not source_ids:
            continue
        if key in originals or not all(source_id in originals for source_id in source_ids):
            raise ValueError(f"Invalid overview sources for {key}")
        sources = [originals[source_id] for source_id in dict.fromkeys(source_ids)]
        source_hashes = annotation.get("source_sha256_by_id", {})
        voice_current = set(source_hashes) == {source.id for source in sources} and all(
            source_hashes[source.id]
            == hashlib.sha256(source.source_text.splitlines()[-1].encode()).hexdigest()
            for source in sources
        )
        documents.append(
            FAQ(
                id=key,
                title=f"{path.name} · {annotation['title']}",
                questions=annotation["questions"],
                answer_en=" ".join(source.answer_en for source in sources),
                voice_answer_en=annotation.get("voice_answer_en") if voice_current else None,
                answer_hi=(
                    " ".join(source.answer_hi for source in sources)
                    if all(source.answer_hi for source in sources)
                    else None
                ),
                source_ids=[source.id for source in sources],
                source_file=path.name,
                document_context=context,
            )
        )
    return documents, raw + b"\0" + annotation_bytes
