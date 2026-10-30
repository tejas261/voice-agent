import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    llm_provider: Literal["extractive", "ollama", "groq"] = "extractive"
    business_kind: Literal["insurance", "restaurant", "clinic", "support"] = "insurance"
    businesses_dir: Path = ROOT / "runtime/businesses"
    max_phone_calls: int = Field(default=2, ge=1, le=32)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:1.7b"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "qwen/qwen3.8-27b"
    model_timeout_seconds: float = Field(default=15, ge=1, le=60)
    model_max_tokens: int = Field(default=600, ge=200, le=1500)
    chunk_size_words: int = Field(default=160, ge=32, le=300)
    chunk_overlap_words: int = Field(default=24, ge=0, le=80)
    retrieval_candidates: int = Field(default=16, ge=4, le=32)
    retrieval_top_k: int = Field(default=5, ge=1, le=8)
    context_max_sources: int = Field(default=8, ge=2, le=12)
    context_max_chars: int = Field(default=5000, ge=1500, le=12000)
    kb_path: Path = ROOT / "data/policy_booklets.txt"
    index_path: Path = ROOT / "runtime/knowledge.sqlite3"
    retrieval_mode: Literal["auto", "lexical", "hybrid"] = "auto"
    embedding_dir: Path = ROOT / "models/multilingual-e5-small"
    reranker_dir: Path = ROOT / "models/bge-reranker-v2-m3"
    api_token: SecretStr = SecretStr("")
    public_origin: str = ""
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = ""
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    require_email_verification: bool = False
    voice_enabled: bool = False
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    cpu_threads: int = Field(default=4, ge=1, le=32)
    kokoro_model: Path = ROOT / "models/kokoro-v1.0.int8.onnx"
    kokoro_voices: Path = ROOT / "models/voices-v1.0.bin"
    tts_en_voice: str = "af_heart"
    tts_hi_voice: str = "hf_alpha"
    max_audio_seconds: int = Field(default=20, ge=2, le=60)
    voices_dir: Path = ROOT / "runtime/voices"
    qwen_python: Path = ROOT / ".venv-qwen/bin/python"
    qwen_model_dir: Path = ROOT / "models/qwen3-tts-natural"
    qwen_timeout_seconds: int = Field(default=180, ge=10, le=600)

    @field_validator(
        "kb_path",
        "index_path",
        "kokoro_model",
        "kokoro_voices",
        "embedding_dir",
        "reranker_dir",
        "voices_dir",
        "businesses_dir",
        "qwen_python",
        "qwen_model_dir",
    )
    @classmethod
    def absolute_paths(cls, path: Path) -> Path:
        return path if path.is_absolute() else ROOT / path

    @field_validator("public_origin")
    @classmethod
    def valid_public_origin(cls, value: str) -> str:
        value = value.rstrip("/")
        if value:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError("PUBLIC_ORIGIN must be an HTTPS origin without a path")
        return value

    @model_validator(mode="after")
    def valid_retrieval_limits(self):
        if self.chunk_overlap_words >= self.chunk_size_words // 2:
            raise ValueError("Chunk overlap must be smaller than half the chunk size")
        if self.retrieval_top_k > self.retrieval_candidates:
            raise ValueError("Retrieval top_k cannot exceed the candidate count")
        return self

    @field_validator("google_redirect_uri")
    @classmethod
    def valid_google_redirect_uri(cls, value: str) -> str:
        if value:
            parsed = urlsplit(value)
            local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (parsed.scheme != "https" and not local) or not parsed.hostname or parsed.username or parsed.password or parsed.path != "/api/auth/google/callback" or parsed.query or parsed.fragment:
                raise ValueError("GOOGLE_REDIRECT_URI must be an HTTPS callback URL (or HTTP on localhost)")
        return value

    @classmethod
    def from_env(cls) -> "Settings":
        values = {**dotenv_values(ROOT / ".env"), **os.environ}
        return cls(
            **{
                name: values[name.upper()]
                for name in cls.model_fields
                if values.get(name.upper()) is not None
            }
        )
