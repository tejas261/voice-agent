"""Local hybrid retrieval with original-source citations and linked context."""

import hashlib
import importlib.util
import logging
import math
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from difflib import get_close_matches
from pathlib import Path

from src.documents import load_documents, retrieval_passage
from src.models import FAQ
from src.query_scope import requires_personal_records

log = logging.getLogger(__name__)

STOPWORDS = set(
    "a an the is are was were be do does did i me my we you your it this that what which how when can could would should please tell about of for to in on and or with have has will get mujhe mera meri kya hai hain ka ki ke ko se kab kitne kaise मैं मुझे मेरा मेरी क्या है हैं का की के को से कब कैसे कितने और में पर यह वो आप आपके करें होगा होगी".split()
)
STOPWORDS.update(
    "if during any at am been being had until once upon possible need long too".split()
)
STOPWORDS.update("इस उस ये कौन सा सी बताइए बताएं बताओ".split())

# Small, explicit inflection map: preserve negation and avoid broad stemming
# that would merge unrelated policy concepts. Applied to queries AND the index.
INFLECTIONS = {
    "premiums": "premium",
    "claims": "claim",
    "claimed": "claim",
    "claiming": "claim",
    "refunds": "refund",
    "refunded": "refund",
    "payments": "payment",
    "years": "year",
    "ends": "end",
    "expires": "expire",
    "expired": "expire",
    "policies": "policy",
    "benefits": "benefit",
    "borrowing": "borrow",
    "borrowed": "borrow",
    "covered": "cover",
    "coverage": "cover",
    "yearly": "annual",
    "annually": "annual",
    "smallest": "minimum",
    "lowest": "minimum",
    "highest": "maximum",
    "largest": "maximum",
    "oldest": "old",
    "older": "old",
    "delay": "late",
    "delayed": "late",
    "stop": "end",
    "stops": "end",
    "terminates": "end",
    "termination": "end",
    "finish": "end",
    "finishes": "end",
    "finished": "end",
    "joining": "join",
    "overdue": "late",
    "instalments": "instalment",
    "installment": "instalment",
    "installments": "instalment",
    # Shared field vocabulary lets Hindi keyword matching benefit from the
    # same headings/examples as English. Keep numbers and negation intact.
    "नाम": "name",
    "योजना": "plan",
    "प्लान": "plan",
    "पॉलिसी": "policy",
    "बीमा": "insurance",
    "पूरा": "full",
}


def terms(text: str) -> list[str]:
    text = text.lower().replace("’", "'")
    text = re.sub(r"\b(can't|cannot)\b", "can not", text)
    text = re.sub(r"\bwon't\b", "will not", text)
    text = re.sub(r"n['’]t\b", " not", text)
    # Apostrophe suffixes must not become high-weight standalone tokens:
    # "what's the policy's name" and "what is the policy name" mean the same.
    # Negation is expanded above, before stripping positive contractions.
    text = re.sub(r"\b([a-z]+)'(?:s|re|ve|ll|d|m)\b", r"\1", text)
    text = re.sub(r"\bupper limit\b", "maximum", text)
    text = re.sub(r"\blower limit\b", "minimum", text)
    return list(
        dict.fromkeys(
            INFLECTIONS.get(t, t)
            for t in re.findall(r"[a-z0-9]+|[\u0900-\u097f]+", text)
            if t not in STOPWORDS
        )
    )


@dataclass(frozen=True)
class Hit:
    faq: FAQ
    coverage: float
    score: float = 0
    lexical_score: float = 0
    semantic_score: float = 0
    rerank_score: float = 0


