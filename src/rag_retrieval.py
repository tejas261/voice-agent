"""High-recall retrieval for grounded generation, independent of FAQ acceptance gates."""

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass

from src.chunks import split_windows
from src.config import Settings
from src.embeddings import MODEL_REVISION
from src.models import FAQ
from src.query_scope import requires_personal_records
from src.rag_engine import KnowledgeBase, terms


@dataclass(frozen=True)
class Chunk:
    id: str
    owner: str
    language: str
    text: str


@dataclass
class Context:
    documents: list[FAQ]
    passages: list[dict]
    scope: str


class RAGRetriever:
    def __init__(self, kb: KnowledgeBase, settings: Settings):
        self.kb, self.settings = kb, settings
        self.chunks = []
        for entry in kb.entries:
            for language in ("en", "hi"):
                text = getattr(entry, f"answer_{language}")
                if not text:
                    continue
                for window in split_windows(
                    text, settings.chunk_size_words, settings.chunk_overlap_words
                ):
                    self.chunks.append(
                        Chunk(
                            f"{entry.id}:{language}:{window.start}", entry.id, language, window.text
                        )
                    )
        self.by_chunk = {chunk.id: chunk for chunk in self.chunks}
        self.owner_chunks = defaultdict(list)
        for i, chunk in enumerate(self.chunks):
            self.owner_chunks[chunk.owner].append(i)
        self.version = hashlib.sha256(
            f"rag-v1:{kb.version}:{settings.chunk_size_words}:{settings.chunk_overlap_words}".encode()
        ).hexdigest()
        if kb.semantic:
            self._index()

    def _passage(self, chunk: Chunk) -> str:
        entry = self.kb.by_id[chunk.owner]
        title = entry.title.removeprefix(f"{entry.source_file} · ")
        section = re.match(r"SECTION: ([^\n]+)", entry.source_text or "")
        heading = title + (f" ({section[1].replace('_', ' ')})" if section else "")
        return f"{heading}\n{chunk.text}"

    def _index(self):
        encoder = self.kb.semantic.embedder
        texts = ["passage: " + self._passage(c) for c in self.chunks]
        keys = [hashlib.sha256((MODEL_REVISION + text).encode()).hexdigest() for text in texts]
        with self.kb.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS rag_embeddings (key TEXT PRIMARY KEY, vector BLOB)"
            )
            cached = dict(db.execute("SELECT key, vector FROM rag_embeddings"))
            missing = {key: text for key, text in zip(keys, texts) if key not in cached}
            if missing:
                vectors = encoder.encode(list(missing.values()))
                new = {key: vector.tobytes() for key, vector in zip(missing, vectors)}
                db.executemany("INSERT OR REPLACE INTO rag_embeddings VALUES (?, ?)", new.items())
                cached.update(new)
            db.executemany(
                "DELETE FROM rag_embeddings WHERE key = ?",
                [(key,) for key in cached.keys() - set(keys)],
            )
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS rag_search USING fts5("
                "id UNINDEXED, text, questions, tokenize='unicode61 remove_diacritics 0')"
            )
            old = db.execute("SELECT value FROM metadata WHERE key = 'rag_version'").fetchone()
            if not old or old[0] != self.version:
                db.execute("DELETE FROM rag_search")
                db.executemany(
                    "INSERT INTO rag_search VALUES (?, ?, ?)",
                    [
                        (
                            c.id,
                            " ".join(terms(self._passage(c))),
                            " ".join(terms(" ".join(self.kb.by_id[c.owner].questions))),
                        )
                        for c in self.chunks
                    ],
                )
                db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('rag_version', ?)", (self.version,)
                )
        self.vectors = encoder.np.stack(
            [encoder.np.frombuffer(cached[key], dtype=encoder.np.float32) for key in keys]
        )

    def retrieve(self, question: str, language: str = "en") -> Context:
        if requires_personal_records(question) or not terms(question):
            return Context([], [], "")
        if not self.kb.semantic:
            return self._pack([h.faq.id for h in self.kb.search(question)], language)

        encoder = self.kb.semantic.embedder
        query = encoder.encode(["query: " + question])[0]
        scores = self.vectors @ query
        dense = {
            owner: max(float(scores[i]) for i in indices)
            for owner, indices in self.owner_chunks.items()
        }
        aliases, _ = self.kb.semantic.scores(question, query_vector=query)
        expression = " OR ".join('"' + word + '"' for word in terms(question)[:50])
        with self.kb.connect() as db:
            lexical = db.execute(
                "SELECT id FROM rag_search WHERE rag_search MATCH ? "
                "ORDER BY bm25(rag_search, 0, 3, 1) LIMIT ?",
                (expression, self.settings.retrieval_candidates * 4),
            ).fetchall()
        lexical_owners = list(dict.fromkeys(self.by_chunk[key].owner for (key,) in lexical))
        # RRF combines rank positions rather than incompatible BM25/cosine scales.
        fused = defaultdict(float)
        for weight, ranking in [
            (1.0, sorted(dense, key=dense.get, reverse=True)),
            (1.0, lexical_owners),
            (0.5, sorted(aliases, key=aliases.get, reverse=True)),
        ]:
            for rank, owner in enumerate(ranking[: self.settings.retrieval_candidates], 1):
                fused[owner] += weight / (60 + rank)
        candidates = sorted(fused, key=fused.get, reverse=True)[
            : self.settings.retrieval_candidates
        ]
        passages = []
        for owner in candidates:
            indices = self.owner_chunks[owner]
            translated = [i for i in indices if self.chunks[i].language == language]
            best = max(translated or indices, key=lambda i: float(scores[i]))
            passages.append(self._passage(self.chunks[best]))
        relevance = self.kb.reranker.scores(question, passages)
        maximum = max(fused.values())
        ranked = sorted(
            zip(candidates, relevance),
            key=lambda item: 0.85 * item[1] + 0.15 * fused[item[0]] / maximum,
            reverse=True,
        )
        # Do not reject paraphrases using word coverage or an arbitrary absolute
        # relevance floor. The answer model checks whether these sources suffice.
        return self._pack([owner for owner, _ in ranked], language)

    def _pack(self, owners: list[str], language: str) -> Context:
        documents, passages, scopes = {}, [], []
        remaining = self.settings.context_max_chars
        picked = 0
        for owner in owners:
            entry = self.kb.by_id[owner]
            # Expand a winning child to its complete original clause, preserving
            # conditions. Overlap and overview/child duplicates are removed by ID.
            bundle = [d for d in self.kb.answer_documents(entry) if d.id not in documents]
            if not bundle:
                continue
            records = [
                {
                    "id": d.id,
                    "title": d.title.removeprefix(f"{d.source_file} · "),
                    "text": d.answer_hi if language == "hi" and d.answer_hi else d.answer_en,
                }
                for d in bundle
            ]
            size = len(json.dumps(records, ensure_ascii=False))
            if len(documents) + len(bundle) > self.settings.context_max_sources or size > remaining:
                continue
            documents.update((d.id, d) for d in bundle)
            passages.extend(records)
            remaining -= size
            if entry.document_context and entry.document_context not in scopes:
                scopes.append(entry.document_context)
            picked += 1
            if picked >= self.settings.retrieval_top_k:
                break
        return Context(list(documents.values()), passages, "\n".join(scopes)[:1000])

    def catalog(self, context: Context) -> list[dict]:
        """Topic metadata is a retrieval aid, never answer evidence."""
        present = {document.id for document in context.documents}
        return [
            {"id": entry.id, "topic": entry.title.removeprefix(f"{entry.source_file} · ")}
            for entry in self.kb.entries
            if not {d.id for d in self.kb.answer_documents(entry)}.issubset(present)
        ][:64]

    def expand(self, context: Context, source_ids: list[str], language: str) -> Context:
        if any(source_id not in self.kb.by_id for source_id in source_ids):
            raise ValueError("Unknown requested source")
        return self._pack(source_ids + [document.id for document in context.documents], language)
