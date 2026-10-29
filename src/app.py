import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.assistant import Assistant
from src.audio_stream import SpeechStream
from src.cloned_speech import ClonedSpeech
from src.config import ROOT, Settings
from src.models import AskRequest, SpeakRequest
from src.speech import SpeechService
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
        app.state.speech = speech or SpeechService(settings)
        app.state.voices = VoiceStore(settings.voices_dir)
        app.state.cloned_speech = cloned_speech or ClonedSpeech(settings, app.state.voices)
        app.state.voice_error = None
        app.state.speech_slots = asyncio.Semaphore(1)
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
        # Bound streamed bodies as well as declared Content-Length.
        limit = (
            3 * 1024 * 1024 if request.url.path in {"/api/transcribe", "/api/voices"} else 32 * 1024
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
        response.headers["Referrer-Policy"] = "same-origin"
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
        if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (
            request.url.scheme,
            request.url.netloc,
        ):
            raise HTTPException(403, "Cross-origin requests are not allowed")
        token = settings.api_token.get_secret_value()
        if token:
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
                raise HTTPException(401, "A valid API token is required")
        elif not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "Local access only; configure API_TOKEN for remote access")

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
            (static_dir / "index.html").read_text().replace("/static/", asset_prefix + "/")
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