class KnowledgeBase:
    def __init__(
        self,
        path: Path,
        index_path: Path,
        *,
        embedding_dir: Path | None = None,
        reranker_dir: Path | None = None,
        retrieval_mode: str = "lexical",
        cpu_threads: int = 4,
    ):
        self.path, self.index_path = path, index_path
        index_path.parent.mkdir(parents=True, exist_ok=True)
        self.entries, raw = load_documents(path)
        self.faqs = [entry for entry in self.entries if not entry.source_ids]
        self.version = hashlib.sha256(b"faq-index-v7:" + raw).hexdigest()
        if not self.entries or len({f.id for f in self.entries}) != len(self.entries):
            raise ValueError("Knowledge base must contain FAQs with unique IDs")
        self.by_id = {f.id: f for f in self.entries}
        for entry in self.entries:
            linked = entry.context_ids or entry.source_ids
            if any(key not in self.by_id or self.by_id[key].source_ids for key in linked):
                raise ValueError(f"Context must reference original clauses: {entry.id}")
            if entry.context_ids and entry.id not in entry.context_ids:
                raise ValueError(f"Context must include its primary clause: {entry.id}")
            if linked:
                for language in ("en", "hi"):
                    answer = " ".join(
                        getattr(self.by_id[key], f"answer_{language}") or "" for key in linked
                    )
                    if len(answer) > 1200:
                        raise ValueError(f"Linked answer exceeds the speech limit: {entry.id}")
        self.document_terms = {
            f.id: set(terms(" ".join(f.questions) + " " + f.answer_en + " " + (f.answer_hi or "")))
            for f in self.entries
        }
        self.vocabulary = set().union(*self.document_terms.values())
        self.alias_terms = {f.id: [set(terms(q)) for q in f.questions] for f in self.entries}
        frequency = Counter(term for values in self.document_terms.values() for term in values)
        self.unknown_weight = math.log1p(len(self.entries) + 1)
        self.weights = {
            term: math.log1p((len(self.entries) + 1) / (count + 1))
            for term, count in frequency.items()
        }
        # Disposable derived index; normalize documents exactly like queries.
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS faq_search USING fts5(id UNINDEXED, questions, answers, tokenize='unicode61 remove_diacritics 0')"
            )
            old = db.execute("SELECT value FROM metadata WHERE key = ?", ("version",)).fetchone()
            if not old or old[0] != self.version:
                db.execute("DELETE FROM faq_search")
                db.executemany(
                    "INSERT INTO faq_search(id, questions, answers) VALUES (?, ?, ?)",
                    [
                        (
                            f.id,
                            " ".join(terms(" ".join(f.questions))),
                            " ".join(terms(f.answer_en + " " + (f.answer_hi or ""))),
                        )
                        for f in self.entries
                    ],
                )
                db.execute(
                    "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("version", self.version),
                )
        self.semantic = None
        self.reranker = None
        self.retrieval_mode = "lexical"
        if retrieval_mode != "lexical":
            model_exists = (
                embedding_dir
                and (embedding_dir / "model_quantized.onnx").exists()
                and reranker_dir
                and (reranker_dir / "model_quantized.onnx").exists()
                and all(
                    importlib.util.find_spec(name)
                    for name in ("numpy", "onnxruntime", "tokenizers")
                )
            )
            if model_exists:
                from src.embeddings import SemanticIndex, get_embedder, get_reranker

                self.semantic = SemanticIndex(self, get_embedder(embedding_dir, cpu_threads))
                self.reranker = get_reranker(reranker_dir, cpu_threads)
                self.retrieval_mode = "hybrid"
            elif retrieval_mode == "hybrid":
                raise ValueError(
                    "Install requirements/retrieval.lock and run scripts/setup_retrieval.py before enabling hybrid retrieval"
                )
            else:
                log.warning("semantic_model_missing_using_lexical")

    def answer_documents(self, entry: FAQ) -> list[FAQ]:
        return [
            self.by_id[key]
            for key in dict.fromkeys(entry.context_ids or entry.source_ids or [entry.id])
        ]

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.index_path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def rank_candidates(self, question: str) -> list[Hit]:
        tokens = terms(question)[:50]
        # Repair close spelling/ASR variants (e.g. दिलीवरी / डिलीवरी), without
        # claiming semantic matching. Short or ambiguous words stay unchanged.
        corrected = []
        for token in tokens:
            matches = (
                get_close_matches(token, self.vocabulary, n=2, cutoff=0.82)
                if len(token) >= 5 and token not in self.vocabulary
                else []
            )
            corrected.append(matches[0] if len(matches) == 1 else token)
        tokens = list(dict.fromkeys(corrected))
        if not tokens:
            return []
        # Customer input is never interpreted as FTS syntax or SQL.
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        with self.connect() as db:
            rows = db.execute(
                "SELECT id FROM faq_search WHERE faq_search MATCH ? ORDER BY bm25(faq_search, 0, 5, 1) LIMIT ?",
                # Filter candidates before taking the final result limit; a
                # high-ranked but low-coverage hit must not hide a valid one.
                (expression, len(self.entries)),
            ).fetchall()
        semantic_scores, best_aliases = (
            self.semantic.scores(question) if self.semantic else ({}, {})
        )
        candidates = list(dict.fromkeys([key for (key,) in rows] + list(semantic_scores)))
        query_terms = set(tokens)

        def weight(values):
            return sum(self.weights.get(t, self.unknown_weight) for t in values)

        query_weight = weight(query_terms)
        hits = []
        for key in candidates:
            faq = self.by_id[key]
            vocabulary = self.document_terms[key]
            coverage = weight(query_terms & vocabulary) / query_weight
            # Score each example question independently. Unioning unrelated
            # aliases made incidental shared words look like a full match.
            alias_score = max(
                (
                    2 * weight(query_terms & alias) / (query_weight + weight(alias))
                    for alias in self.alias_terms[key]
                ),
                default=0,
            )
            lexical = 0.75 * alias_score + 0.25 * coverage
            semantic = semantic_scores.get(key, 0)
            score = 0.85 * semantic + 0.15 * lexical if self.semantic else lexical
            hits.append(Hit(faq, coverage, score, lexical, semantic))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        if self.reranker:
            # Reserve space for both retrieval signals. The former dense-heavy
            # shortlist could drop a precise keyword match before reranking.
            dense = sorted(hits, key=lambda h: h.semantic_score, reverse=True)[:8]
            lexical = sorted(hits, key=lambda h: h.lexical_score, reverse=True)[:4]
            candidates = list({h.faq.id: h for h in [*dense, *lexical]}.values())
            hindi = bool(re.search(r"[\u0900-\u097f]", question))
            passages = []
            for hit in candidates:
                # Rank the clause's own meaning; expand linked context only
                # after selection, so a grace-period neighbor cannot dominate.
                entry = hit.faq
                passage = retrieval_passage(entry, "hi" if hindi else "en")
                passages.append(f"Question: {best_aliases[entry.id]}\nAnswer: {passage}")
            scores = self.reranker.scores(question, passages)
            hits = [
                replace(
                    hit,
                    rerank_score=score,
                    score=(
                        0.55 * score
                        + 0.25 * hit.semantic_score
                        + 0.15 * hit.lexical_score
                        + 0.05 * hit.coverage
                    ),
                )
                for hit, score in zip(candidates, scores)
            ]
            hits.sort(key=lambda hit: hit.score, reverse=True)
            if max(h.rerank_score for h in hits) < 0.10:
                # Parent metadata helps scope, but can drown out a short clause.
                # Retry only weak results with a compact Q/A view. This keeps
                # the common path to one bounded reranker pass.
                retry = [h for h in hits if h.semantic_score >= 0.88][:3]
                if retry:
                    compact = [
                        f"Question: {best_aliases[h.faq.id]}\nAnswer: "
                        + (h.faq.answer_hi if hindi and h.faq.answer_hi else h.faq.answer_en)
                        for h in retry
                    ]
                    refined = dict(
                        zip((h.faq.id for h in retry), self.reranker.scores(question, compact))
                    )
                    hits = [
                        replace(
                            h,
                            rerank_score=max(h.rerank_score, refined.get(h.faq.id, 0)),
                            score=h.score
                            + 0.55 * max(0, refined.get(h.faq.id, 0) - h.rerank_score),
                        )
                        for h in hits
                    ]
                    hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits

    def search(self, question: str, limit: int = 3) -> list[Hit]:
        if requires_personal_records(question):
            return []
        # A unique full content-word match is a reviewed FAQ mapping. Resolve
        # it directly from its source, saving two model calls on common queries.
        query_terms = set(terms(question))
        # Preserve an explicitly named subject instead of answering just the
        # shared action (e.g. borrowing against two different assets). An
        # unknown subject cannot be silently replaced by this corpus's product.
        subject = re.search(
            r"\bagainst\s+(?:my|our|the|this|that)\s+([a-z0-9 ]+)",
            question,
            re.IGNORECASE,
        )
        if subject:
            phrase = re.split(r"\b(?:if|when|with|under|for)\b", subject[1], maxsplit=1)[0]
            subject_terms = [t for t in terms(phrase) if not t.isdigit()]
            # English noun phrases put the head last: an unfamiliar modifier
            # in "cash value" must not hide the known subject "value".
            if subject_terms and subject_terms[-1] not in self.vocabulary:
                return []
        direct = [
            self.by_id[key]
            for key, aliases in self.alias_terms.items()
            if query_terms and query_terms in aliases
        ]
        if len(direct) == 1:
            return [Hit(direct[0], coverage=1, score=1, lexical_score=1)]
        ranked = self.rank_candidates(question)
        if not ranked:
            return []
        # A unique, complete content-word match to an indexed question is
        # explicit evidence. A passage reranker often underscores bare names
        # and identifiers; it must not veto an agreed, exact FAQ match.
        exact = [
            hit
            for hit in ranked
            if hit.lexical_score >= 1 - 1e-6 and (not self.semantic or hit.semantic_score >= 0.90)
        ]
        if len(exact) == 1:
            return exact

        # Weak relative evidence must not silently ignore an unknown concept
        # (for example a refund being "credited"). Numeric values can be new:
        # the clause may answer them using a stated eligibility range.
        known_concepts = all(t in self.vocabulary or t.isdigit() for t in query_terms)
        return self._supported_hits(ranked, limit, allow_relative=known_concepts)

    def _supported_hits(
        self, ranked: list[Hit], limit: int, *, allow_relative: bool = False
    ) -> list[Hit]:

        def competitors_of(hit, candidates):
            sources = {d.id for d in self.answer_documents(hit.faq)}
            return [
                h
                for h in candidates
                if h.faq.id != hit.faq.id
                and sources.isdisjoint(d.id for d in self.answer_documents(h.faq))
            ]

        def supported(best):
            rivals = competitors_of(best, ranked)
            gap = best.score - rivals[0].score if rivals else 1.0
            if not self.semantic:
                return best.lexical_score >= 0.55 and best.coverage >= 0.65
            return (
                # Strong passage relevance, independently backed by retrieval.
                (best.rerank_score >= 0.3 and best.semantic_score >= 0.85)
                # Moderate relevance needs clear separation from other topics.
                or (
                    best.rerank_score >= 0.10
                    and best.semantic_score >= 0.85
                    and best.lexical_score >= 0.25
                    and gap >= 0.075
                )
                # A well-covered short fact can be under-scored by a reranker.
                # Require agreement of lexical, semantic and coverage signals.
                or (
                    best.semantic_score >= 0.90
                    and best.lexical_score >= 0.65
                    and best.coverage >= 0.95
                    and gap >= 0.075
                )
                # Absolute reranker scores are not calibrated probabilities.
                # A conditional rule may receive a low score even when it is
                # the only relevant passage. Require independent retrieval
                # agreement AND a clear relative relevance margin. Include
                # overlapping overviews in this comparison too.
                or (
                    allow_relative
                    and best.rerank_score >= 0.025
                    and best.semantic_score >= 0.85
                    and best.lexical_score >= 0.20
                    and (
                        best.rerank_score
                        >= 3 * max((h.rerank_score for h in ranked if h != best), default=0.01)
                        or (
                            best.rerank_score
                            > max((h.rerank_score for h in ranked if h != best), default=0)
                            and best.lexical_score
                            >= 0.25 + max((h.lexical_score for h in rivals), default=0)
                        )
                    )
                )
            )

        # Validate all candidates before choosing. Previously an unsupported
        # top hit caused a refusal even when a lower hit answered the question.
        ranked = [hit for hit in ranked if supported(hit)]
        if not ranked:
            return []
        best = ranked[0]
        if best.faq.source_ids:
            # A composed overview contains many incidental facts. Prefer a
            # supported child when the query matches that child's own question
            # examples substantially better than the overview's examples.
            specific = [
                h
                for h in ranked
                if h.faq.id in best.faq.source_ids
                and h.lexical_score >= 0.70
                and h.lexical_score >= best.lexical_score + 0.20
            ]
            if specific:
                return [max(specific, key=lambda h: h.lexical_score)]
        competitors = competitors_of(best, ranked)
        gap = best.score - competitors[0].score if competitors else 1.0
        # Similarity is not proof of relevance. Refuse close, weakly supported
        # ties rather than confidently returning an unrelated narrow clause.
        if gap < 0.015 and best.lexical_score < 0.8:
            return []
        return [hit for hit in ranked if hit.score >= best.score - 0.04][:limit]
