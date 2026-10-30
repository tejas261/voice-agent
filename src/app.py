import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.accounts import (
    SESSION_SECONDS,
    AccountStore,
    Credentials,
    EmailRequest,
    OnboardingProgress,
    OperatorLogin,
    PasswordReset,
    Signup,
    TokenRequest,
)
from src.assistant import Assistant
from src.audio_stream import SpeechStream
from src.businesses import BusinessCreate, BusinessStore, KnowledgeEntry, KnowledgeUpdate
from src.cloned_speech import ClonedSpeech
from src.config import ROOT, Settings
from src.google_auth import authorize_url, exchange_code
from src.google_auth import configured as google_configured
from src.mailer import configured as mail_configured
from src.mailer import send_account_email
from src.models import AskRequest, SpeakRequest
from src.speech import SpeechService
from src.telephony import exotel_session
from src.voices import VoiceStore

log = logging.getLogger(__name__)


class JSONLogFormatter(logging.Formatter):
    def format(self, record):
        data = {"event": record.getMessage(), "level": record.levelname}
        for key in (
            "provider",
            "reason",
            "status_code",
            "mode",
            "latency_ms",
            "source_count",
            "audio_tokens",
            "audio_samples",
            "retries",
            "path",
        ):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data)


def create_app(settings: Settings | None = None, speech=None, cloned_speech=None) -> FastAPI:
    settings = settings or Settings.from_env()
    static_dir = ROOT / "static"
    digest = hashlib.sha256()
    for asset in sorted(static_dir.rglob("*")):
        if asset.is_file():
            digest.update(str(asset.relative_to(static_dir)).encode() + b"\0" + asset.read_bytes())
    asset_prefix = f"/static/{digest.hexdigest()[:16]}"

    @asynccontextmanager
    async def lifespan(app):
        app.state.assistant = Assistant(settings)
        app.state.businesses = BusinessStore(settings.businesses_dir)
        app.state.accounts = AccountStore(app.state.businesses)
        app.state.operator_sessions = {}
        app.state.business_voices = {}
        app.state.speech = speech or SpeechService(settings)
        app.state.voices = VoiceStore(settings.voices_dir)
        app.state.cloned_speech = cloned_speech or ClonedSpeech(settings, app.state.voices)
        app.state.voice_error = None
        app.state.speech_slots = asyncio.Semaphore(1)
        app.state.phone_slots = asyncio.Semaphore(settings.max_phone_calls)
        if settings.voice_enabled:
            try:
                await asyncio.to_thread(app.state.speech.load)
            except Exception as exc:
                app.state.voice_error = "Voice models could not load. Run scripts/setup_models.py and check your voice settings."
                log.error("voice_load_failed", extra={"reason": type(exc).__name__})
        try:
            yield
        finally:
            await app.state.assistant.close()
            for voice in app.state.business_voices.values():
                await asyncio.to_thread(voice.close)
            await asyncio.to_thread(app.state.cloned_speech.close)

    app = FastAPI(
        title="Local voice support",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    if not settings.api_token.get_secret_value():
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
        )

    @app.middleware("http")
    async def request_limits(request, call_next):
        if request.url.path == "/api/auth/google/callback":
            # Uvicorn logs its scope's request target. Never log OAuth codes or state.
            request.scope["google_query"] = dict(request.query_params)
            request.scope["query_string"] = b""
        if request.url.path.startswith("/static/") and request.url.path.rsplit("/", 1)[-1] in {"index.html", "business.html", "portal.html"}:
            return Response(status_code=404)
        # Bound streamed bodies as well as declared Content-Length.
        limit = (
            3 * 1024 * 1024 if request.url.path in {"/api/transcribe", "/api/voices"} or (request.url.path.startswith("/api/businesses/") and request.url.path.endswith("/voice")) else 32 * 1024
        )
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit:
                return JSONResponse(
                    {
                        "error": {
                            "code": "body_too_large",
                            "message": "Request exceeds the size limit",
                        }
                    },
                    status_code=413,
                )
        request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith(asset_prefix + "/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    async def authorized(request: Request):
        origin = request.headers.get("origin")
        expected = urlsplit(settings.public_origin) if settings.public_origin else request.url
        if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (expected.scheme, expected.netloc):
            raise HTTPException(403, "Cross-origin requests are not allowed")
        token = settings.api_token.get_secret_value()
        if token:
            operator_cookie = request.cookies.get("listen_operator", "")
            expires = app.state.operator_sessions.get(hashlib.sha256(operator_cookie.encode()).hexdigest(), 0)
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token) and expires <= time.time():
                raise HTTPException(401, "A valid API token is required")
        else:
            raise HTTPException(403, "Configure an operator token to use internal tools")

    def cookie_secure(request: Request):
        return bool(settings.public_origin.startswith("https://") or request.url.scheme == "https" or request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"})

    def set_session_cookie(response, request, token):
        response.set_cookie("listen_session", token, max_age=SESSION_SECONDS, httponly=True,
                            secure=cookie_secure(request), samesite="lax", path="/")

    def same_origin(request):
        origin = request.headers.get("origin")
        expected = urlsplit(settings.public_origin) if settings.public_origin else request.url
        if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (expected.scheme, expected.netloc):
            raise HTTPException(403, "Cross-origin requests are not allowed")

    async def current_session(request: Request, *, csrf: bool = False):
        same_origin(request)
        session = await asyncio.to_thread(app.state.accounts.session, request.cookies.get("listen_session", ""))
        if not session:
            raise HTTPException(401, "Please sign in")
        if csrf and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            raise HTTPException(403, "Session verification failed; refresh the page")
        return session

    def session_info(session):
        return {"email": session["email"], "business_id": session["business_id"], "onboarding_step": session["onboarding_step"], "csrf": session["csrf"],
                "google_linked": app.state.accounts.google_linked(session["id"])}

    @app.post("/api/auth/signup", status_code=201)
    async def signup(request: Request, body: Signup):
        same_origin(request)
        ip = request.client.host if request.client else "unknown"
        if not await asyncio.to_thread(app.state.accounts.throttle, ip, body.email):
            raise HTTPException(429, "Too many attempts. Please try again later")
        verify = bool(settings.require_email_verification or settings.public_origin or request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"})
        if verify and not mail_configured(settings):
            raise HTTPException(503, "Sign-up is unavailable until email verification is configured")
        try:
            user_id = await asyncio.to_thread(app.state.accounts.signup, body, not verify)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if verify:
            token = await asyncio.to_thread(app.state.accounts.email_token, user_id, "verify")
            try:
                await asyncio.to_thread(send_account_email, settings, body.email, "verify", token)
            except Exception as exc:
                await asyncio.to_thread(app.state.accounts.remove_unverified, user_id)
                log.warning("verification_email_failed", extra={"reason": type(exc).__name__})
                raise HTTPException(503, "Could not send the verification email. Please try again") from exc
            return JSONResponse({"verification_required": True}, status_code=201)
        token, _ = await asyncio.to_thread(app.state.accounts.start_session, user_id)
        session = await asyncio.to_thread(app.state.accounts.session, token)
        response = JSONResponse(session_info(session), status_code=201)
        set_session_cookie(response, request, token)
        log.info("account_created")
        return response

    @app.post("/api/auth/login")
    async def login(request: Request, body: Credentials):
        same_origin(request)
        ip = request.client.host if request.client else "unknown"
        if not await asyncio.to_thread(app.state.accounts.throttle, ip, body.email):
            raise HTTPException(429, "Too many attempts. Please try again later")
        user = await asyncio.to_thread(app.state.accounts.authenticate, body)
        if not user:
            raise HTTPException(401, "Email or password is incorrect")
        if not user["email_verified"]:
            raise HTTPException(403, "Verify your email before signing in")
        token, _ = await asyncio.to_thread(app.state.accounts.start_session, user["id"])
        session = await asyncio.to_thread(app.state.accounts.session, token)
        response = JSONResponse(session_info(session))
        set_session_cookie(response, request, token)
        log.info("account_signed_in")
        return response

    @app.get("/api/auth/capabilities")
    async def auth_capabilities():
        return {"email_recovery": mail_configured(settings), "google": google_configured(settings)}

    async def google_start(request: Request, link_session=None):
        if not google_configured(settings):
            raise HTTPException(503, "Google sign-in is not configured")
        expected = urlsplit(settings.public_origin) if settings.public_origin else request.url
        redirect = urlsplit(settings.google_redirect_uri)
        if (expected.scheme, expected.netloc) != (redirect.scheme, redirect.netloc):
            raise HTTPException(400, "Open this site at the configured Google sign-in address")
        ip = request.client.host if request.client else "unknown"
        if not await asyncio.to_thread(app.state.accounts.throttle, ip, "google:" + ip):
            raise HTTPException(429, "Too many attempts. Please try again later")
        state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        await asyncio.to_thread(app.state.accounts.create_oauth_state, state, browser, nonce, verifier,
                                link_session["id"] if link_session else None,
                                request.cookies.get("listen_session") if link_session else None)
        url = authorize_url(settings, state, nonce, verifier)
        response = JSONResponse({"url": url}) if link_session else RedirectResponse(url, status_code=303)
        response.set_cookie("listen_oauth", browser, max_age=600, httponly=True, secure=cookie_secure(request),
                            samesite="lax", path="/api/auth/google/callback")
        return response

    @app.get("/api/auth/google/start")
    async def google_login_start(request: Request):
        return await google_start(request)

    @app.post("/api/auth/google/link")
    async def google_link_start(request: Request):
        return await google_start(request, await current_session(request, csrf=True))

    @app.get("/api/auth/google/callback")
    async def google_callback(request: Request):
        params = request.scope.get("google_query", {})
        state = str(params.get("state", ""))
        browser = request.cookies.get("listen_oauth", "")
        saved = await asyncio.to_thread(app.state.accounts.consume_oauth_state, state, browser)
        link = bool(saved and saved["link_user_id"])
        result = "failed"
        try:
            if not saved:
                raise ValueError("invalid_state")
            if params.get("error"):
                raise ValueError("cancelled")
            code = params.get("code")
            if not isinstance(code, str) or not 1 <= len(code) <= 4096 or not google_configured(settings):
                raise ValueError("invalid_code")
            if link:
                raw_session = request.cookies.get("listen_session", "")
                session = await asyncio.to_thread(app.state.accounts.session, raw_session)
                if not session or session["id"] != saved["link_user_id"] or not hmac.compare_digest(hashlib.sha256(raw_session.encode()).hexdigest(), saved["link_session_hash"] or ""):
                    raise ValueError("link_session_expired")
            identity = await exchange_code(settings, code, saved["verifier"], saved["nonce"])
            if link:
                await asyncio.to_thread(app.state.accounts.link_google, saved["link_user_id"], identity["subject"])
                response = RedirectResponse("/app?google=linked", status_code=303)
            else:
                user_id = await asyncio.to_thread(app.state.accounts.google_signin, identity["subject"], identity["email"], identity["authoritative"])
                token, _ = await asyncio.to_thread(app.state.accounts.start_session, user_id)
                response = RedirectResponse("/app", status_code=303)
                set_session_cookie(response, request, token)
            log.info("google_account_authenticated")
        except Exception as exc:
            if isinstance(exc, ValueError) and str(exc) in {"email_not_authoritative", "email_unverified", "already_linked", "cancelled", "link_session_expired"}:
                result = str(exc)
            log.warning("google_auth_failed", extra={"reason": type(exc).__name__})
            response = RedirectResponse(("/app" if link else "/login") + "?google=" + result, status_code=303)
        response.delete_cookie("listen_oauth", path="/api/auth/google/callback")
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.post("/api/auth/verify-email")
    async def verify_email(request: Request, body: TokenRequest):
        same_origin(request)
        if not await asyncio.to_thread(app.state.accounts.throttle, request.client.host if request.client else "unknown", "verify:" + body.token):
            raise HTTPException(429, "Too many attempts. Please try again later")
        user_id = await asyncio.to_thread(app.state.accounts.verify_email, body.token)
        if not user_id:
            raise HTTPException(400, "This verification link is invalid or expired")
        token, _ = await asyncio.to_thread(app.state.accounts.start_session, user_id)
        session = await asyncio.to_thread(app.state.accounts.session, token)
        response = JSONResponse(session_info(session))
        set_session_cookie(response, request, token)
        return response

    @app.post("/api/auth/email-link")
    async def account_email_link(request: Request, body: EmailRequest, purpose: Literal["verify", "reset"] = "reset"):
        same_origin(request)
        if not mail_configured(settings):
            raise HTTPException(503, "Email recovery is not configured")
        ip = request.client.host if request.client else "unknown"
        if not await asyncio.to_thread(app.state.accounts.throttle, ip, body.email):
            raise HTTPException(429, "Too many attempts. Please try again later")
        user = await asyncio.to_thread(app.state.accounts.user_by_email, body.email)
        if user and (bool(user["email_verified"]) == (purpose == "reset")):
            token = await asyncio.to_thread(app.state.accounts.email_token, user["id"], purpose)
            try:
                await asyncio.to_thread(send_account_email, settings, user["email"], purpose, token)
            except Exception as exc:
                log.warning("account_email_failed", extra={"reason": type(exc).__name__})
                raise HTTPException(503, "Could not send the email. Please try again") from exc
        return {"message": "If an eligible account exists, an email has been sent."}

    @app.post("/api/auth/reset-password")
    async def reset_password(request: Request, body: PasswordReset):
        same_origin(request)
        if not await asyncio.to_thread(app.state.accounts.throttle, request.client.host if request.client else "unknown", "reset:" + body.token):
            raise HTTPException(429, "Too many attempts. Please try again later")
        if not await asyncio.to_thread(app.state.accounts.reset_password, body.token, body.password):
            raise HTTPException(400, "This reset link is invalid or expired")
        response = JSONResponse({"message": "Password updated. Please sign in."})
        response.delete_cookie("listen_session", path="/")
        return response

    @app.get("/api/auth/me")
    async def me(request: Request):
        return session_info(await current_session(request))

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        await current_session(request, csrf=True)
        await asyncio.to_thread(app.state.accounts.end_session, request.cookies.get("listen_session", ""))
        response = Response(status_code=204)
        response.delete_cookie("listen_session", path="/")
        return response

    @app.post("/api/operator/login")
    async def operator_login(request: Request, body: OperatorLogin):
        same_origin(request)
        ip = request.client.host if request.client else "unknown"
        if not await asyncio.to_thread(app.state.accounts.throttle, "operator:" + ip, "operator"):
            raise HTTPException(429, "Too many attempts. Please try again later")
        configured = settings.api_token.get_secret_value()
        if not configured or not hmac.compare_digest(body.token, configured):
            raise HTTPException(401, "Operator token is incorrect or not configured")
        raw = secrets.token_urlsafe(32)
        now = time.time()
        app.state.operator_sessions = {key: expiry for key, expiry in app.state.operator_sessions.items() if expiry > now}
        app.state.operator_sessions[hashlib.sha256(raw.encode()).hexdigest()] = now + 28800
        response = JSONResponse({"ok": True})
        response.set_cookie("listen_operator", raw, max_age=28800, httponly=True, secure=cookie_secure(request), samesite="strict", path="/")
        return response

    @app.post("/api/operator/logout", dependencies=[Depends(authorized)])
    async def operator_logout(request: Request):
        raw = request.cookies.get("listen_operator", "")
        app.state.operator_sessions.pop(hashlib.sha256(raw.encode()).hexdigest(), None)
        response = Response(status_code=204)
        response.delete_cookie("listen_operator", path="/")
        return response

    @app.post("/api/onboarding/business", status_code=201)
    async def onboarding_business(request: Request, body: BusinessCreate):
        session = await current_session(request, csrf=True)
        if session["business_id"]:
            raise HTTPException(409, "This account already has a workspace")
        try:
            business = await asyncio.to_thread(app.state.businesses.create_for_user, session["id"], body)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "That phone number is already assigned") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"business": public_business(business)}

    @app.post("/api/onboarding/progress")
    async def onboarding_progress(request: Request, body: OnboardingProgress):
        session = await current_session(request, csrf=True)
        try:
            await asyncio.to_thread(app.state.accounts.set_progress, session["id"], body.step)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"step": body.step}

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            {"error": {"code": f"http_{exc.status_code}", "message": str(exc.detail)}},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_input",
                    "message": "Check the request fields, language, and length limits",
                }
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.error("request_failed", extra={"reason": type(exc).__name__, "path": request.url.path})
        return JSONResponse(
            {"error": {"code": "internal_error", "message": "The request could not be completed"}},
            status_code=500,
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status", dependencies=[Depends(authorized)])
    async def status():
        return {
            "provider": settings.llm_provider,
            "answer_model": settings.groq_model
            if settings.llm_provider == "groq"
            else (settings.ollama_model if settings.llm_provider == "ollama" else None),
            "retrieval_config": {
                "chunk_size_words": settings.chunk_size_words,
                "chunk_overlap_words": settings.chunk_overlap_words,
                "candidates": settings.retrieval_candidates,
                "top_k": settings.retrieval_top_k,
                "context_max_chars": settings.context_max_chars,
            },
            "retrieval_mode": app.state.assistant.kb.retrieval_mode,
            "retrieval_models": {
                "embedding": settings.embedding_dir.name,
                "reranker": settings.reranker_dir.name,
            }
            if app.state.assistant.kb.retrieval_mode == "hybrid"
            else None,
            "voice_ready": app.state.speech.ready,
            "voice_error": app.state.voice_error,
            "voice_cloning": app.state.cloned_speech.status(),
            "faq_count": len(app.state.assistant.kb.faqs),
            "max_audio_seconds": settings.max_audio_seconds,
            "languages": ["en", "hi"],
            "knowledge_base": {
                "name": settings.kb_path.name,
                "entry_count": len(app.state.assistant.kb.faqs),
                "passage_count": len(app.state.assistant.kb.entries),
                "suggestions": [
                    {
                        "question": faq.questions[1]
                        if faq.source_file and len(faq.questions) > 1
                        else faq.questions[0],
                        "language": "en",
                    }
                    for faq in app.state.assistant.kb.faqs[:3]
                ],
            },
        }

    @app.post("/api/ask", dependencies=[Depends(authorized)])
    async def ask(body: AskRequest):
        log.info("question_received", extra={"path": "/api/ask"})
        return await app.state.assistant.answer(body)

    def public_business(business):
        return {key: business[key] for key in ("id", "name", "kind", "phone", "language", "greeting", "version", "voice_id")}

    async def business_access(request: Request, business_id: str):
        business = await asyncio.to_thread(app.state.businesses.get, business_id)
        if not business:
            raise HTTPException(404, "Business not found")
        same_origin(request)
        header = request.headers.get("authorization", "")
        key = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
        if app.state.businesses.check_key(business, key, "access_hash"):
            return business
        if header or request.cookies.get("listen_operator"):
            await authorized(request)
            return business
        session = await current_session(request, csrf=request.method not in {"GET", "HEAD", "OPTIONS"})
        if session["business_id"] != business_id:
            raise HTTPException(404, "Business not found")
        return business

    @app.post("/api/businesses", dependencies=[Depends(authorized)], status_code=201)
    async def create_business(body: BusinessCreate):
        try:
            business, access_key, stream_key = await asyncio.to_thread(app.state.businesses.create, body)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "That phone number is already assigned") from exc
        return {"business": public_business(business), "access_key": access_key, "stream_key": stream_key}

    @app.get("/api/businesses", dependencies=[Depends(authorized)])
    async def list_businesses():
        return {"businesses": await asyncio.to_thread(app.state.businesses.list)}

    @app.post("/api/businesses/{business_id}/rotate-keys", dependencies=[Depends(authorized)])
    async def rotate_business_keys(business_id: str):
        try:
            access_key, stream_key = await asyncio.to_thread(app.state.businesses.rotate_keys, business_id)
        except KeyError as exc:
            raise HTTPException(404, "Business not found") from exc
        return {"access_key": access_key, "stream_key": stream_key}

    @app.post("/api/businesses/{business_id}/stream-key")
    async def create_stream_key(request: Request, business_id: str):
        business = await business_access(request, business_id)
        if not business["phone"]:
            raise HTTPException(409, "Add the Exotel destination number first")
        stream_key = await asyncio.to_thread(app.state.businesses.rotate_stream_key, business_id)
        return {"stream_key": stream_key, "business_id": business_id}

    @app.get("/api/businesses/{business_id}/knowledge")
    async def get_business_knowledge(request: Request, business_id: str):
        business = await business_access(request, business_id)
        voice = app.state.business_voices.get(business_id)
        return {"business": public_business(business), "entries": json.loads(business["knowledge"]),
                "voice_prepared": bool(voice and business["voice_id"] and voice.prepared_voice == business["voice_id"])}

    @app.put("/api/businesses/{business_id}")
    async def update_business(request: Request, business_id: str, body: BusinessCreate):
        await business_access(request, business_id)
        try:
            business = await asyncio.to_thread(app.state.businesses.update, business_id, body)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "That phone number is already assigned") from exc
        return public_business(business)

    @app.put("/api/businesses/{business_id}/knowledge")
    async def update_business_knowledge(request: Request, business_id: str, body: KnowledgeUpdate):
        await business_access(request, business_id)
        try:
            business = await asyncio.to_thread(app.state.businesses.update_knowledge, business_id, body.entries, body.expected_version)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"business": public_business(business), "entries": json.loads(business["knowledge"])}

    @app.post("/api/businesses/{business_id}/knowledge/import")
    async def import_business_knowledge(request: Request, business_id: str, file: UploadFile = File()):
        await business_access(request, business_id)
        try:
            if not file.filename or not file.filename.lower().endswith(".txt"):
                raise ValueError("Upload a UTF-8 .txt file")
            raw = await file.read()
            if not raw or len(raw) > 24_000:
                raise ValueError("The text file must be between 1 and 24,000 bytes")
            content = raw.decode("utf-8-sig")
            paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
            if not paragraphs or any(len(part) > 1200 for part in paragraphs):
                raise ValueError("Separate the text into paragraphs of at most 1,200 characters")
            name = Path(file.filename.replace("\\", "/")).name[:70]
            entries = [
                KnowledgeEntry(
                    id="doc_" + hashlib.sha256((name + "\0" + str(index) + "\0" + paragraph).encode()).hexdigest()[:24],
                    title=f"{name} · section {index}",
                    questions=[f"{name} section {index}"] + [
                        sentence[:300] for sentence in re.split(r"(?<=[.!?])\s+|\n+", paragraph)
                        if sentence.strip()
                    ][:19],
                    answer_en=paragraph, document_name=name,
                )
                for index, paragraph in enumerate(paragraphs, 1)
            ]
            business = await asyncio.to_thread(app.state.businesses.import_knowledge, business_id, entries)
            return {"business": public_business(business), "entries": json.loads(business["knowledge"])}
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            await file.close()

    @app.post("/api/businesses/{business_id}/ask")
    async def ask_business(request: Request, business_id: str, body: AskRequest):
        business = await business_access(request, business_id)
        agent = await asyncio.to_thread(app.state.businesses.assistant, business, settings)
        try:
            return await agent.answer(body)
        finally:
            await agent.close()

    def business_voice(business_id):
        if business_id not in app.state.business_voices:
            app.state.business_voices[business_id] = ClonedSpeech(
                settings, app.state.businesses.voice_store(business_id)
            )
        return app.state.business_voices[business_id]

    @app.post("/api/businesses/{business_id}/voice", status_code=201)
    async def create_business_voice(
        request: Request, business_id: str, name: str = Form(min_length=1, max_length=60),
        transcript: str = Form(min_length=1, max_length=1000), consent: bool = Form(),
        audio: UploadFile = File(),
    ):
        business = await business_access(request, business_id)
        if not consent:
            raise HTTPException(422, "Permission from the voice owner is required")
        try:
            profile = await asyncio.to_thread(app.state.businesses.voice_store(business_id).create, name, transcript, await audio.read())
            await asyncio.to_thread(app.state.businesses.set_voice, business_id, profile.id)
            if business["voice_id"] and business["voice_id"] != profile.id:
                old_worker = app.state.business_voices.pop(business_id, None)
                if old_worker:
                    await asyncio.to_thread(old_worker.delete_voice, business["voice_id"])
                else:
                    await asyncio.to_thread(app.state.businesses.voice_store(business_id).delete, business["voice_id"])
            return {"voice": profile.public(), "prepared": False}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            await audio.close()

    @app.post("/api/businesses/{business_id}/voice/prepare")
    async def prepare_business_voice(request: Request, business_id: str):
        business = await business_access(request, business_id)
        if not business["voice_id"]:
            raise HTTPException(409, "Upload a custom voice first")
        voice = business_voice(business_id)
        if not voice.status()["available"]:
            raise HTTPException(503, "Local voice cloning is not installed")
        try:
            ready = await asyncio.to_thread(voice.prepare, business["voice_id"])
        except Exception as exc:
            log.warning("business_voice_prepare_failed", extra={"reason": type(exc).__name__})
            raise HTTPException(503, "Could not prepare the custom voice") from exc
        return {"prepared": ready}

    @app.post("/api/businesses/{business_id}/voice/preview")
    async def preview_business_voice(request: Request, business_id: str, body: SpeakRequest):
        business = await business_access(request, business_id)
        voice = app.state.business_voices.get(business_id)
        if body.language != "en" or body.voice_id != "default":
            raise HTTPException(422, "Preview uses this business's English voice")
        if not business["voice_id"] or not voice or voice.prepared_voice != business["voice_id"]:
            raise HTTPException(409, "Prepare this business's custom voice first")
        try:
            audio = await asyncio.to_thread(voice.synthesize, body.text, "en", business["voice_id"])
        except Exception as exc:
            log.warning("business_voice_preview_failed", extra={"reason": type(exc).__name__})
            raise HTTPException(503, "Could not preview the custom voice") from exc
        return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.delete("/api/businesses/{business_id}/voice", status_code=204)
    async def delete_business_voice(request: Request, business_id: str):
        business = await business_access(request, business_id)
        if not business["voice_id"]:
            raise HTTPException(404, "No custom voice is configured")
        await asyncio.to_thread(app.state.businesses.set_voice, business_id, None)
        worker = app.state.business_voices.pop(business_id, None)
        if worker:
            await asyncio.to_thread(worker.delete_voice, business["voice_id"])
        else:
            await asyncio.to_thread(app.state.businesses.voice_store(business_id).delete, business["voice_id"])
        return Response(status_code=204)

    @app.websocket("/telephony/exotel/{business_id}")
    async def exotel_stream(websocket: WebSocket, business_id: str):
        # Public telephony must never run with the local-only operator setup.
        if not settings.api_token.get_secret_value():
            await websocket.close(code=1008)
            return
        business = await asyncio.to_thread(app.state.businesses.get, business_id)
        header = websocket.headers.get("authorization", "")
        try:
            encoded = header.removeprefix("Basic ") if header.startswith("Basic ") else ""
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeError, binascii.Error):
            await websocket.close(code=1008)
            return
        if not business or not hmac.compare_digest(username, business_id) or not app.state.businesses.check_key(business, password, "stream_hash"):
            await websocket.close(code=1008)
            return
        voice = app.state.business_voices.get(business_id)
        if not business["voice_id"] or not voice or voice.prepared_voice != business["voice_id"]:
            await websocket.close(code=1013)
            return
        await exotel_session(websocket, business, app.state.businesses, settings, app.state.speech, voice, app.state.phone_slots)

    async def run_speech(function, *args, require_standard=True):
        if require_standard and not app.state.speech.ready:
            raise HTTPException(
                503,
                "Local speech is not ready. Install voice dependencies, run scripts/setup_models.py, and set VOICE_ENABLED=true.",
            )
        if app.state.speech_slots.locked():
            raise HTTPException(429, "Speech engine is busy. Try again shortly.")
        async with app.state.speech_slots:
            worker = asyncio.create_task(asyncio.to_thread(function, *args))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Cancelling a request does not stop native inference. Keep its
                # capacity reserved until the worker really exits.
                await worker
                raise
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(
                    404, "That voice no longer exists. Choose another voice"
                ) from exc
            except TimeoutError as exc:
                raise HTTPException(503, str(exc)) from exc
            except Exception as exc:
                log.error("speech_failed", extra={"reason": type(exc).__name__})
                raise HTTPException(
                    503, "Local speech processing failed. Try again or use text."
                ) from exc

    @app.post("/api/transcribe", dependencies=[Depends(authorized)])
    async def transcribe(request: Request, language: Literal["en", "hi"] = "en"):
        if request.headers.get("content-type", "").split(";")[0] not in {
            "audio/wav",
            "audio/x-wav",
        }:
            raise HTTPException(415, "Send mono 16-bit PCM audio/wav")
        return await run_speech(app.state.speech.transcribe, await request.body(), language)

    @app.post("/api/speak", dependencies=[Depends(authorized)])
    async def speak(body: SpeakRequest):
        if body.voice_id == "default":
            audio = await run_speech(app.state.speech.synthesize, body.text, body.language)
        else:
            audio = await run_speech(
                app.state.cloned_speech.synthesize,
                body.text,
                body.language,
                body.voice_id,
                require_standard=False,
            )
        return Response(audio, media_type="audio/wav", headers={"X-Voice-ID": body.voice_id})

    @app.get("/api/voices", dependencies=[Depends(authorized)])
    async def voices():
        profiles = await asyncio.to_thread(app.state.voices.list)
        return {
            "cloning": app.state.cloned_speech.status(),
            "voices": [p.public() for p in profiles],
        }

    @app.post("/api/speak/stream", dependencies=[Depends(authorized)])
    async def stream_speech(body: SpeakRequest):
        if body.voice_id == "default":
            if not app.state.speech.ready:
                raise HTTPException(503, "Local speech is not ready")

            def generate(cancel):
                return app.state.speech.stream(body.text, body.language, cancel)
        else:
            if body.language != "en":
                raise HTTPException(
                    422, "Qwen voice cloning supports English; choose Standard for Hindi"
                )
            try:
                await asyncio.to_thread(app.state.voices.get, body.voice_id)
            except KeyError as exc:
                raise HTTPException(
                    404, "That voice no longer exists. Choose another voice"
                ) from exc
            if not app.state.cloned_speech.status()["available"]:
                raise HTTPException(503, "Voice cloning is not installed")

            def generate(cancel):
                return app.state.cloned_speech.stream(
                    body.text, body.language, body.voice_id, cancel
                )

        try:
            # An interrupted stream normally releases its worker at the next
            # audio chunk. Allow that brief cleanup before rejecting a new turn.
            await asyncio.wait_for(app.state.speech_slots.acquire(), timeout=1.0)
        except TimeoutError as exc:
            raise HTTPException(429, "Speech engine is busy. Try again shortly.") from exc
        stream = SpeechStream(generate, app.state.speech_slots.release)
        return StreamingResponse(
            stream.body(),
            media_type="application/x-ndjson",
            headers={"X-Voice-ID": body.voice_id, "X-Accel-Buffering": "no"},
        )

    @app.post("/api/voices/{voice_id}/prepare", dependencies=[Depends(authorized)])
    async def prepare_voice(voice_id: str):
        try:
            ready = await asyncio.to_thread(app.state.cloned_speech.prepare, voice_id)
            return {"ready": ready}
        except KeyError as exc:
            raise HTTPException(404, "That voice no longer exists") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            log.warning("voice_prepare_failed", extra={"reason": type(exc).__name__})
            raise HTTPException(503, "Voice could not be prepared. Try again") from exc

    @app.post("/api/voices", dependencies=[Depends(authorized)], status_code=201)
    async def create_voice(
        name: str = Form(min_length=1, max_length=60),
        transcript: str = Form(min_length=1, max_length=1000),
        audio: UploadFile = File(),
    ):
        if not app.state.cloned_speech.status()["available"]:
            raise HTTPException(503, "Voice cloning is not installed on this server yet")
        try:
            profile = await asyncio.to_thread(
                app.state.voices.create, name, transcript, await audio.read()
            )
            return profile.public()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            await audio.close()

    @app.delete("/api/voices/{voice_id}", dependencies=[Depends(authorized)], status_code=204)
    async def delete_voice(voice_id: str):
        try:
            await asyncio.to_thread(app.state.cloned_speech.delete_voice, voice_id)
        except KeyError as exc:
            raise HTTPException(404, "That voice no longer exists") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(status_code=204)

    @app.get("/")
    async def index():
        return HTMLResponse(
            (static_dir / "landing.html").read_text().replace("/static/", asset_prefix + "/")
        )

    @app.get("/signup")
    @app.get("/login")
    async def account_page():
        return HTMLResponse(
            (static_dir / "auth.html").read_text().replace("/static/", asset_prefix + "/"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/verify")
    @app.get("/forgot")
    @app.get("/reset")
    async def recovery_page():
        return HTMLResponse(
            (static_dir / "recovery.html").read_text().replace("/static/", asset_prefix + "/"),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.get("/app")
    async def workspace_page(request: Request):
        try:
            await current_session(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(
            (static_dir / "portal.html").read_text().replace("/static/", asset_prefix + "/"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/internal")
    async def operator_page():
        return HTMLResponse(
            (static_dir / "operator.html").read_text().replace("/static/", asset_prefix + "/"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/testing")
    async def testing_page(request: Request):
        try:
            await authorized(request)
        except HTTPException:
            return RedirectResponse("/internal", status_code=303)
        return HTMLResponse(
            (static_dir / "index.html").read_text().replace("/static/", asset_prefix + "/"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/business")
    async def business_dashboard(request: Request):
        try:
            await authorized(request)
        except HTTPException:
            return RedirectResponse("/internal", status_code=303)
        return HTMLResponse(
            (static_dir / "business.html").read_text().replace("/static/", asset_prefix + "/"),
            headers={"Cache-Control": "no-store"},
        )

    # Relative ES-module imports keep the same build prefix, so a cached older
    # player cannot be mixed with new voice controls after an application update.
    app.mount(asset_prefix, StaticFiles(directory=static_dir), name="versioned_static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


handler = logging.StreamHandler()
handler.setFormatter(JSONLogFormatter())
logging.getLogger("src").addHandler(handler)
logging.getLogger("src").setLevel(logging.INFO)
app = create_app()
