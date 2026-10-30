"""Business workspaces and tenant-scoped knowledge snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from src.assistant import Assistant
from src.config import Settings
from src.models import FAQ


class BusinessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["restaurant", "clinic", "support"]
    # Exotel's destination number, not the caller's number.
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9]{8,15}$")
    # The installed local clone engine currently supports English only.
    language: Literal["en"] = "en"
    greeting: str = Field(min_length=1, max_length=300)


class KnowledgeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=100)
    questions: list[str] = Field(min_length=1, max_length=20)
    answer_en: str = Field(min_length=1, max_length=1200)
    answer_hi: str | None = Field(default=None, min_length=1, max_length=1200)
    document_name: str | None = Field(default=None, min_length=1, max_length=70)

    @field_validator("questions")
    @classmethod
    def valid_questions(cls, questions):
        if any(not question.strip() or len(question) > 300 for question in questions):
            raise ValueError("Questions must be between 1 and 300 characters")
        return [question.strip() for question in questions]


class KnowledgeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[KnowledgeEntry] = Field(min_length=1, max_length=40)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("entries")
    @classmethod
    def unique_entries(cls, entries):
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("Knowledge IDs must be unique")
        return entries


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class BusinessStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = root / "businesses.sqlite3"
        # Additive first migration; existing policy and index files are untouched.
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS businesses (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    phone TEXT NOT NULL UNIQUE, language TEXT NOT NULL, greeting TEXT NOT NULL,
                    access_hash TEXT NOT NULL, stream_hash TEXT NOT NULL,
                    knowledge TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS businesses_phone_idx ON businesses(phone);
            """)
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 5:
                raise ValueError("Business database is newer than this application")
            columns = {row[1] for row in db.execute("PRAGMA table_info(businesses)")}
            if "voice_id" not in columns:
                db.execute("ALTER TABLE businesses ADD COLUMN voice_id TEXT")
            db.execute("BEGIN IMMEDIATE")
            # The pilot required an Exotel number up front. Self-service accounts
            # can create a workspace before obtaining one; existing rows survive.
            columns = {row[1]: row for row in db.execute("PRAGMA table_info(businesses)")}
            if columns["phone"][3]:
                db.execute("""CREATE TABLE businesses_v3 (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    phone TEXT UNIQUE, language TEXT NOT NULL, greeting TEXT NOT NULL,
                    access_hash TEXT NOT NULL, stream_hash TEXT NOT NULL,
                    knowledge TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, voice_id TEXT
                )""")
                db.execute("""INSERT INTO businesses_v3
                    SELECT id,name,kind,phone,language,greeting,access_hash,stream_hash,knowledge,version,voice_id
                    FROM businesses""")
                db.execute("DROP TABLE businesses")
                db.execute("ALTER TABLE businesses_v3 RENAME TO businesses")
                db.execute("CREATE INDEX IF NOT EXISTS businesses_phone_idx ON businesses(phone)")
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                business_id TEXT UNIQUE REFERENCES businesses(id), created_at INTEGER NOT NULL,
                onboarding_step INTEGER NOT NULL DEFAULT 1
            )""")
            user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            if "onboarding_step" not in user_columns:
                db.execute("ALTER TABLE users ADD COLUMN onboarding_step INTEGER NOT NULL DEFAULT 1")
            if "email_verified" not in user_columns:
                db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
                # Existing pilot accounts remain usable after this migration.
                db.execute("UPDATE users SET email_verified = 1")
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS auth_attempts (
                bucket TEXT PRIMARY KEY, window_start INTEGER NOT NULL, count INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS email_tokens (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                purpose TEXT NOT NULL, expires_at INTEGER NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS email_tokens_user_idx ON email_tokens(user_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS google_accounts (
                subject TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE REFERENCES users(id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS oauth_states (
                state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, nonce TEXT NOT NULL,
                verifier TEXT NOT NULL, expires_at INTEGER NOT NULL,
                link_user_id TEXT REFERENCES users(id), link_session_hash TEXT
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS oauth_states_expiry_idx ON oauth_states(expires_at)")
            db.execute("PRAGMA user_version = 5")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, info: BusinessCreate):
        business_id = secrets.token_hex(12)
        access_key = secrets.token_urlsafe(32)
        stream_key = secrets.token_urlsafe(32)
        default = [KnowledgeEntry(
            id="business_identity", title="Business identity",
            questions=["What is this business called?", "Who am I calling?"],
            answer_en=f"This is {info.name}.",
        ).model_dump()]
        with self.connect() as db:
            db.execute(
                "INSERT INTO businesses (id,name,kind,phone,language,greeting,access_hash,stream_hash,knowledge) VALUES (?,?,?,?,?,?,?,?,?)",
                (business_id, info.name, info.kind, info.phone.lstrip("+") if info.phone else None, info.language,
                 info.greeting, digest(access_key), digest(stream_key), json.dumps(default, ensure_ascii=False)),
            )
        return self.get(business_id), access_key, stream_key

    def create_for_user(self, user_id: str, info: BusinessCreate):
        business_id = secrets.token_hex(12)
        access_key, stream_key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        default = [KnowledgeEntry(
            id="business_identity", title="Business identity",
            questions=["What is this business called?", "Who am I calling?"],
            answer_en=f"This is {info.name}.",
        ).model_dump()]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT business_id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user or user["business_id"]:
                raise ValueError("This account already has a workspace")
            db.execute(
                "INSERT INTO businesses (id,name,kind,phone,language,greeting,access_hash,stream_hash,knowledge) VALUES (?,?,?,?,?,?,?,?,?)",
                (business_id, info.name, info.kind, info.phone.lstrip("+") if info.phone else None,
                 info.language, info.greeting, digest(access_key), digest(stream_key), json.dumps(default, ensure_ascii=False)),
            )
            db.execute("UPDATE users SET business_id = ?, onboarding_step = 2 WHERE id = ?", (business_id, user_id))
        return self.get(business_id)

    def get(self, business_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
        return dict(row) if row else None

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,name,kind,phone,language,greeting,version,voice_id FROM businesses ORDER BY name")]

    def set_voice(self, business_id: str, voice_id: str | None):
        with self.connect() as db:
            cursor = db.execute("UPDATE businesses SET voice_id = ? WHERE id = ?", (voice_id, business_id))
            if cursor.rowcount != 1:
                raise KeyError(business_id)
        return self.get(business_id)

    def voice_store(self, business_id: str):
        from src.voices import VoiceStore
        return VoiceStore(self.root / business_id / "voices")

    def update_knowledge(self, business_id: str, entries: list[KnowledgeEntry], expected_version: int | None = None):
        payload = json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False)
        with self.connect() as db:
            if expected_version is None:
                cursor = db.execute("UPDATE businesses SET knowledge = ?, version = version + 1 WHERE id = ?", (payload, business_id))
            else:
                cursor = db.execute("UPDATE businesses SET knowledge = ?, version = version + 1 WHERE id = ? AND version = ?", (payload, business_id, expected_version))
            if cursor.rowcount != 1:
                raise ValueError("Knowledge changed; reload before saving")
        return self.get(business_id)

    def import_knowledge(self, business_id: str, entries: list[KnowledgeEntry]):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT knowledge FROM businesses WHERE id = ?", (business_id,)).fetchone()
            if not row:
                raise KeyError(business_id)
            combined = [KnowledgeEntry.model_validate(item) for item in json.loads(row["knowledge"])]
            document_name = entries[0].document_name
            previous = [entry for entry in combined if entry.document_name == document_name]
            if previous != entries:
                combined = [entry for entry in combined if entry.document_name != document_name] + entries
                KnowledgeUpdate(entries=combined)
                db.execute("UPDATE businesses SET knowledge = ?, version = version + 1 WHERE id = ?", (
                    json.dumps([entry.model_dump() for entry in combined], ensure_ascii=False), business_id,
                ))
        return self.get(business_id)

    def update(self, business_id: str, info: BusinessCreate):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT name, knowledge FROM businesses WHERE id = ?", (business_id,)).fetchone()
            if not row:
                raise KeyError(business_id)
            knowledge = json.loads(row["knowledge"])
            for entry in knowledge:
                if entry["id"] == "business_identity" and entry["answer_en"] == f"This is {row['name']}.":
                    entry["answer_en"] = f"This is {info.name}."
            cursor = db.execute(
                "UPDATE businesses SET name=?, kind=?, phone=?, language=?, greeting=?, knowledge=?, version=version+1 WHERE id=?",
                (info.name, info.kind, info.phone.lstrip("+") if info.phone else None, info.language, info.greeting,
                 json.dumps(knowledge, ensure_ascii=False), business_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(business_id)
        return self.get(business_id)

    def rotate_keys(self, business_id: str):
        access_key, stream_key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE businesses SET access_hash = ?, stream_hash = ? WHERE id = ?",
                (digest(access_key), digest(stream_key), business_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(business_id)
        return access_key, stream_key

    def rotate_stream_key(self, business_id: str):
        stream_key = secrets.token_urlsafe(32)
        with self.connect() as db:
            cursor = db.execute("UPDATE businesses SET stream_hash = ? WHERE id = ?", (digest(stream_key), business_id))
            if cursor.rowcount != 1:
                raise KeyError(business_id)
        return stream_key

    @staticmethod
    def check_key(business: dict, key: str, field: str) -> bool:
        return bool(key) and hmac.compare_digest(business[field], digest(key))

    def assistant(self, business: dict, settings: Settings) -> Assistant:
        # One immutable source snapshot and one index per business. Never derive a
        # path from a caller-supplied phone number or knowledge entry.
        folder = self.root / business["id"]
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = folder / f"knowledge-{business['version']}.json"
        if not path.exists():
            entries = json.loads(business["knowledge"])
            for entry in entries:
                entry.pop("document_name", None)
                entry["document_context"] = f"Business name: {business['name']}\nBusiness type: {business['kind']}"
            TypeAdapterFAQ.validate_python(entries)
            tmp = folder / f".{secrets.token_hex(8)}.tmp"
            tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        config = settings.model_copy(update={
            "kb_path": path, "index_path": folder / f"index-{business['version']}.sqlite3",
            "business_kind": business["kind"],
        })
        return Assistant(config)


TypeAdapterFAQ = TypeAdapter(list[FAQ])
