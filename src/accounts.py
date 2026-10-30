"""Self-service accounts and opaque, revocable browser sessions."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.businesses import BusinessStore, digest

SESSION_SECONDS = 60 * 60 * 24 * 7


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


class Signup(Credentials):
    @field_validator("password")
    @classmethod
    def strong_password(cls, value):
        if len(value) < 12:
            raise ValueError("Use a password of at least 12 characters")
        return value


class OperatorLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=1024)


class OnboardingProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int = Field(ge=2, le=5)


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return Credentials.valid_email(value)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=256)


class PasswordReset(TokenRequest):
    password: str = Field(min_length=12, max_length=128)


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt$16384$8$1${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = stored.split("$")
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (16384, 8, 1):
            return False
        actual = password_hash(password, bytes.fromhex(salt)).rsplit("$", 1)[1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class AccountStore:
    def __init__(self, businesses: BusinessStore):
        self.businesses = businesses
        self.dummy = password_hash("dummy-password-that-is-never-valid")

    def throttle(self, ip: str, email: str) -> bool:
        now = int(time.time())
        buckets = [(digest("ip:" + ip), 30), (digest("email:" + email), 10)]
        with self.businesses.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM auth_attempts WHERE window_start < ?", (now - 900,))
            for bucket, limit in buckets:
                row = db.execute("SELECT count FROM auth_attempts WHERE bucket = ?", (bucket,)).fetchone()
                if row and row["count"] >= limit:
                    return False
            for bucket, _ in buckets:
                db.execute("""INSERT INTO auth_attempts(bucket, window_start, count) VALUES (?, ?, 1)
                    ON CONFLICT(bucket) DO UPDATE SET count = count + 1""", (bucket, now))
        return True

    def signup(self, credentials: Signup, verified: bool = False) -> str:
        user_id = secrets.token_hex(16)
        hashed = password_hash(credentials.password)
        try:
            with self.businesses.connect() as db:
                db.execute("INSERT INTO users (id,email,password_hash,created_at,email_verified) VALUES (?,?,?,?,?)",
                           (user_id, credentials.email, hashed, int(time.time()), int(verified)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("An account with this email already exists") from exc
        return user_id

    def authenticate(self, credentials: Credentials):
        with self.businesses.connect() as db:
            user = db.execute("SELECT id,password_hash,email_verified FROM users WHERE email = ?", (credentials.email,)).fetchone()
        valid = verify_password(credentials.password, user["password_hash"] if user else self.dummy)
        return dict(user) if user and valid else None

    def user_by_email(self, email: str):
        with self.businesses.connect() as db:
            row = db.execute("SELECT id,email,email_verified FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def google_linked(self, user_id: str) -> bool:
        with self.businesses.connect() as db:
            return db.execute("SELECT 1 FROM google_accounts WHERE user_id = ?", (user_id,)).fetchone() is not None

    def google_signin(self, subject: str, email: str, authoritative: bool) -> str:
        """Use Google's stable subject, and only link by an authoritative verified email."""
        with self.businesses.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            linked = db.execute("SELECT user_id FROM google_accounts WHERE subject = ?", (subject,)).fetchone()
            if linked:
                return linked["user_id"]
            user = db.execute("SELECT id,email_verified FROM users WHERE email = ?", (email,)).fetchone()
            if not authoritative:
                raise ValueError("email_not_authoritative")
            if user:
                if not user["email_verified"]:
                    raise ValueError("email_unverified")
                if db.execute("SELECT 1 FROM google_accounts WHERE user_id = ?", (user["id"],)).fetchone():
                    raise ValueError("already_linked")
                user_id = user["id"]
            else:
                user_id = secrets.token_hex(16)
                db.execute("INSERT INTO users (id,email,password_hash,created_at,email_verified) VALUES (?,?,?,?,1)",
                           (user_id, email, password_hash(secrets.token_urlsafe(32)), int(time.time())))
            db.execute("INSERT INTO google_accounts(subject,user_id) VALUES (?,?)", (subject, user_id))
            return user_id

    def link_google(self, user_id: str, subject: str):
        with self.businesses.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            subject_user = db.execute("SELECT user_id FROM google_accounts WHERE subject = ?", (subject,)).fetchone()
            user_subject = db.execute("SELECT subject FROM google_accounts WHERE user_id = ?", (user_id,)).fetchone()
            if subject_user and subject_user["user_id"] != user_id:
                raise ValueError("already_linked")
            if user_subject and user_subject["subject"] != subject:
                raise ValueError("already_linked")
            if not subject_user:
                db.execute("INSERT INTO google_accounts(subject,user_id) VALUES (?,?)", (subject, user_id))

    def create_oauth_state(self, state: str, browser: str, nonce: str, verifier: str,
                           link_user_id: str | None = None, link_session: str | None = None):
        with self.businesses.connect() as db:
            db.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (int(time.time()),))
            db.execute("""INSERT INTO oauth_states(state_hash,browser_hash,nonce,verifier,expires_at,link_user_id,link_session_hash)
                VALUES (?,?,?,?,?,?,?)""", (digest(state), digest(browser), nonce, verifier, int(time.time()) + 600,
                                               link_user_id, digest(link_session) if link_session else None))

    def consume_oauth_state(self, state: str, browser: str):
        if not state or not browser or len(state) > 256 or len(browser) > 256:
            return None
        with self.businesses.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM oauth_states WHERE state_hash = ? AND browser_hash = ? AND expires_at > ?",
                             (digest(state), digest(browser), int(time.time()))).fetchone()
            if row:
                db.execute("DELETE FROM oauth_states WHERE state_hash = ?", (digest(state),))
                return dict(row)
        return None

    def remove_unverified(self, user_id: str):
        with self.businesses.connect() as db:
            db.execute("DELETE FROM email_tokens WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM users WHERE id = ? AND email_verified = 0", (user_id,))

    def email_token(self, user_id: str, purpose: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.businesses.connect() as db:
            db.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose))
            db.execute("INSERT INTO email_tokens(token_hash,user_id,purpose,expires_at) VALUES (?,?,?,?)",
                       (digest(token), user_id, purpose, int(time.time()) + 3600))
        return token

    def verify_email(self, token: str) -> str | None:
        with self.businesses.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT user_id FROM email_tokens WHERE token_hash = ? AND purpose = 'verify' AND expires_at > ?", (digest(token), int(time.time()))).fetchone()
            if not row:
                return None
            db.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (row["user_id"],))
            db.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = 'verify'", (row["user_id"],))
            return row["user_id"]

    def reset_password(self, token: str, password: str) -> bool:
        hashed = password_hash(password)
        with self.businesses.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT user_id FROM email_tokens WHERE token_hash = ? AND purpose = 'reset' AND expires_at > ?", (digest(token), int(time.time()))).fetchone()
            if not row:
                return False
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed, row["user_id"]))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
            db.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = 'reset'", (row["user_id"],))
            return True

    def start_session(self, user_id: str) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        with self.businesses.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute("INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES (?,?,?,?)",
                       (digest(token), user_id, csrf, now + SESSION_SECONDS))
        return token, csrf

    def session(self, token: str):
        if not token or len(token) > 256:
            return None
        with self.businesses.connect() as db:
            row = db.execute("""SELECT users.id,users.email,users.business_id,users.onboarding_step,sessions.csrf,sessions.expires_at
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
                (digest(token), int(time.time()))).fetchone()
        return dict(row) if row else None

    def end_session(self, token: str):
        if not token or len(token) > 256:
            return
        with self.businesses.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest(token),))

    def set_progress(self, user_id: str, step: int):
        with self.businesses.connect() as db:
            row = db.execute("SELECT business_id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row or not row["business_id"]:
                raise ValueError("Create a workspace first")
            db.execute("UPDATE users SET onboarding_step = ? WHERE id = ?", (step, user_id))
