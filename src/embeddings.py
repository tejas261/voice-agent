"""Local multilingual E5 embeddings; no network requests at application runtime."""

import hashlib
import threading
from functools import lru_cache
from pathlib import Path

from src.documents import retrieval_passage

MODEL_REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
MODEL_URL = f"https://huggingface.co/Xenova/multilingual-e5-small/resolve/{MODEL_REVISION}/"
ARTIFACTS = [
    (
        "onnx/model_quantized.onnx",
        118308185,
        "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    ),
    (
        "tokenizer.json",
        17082730,
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
]


RERANKER_REVISION = "6f5ff65298512715a1e669753bc754d2bc8f367b"
RERANKER_URL = (
    f"https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX/resolve/{RERANKER_REVISION}/"
)
RERANKER_ARTIFACTS = [
    (
        "onnx/model_quantized.onnx",
        570727094,
        "912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d",
    ),
    (
        "tokenizer.json",
        17082900,
        "8bf8afbfd11306bd872018c53bfdf2e160a56f8edbcf49933324404791c148d3",
    ),
]


class LocalEmbedder:
    def __init__(self, directory: Path, threads: int, artifacts=ARTIFACTS):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        # One checked model/session per process, shared by all indexes.
        for name, size, checksum in artifacts:
            path = directory / Path(name).name
            if (
                path.stat().st_size != size
                or hashlib.sha256(path.read_bytes()).hexdigest() != checksum
            ):
                raise ValueError(f"Retrieval model integrity check failed: {path.name}")
        self.np = np
        self.lock = threading.Lock()
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id("<pad>"), pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(directory / Path(artifacts[0][0]).name), options, providers=["CPUExecutionProvider"]
        )
        self.inputs = {item.name for item in self.session.get_inputs()}

    def run_batch(self, texts):
        np = self.np
        batch = self.tokenizer.encode_batch(texts)
        feed = {
            "input_ids": np.asarray([item.ids for item in batch], dtype=np.int64),
            "attention_mask": np.asarray([item.attention_mask for item in batch], dtype=np.int64),
            "token_type_ids": np.asarray([item.type_ids for item in batch], dtype=np.int64),
        }
        output = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
        return output, feed["attention_mask"]

    def encode(self, texts: list[str]):
        np = self.np
        vectors = []
        with self.lock:
            for offset in range(0, len(texts), 8):
                hidden, attention = self.run_batch(texts[offset : offset + 8])
                mask = attention[..., None]
                pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1)
                normalized = pooled / np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-12)
                vectors.extend(normalized.astype(np.float32))
        return np.stack(vectors)


@lru_cache(maxsize=1)
def get_embedder(directory: Path, threads: int) -> LocalEmbedder:
    return LocalEmbedder(directory, threads)


class LocalReranker(LocalEmbedder):
    def __init__(self, directory: Path, threads: int):
        super().__init__(directory, threads, RERANKER_ARTIFACTS)

    def scores(self, question: str, passages: list[str]) -> list[float]:
        scores = []
        with self.lock:
            for offset in range(0, len(passages), 4):
                logits, _ = self.run_batch([(question, p) for p in passages[offset : offset + 4]])
                scores.extend((1 / (1 + self.np.exp(-logits.reshape(-1).clip(-30, 30)))).tolist())
        # Relevance scores, not calibrated probabilities of answer correctness.
        return scores


@lru_cache(maxsize=1)
def get_reranker(directory: Path, threads: int) -> LocalReranker:
    return LocalReranker(directory, threads)


class SemanticIndex:
    def __init__(self, kb, embedder: LocalEmbedder):
        self.embedder = embedder
        texts = []
        self.owners = []
        self.questions = []
        for entry in kb.entries:
            # Preserve full questions: semantic retrieval must see words such
            # as "about", which lexical stopword filtering discards.
            for question in entry.questions:
                texts.append("query: " + question)
                self.owners.append(entry.id)
                self.questions.append(question)
            texts.append("passage: " + retrieval_passage(entry))
            self.owners.append(entry.id)
            self.questions.append(None)
            if entry.answer_hi:
                texts.append("passage: " + retrieval_passage(entry, "hi"))
                self.owners.append(entry.id)
                self.questions.append(None)
        keys = [hashlib.sha256((MODEL_REVISION + text).encode()).hexdigest() for text in texts]
        with kb.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector BLOB)")
            cached = dict(db.execute("SELECT key, vector FROM embeddings"))
            missing = {key: text for key, text in zip(keys, texts) if key not in cached}
            if missing:
                vectors = embedder.encode(list(missing.values()))
                new = {key: vector.tobytes() for key, vector in zip(missing, vectors)}
                db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?)", new.items())
                cached.update(new)
            # Bound the derived cache to the current knowledge base.
            db.executemany(
                "DELETE FROM embeddings WHERE key = ?", [(k,) for k in cached.keys() - set(keys)]
            )
        self.vectors = embedder.np.stack(
            [embedder.np.frombuffer(cached[key], dtype=embedder.np.float32) for key in keys]
        )

    def scores(self, question: str, query_vector=None) -> tuple[dict[str, float], dict[str, str]]:
        query = (
            self.embedder.encode(["query: " + question])[0]
            if query_vector is None
            else query_vector
        )
        scores = self.vectors @ query
        result = {}
        aliases = {}
        for owner, score, alias in zip(self.owners, scores, self.questions):
            result[owner] = max(result.get(owner, 0.0), float(score))
            if alias is not None and (owner not in aliases or score > aliases[owner][0]):
                aliases[owner] = (float(score), alias)
        return result, {key: value[1] for key, value in aliases.items()}
