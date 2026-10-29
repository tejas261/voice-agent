from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["en", "hi"]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1500)


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    question: str = Field(min_length=1, max_length=1000)
    language: Language = "en"
    history: list[Message] = Field(default_factory=list, max_length=6)


class SpeakRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(min_length=1, max_length=1200)
    language: Language = "en"
    voice_id: str = Field(default="default", pattern=r"^(default|[a-f0-9]{24})$")
    # Accept the old neutral value so previously loaded clients keep working.
    # OS accent presets were removed; they changed the speaker and sounded flat.
    accent: Literal["original"] = "original"


class Source(BaseModel):
    id: str
    title: str
    text: str
    file: str | None = None


class Answer(BaseModel):
    answer: str
    language: Language
    sources: list[Source] = Field(default_factory=list)
    mode: Literal["faq", "ollama", "groq", "fallback", "no_match"]
    needs_human: bool = False
    cached: bool = False
    latency_ms: float = 0


class FAQ(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=160)
    questions: list[str] = Field(min_length=1, max_length=30)
    answer_en: str = Field(min_length=1, max_length=1200)
    answer_hi: str | None = Field(default=None, min_length=1, max_length=1200)
    # Optional source-checked wording for spoken extractive answers.
    voice_answer_en: str | None = Field(default=None, min_length=1, max_length=1200)
    source_text: str | None = None
    source_file: str | None = None
    document_context: str | None = Field(default=None, max_length=2000)
    # Derived overview passages cite their originals. Individual clauses may
    # require linked context (e.g. grace period alongside non-payment rules).
    source_ids: list[str] = Field(default_factory=list, max_length=6)
    context_ids: list[str] = Field(default_factory=list, max_length=6)
