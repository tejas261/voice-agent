"""Embedding cache checks use a tiny deterministic encoder, never a download."""

import json

import pytest

from src.embeddings import SemanticIndex
from src.rag_engine import KnowledgeBase


def test_cached_passages_are_reused_and_deleted_content_is_removed(settings, tmp_path):
    np = pytest.importorskip("numpy")

    class Encoder:
        def __init__(self):
            self.np = np
            self.calls = []

        def encode(self, texts):
            self.calls.extend(texts)
            return np.tile(np.array([1, 0, 0], dtype=np.float32), (len(texts), 1))

    encoder = Encoder()
    source = tmp_path / "faqs.json"
    data = json.loads(settings.kb_path.read_text())
    source.write_text(json.dumps(data))
    first = KnowledgeBase(source, settings.index_path)
    SemanticIndex(first, encoder)
    assert encoder.calls
    encoder.calls.clear()
    SemanticIndex(KnowledgeBase(source, settings.index_path), encoder)
    assert encoder.calls == []
    data[0]["answer_en"] = "Updated return conditions."
    removed = data.pop()
    source.write_text(json.dumps(data))
    changed = KnowledgeBase(source, settings.index_path)
    index = SemanticIndex(changed, encoder)
    assert len(encoder.calls) == 1
    assert data[0]["title"] in encoder.calls[0]
    assert encoder.calls[0].endswith("Updated return conditions.")
    assert removed["id"] not in index.owners
    with changed.connect() as db:
        assert db.execute("SELECT count(*) FROM embeddings").fetchone()[0] <= len(index.owners)
