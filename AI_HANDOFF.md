# AI Handoff

State-of-work snapshot for switching models or sessions. This file records **status, decisions, and open work** — not a second architecture manual.

- Deep feature/behaviour documentation: [`README.md`](README.md)
- Module graph, god nodes, communities, cross-file paths: `graphify query "<question>"` / `graphify path` / `graphify explain` over `graphify-out/`
- Agent/tooling rules: [`AGENTS.md`](AGENTS.md)

Last updated: 2026-11-06 (after rebuilding the graphify knowledge graph).

---

## 1. Project objective

**Listen** is a self-service voice support agent for small businesses (restaurants, clinics, support teams). Each business creates its own workspace, supplies its own knowledge base and voice, connects an Exotel phone flow, and gets calls answered from **its own** content and voice. The original English/Hindi **insurance policy playground** remains as an operator-only testing surface.

Core constraints that shape the product:

- Retrieval and speech run **locally**; only question text/source passages go to a hosted answer model, and only when configured.
- No-key `extractive` mode must remain usable (copies source clauses).
- One business must never see another business's knowledge, voice, or calls.

## 2. Current architecture

Single-process FastAPI app (`src.app:create_app`), browser UI served by the same process, one Uvicorn worker so models load once. No frontend build step.

| Layer | Modules | Responsibility |
| --- | --- | --- |
| HTTP / composition root | `src/app.py`, `src/config.py` | App factory, middleware, routes, auth dependencies, static serving, lifespan wiring |
| Accounts | `src/accounts.py`, `src/google_auth.py`, `src/mailer.py` | Signup/login, sessions + CSRF, email verification/reset, Google OIDC |
| Tenancy | `src/businesses.py` | Business workspaces, per-business knowledge snapshots/versioning, per-call `Assistant` construction |
| Answer pipeline | `src/assistant.py`, `src/rag_retrieval.py`, `src/rag_engine.py`, `src/chunks.py`, `src/embeddings.py`, `src/query_scope.py` | Query planning, hybrid retrieval, reranking, grounded generation, source recovery, caching |
| Knowledge ingestion | `src/documents.py`, `scripts/convert_document.py` | `LABEL: text` / FAQ JSON loading; offline PDF/DOCX/TXT/MD conversion |
| Speech | `src/speech.py`, `src/speech_text.py`, `src/audio_stream.py`, `src/cloned_speech.py`, `src/clone_audio.py`, `src/voices.py` | Whisper ASR, Kokoro TTS, sentence/streaming pipeline, Qwen3-TTS worker IPC, voice profiles |
| Telephony | `src/telephony.py` | Exotel Voicebot WebSocket: greeting, VAD/barge-in, turn-taking, DTMF transfer, bounded concurrency |
| Browser UI | `static/*` | Landing, signup/login/recovery, business portal, operator console, voice studio, recorder worklets |
| Ops | `run.sh`, `setup.sh`, `scripts/setup_*.py`, `scripts/smoke_*.py` | Start, one-command install, model installs, integration checks |

Persistent state:

- `runtime/businesses/businesses.sqlite3` — businesses, users, sessions, email tokens, OAuth state, per-business knowledge JSON; schema `user_version = 5`, additive migrations.
- `runtime/knowledge.sqlite3` — derived retrieval index (FTS5 + embeddings); rebuildable.
- `runtime/voices/` — saved reference audio/profiles; must be preserved.
- `models/` — downloaded local model weights.

## 3. Completed features

- **Self-service tenancy**: signup, login, onboarding with resumable progress, one workspace per account, tenant-scoped access.
- **Auth**: opaque revocable sessions, CSRF, per-IP/email throttling, email verification, one-time password reset, Google sign-in and account linking (tokens not persisted).
- **Business knowledge**: CRUD, 40-entry cap, per-entry English/Hindi answers, `.txt` import (24 KB, sectioned, replace-by-filename), optimistic versioning.
- **Grounded answers**: local FTS5 + multilingual E5 hybrid retrieval, reciprocal rank fusion, BGE cross-encoder reranking, sentence-aware chunking, bounded context packing, source-ID validation, bounded missing-source recovery, five-minute version-keyed cache.
- **Providers**: `extractive` (no key), `ollama` (local), `groq` (recommended, Qwen 3.8 27B); provider/model reported by `/api/status`.
- **Speech**: local faster-whisper ASR, Kokoro ONNX English/Hindi TTS, incremental PCM streaming, playback interruption, sentence preparation for prosody.
- **Custom voice**: isolated Qwen3-TTS 1.7B MLX worker (Apple Silicon), reference recording/upload, prepare/preview/delete, per-voice caches, English-only cloned speech.
- **Phone agent**: Exotel bidirectional Voicebot over WSS, stream-key auth, destination/format checks, greeting, speech-level barge-in, DTMF `0` transfer, `MAX_PHONE_CALLS` limit, 600-second call cap.
- **Operator surface**: `/internal` sign-in, `/testing` insurance playground, `/business` operator console, voice studio.
- **Document conversion**: offline PDF/DOCX/TXT/MD → business text or policy `LABEL:` format.
- **One-command setup**: `setup.sh` provisions uv/Python, envs, models, optional clone-from-GitHub, preserves `.env` and runtime data.
- **Tests**: 132 test functions across 18 test files + fixtures for retrieval/RAG/scope; optional smoke scripts for browser, voice, cloning, streaming.

## 4. Current implementation status

- **HEAD**: `5871ac4 Add one-command laptop setup` on `main`. Working tree has **no in-progress source edits**.
- Last feature work shipped the self-service platform (`35eeb0b`) and then the installer (`5871ac4`); the codebase is at a stable checkpoint, not mid-refactor.
- **This checkout is not provisioned**: no `.venv-local/`, `.venv-qwen/`, `models/` or `runtime/` exists here, so the full test suite and live models were **not run** in this session.
- Checks that were run at handoff: `python3 -m compileall -q src scripts tests` ✅, `sh -n run.sh` ✅, `sh -n setup.sh` ✅.
- Graphify graph rebuilt: 882 nodes, 2,413 edges, 38 communities. The pre-build diagnostic reported **414 dangling-endpoint edges, 2 self-loops, 150 collapsed groups** (see §7).
- Product status per README: **pilot**, not a production-ready multi-customer deployment.

## 5. Important technical decisions

1. **Local-first, optional hosted LLM.** Retrieval/speech never leave the machine by default; Groq is opt-in. Extractive fallback must always work without an API key.
2. **No LangChain, no hosted vector DB.** SQLite (FTS5 + embedding BLOBs) + ONNX E5/BGE keeps deployment small and offline-capable; the index is disposable.
3. **One process / one worker.** Models are loaded once into app state; multiple workers would duplicate multi-GB models and voice workers.
4. **Tenant isolation by construction.** Every phone call and business `/ask` builds an `Assistant` from that business's stored knowledge snapshot, never from the global policy KB.
5. **Voice cloning is a separate environment and subprocess.** MLX/Qwen dependencies are isolated in `.venv-qwen`; the main app talks to a warm worker over bounded IPC. Apple Silicon only, English only.
6. **Streaming is bounded and cancellable.** Sentence-level synthesis, bounded PCM buffers, cancel at chunk boundaries, never replay already-heard audio or cache partial replies.
7. **Answers are evidence-gated.** Pydantic schemas forbid unsupported answers without source IDs; at most one bounded recovery call; unsupported questions refuse rather than guess.
8. **Explicit budgets.** 300-token query-preparation cap, 600-token answer cap, 15-second pipeline deadline, 160-word chunks / 24-word overlap, 16 rerank candidates, 5 result groups, 8 sources, 5,000 context chars.
9. **Security defaults.** Operator-token gate for internal/phone APIs, `TrustedHostMiddleware` when no token, same-origin checks, CSP, `no-store` on APIs, bounded request bodies, content-hashed static URLs, OAuth state bound to the initiating browser.
10. **Additive SQLite migrations.** Schema bumps preserve existing pilot rows and data; `user_version` guards forward compatibility.
11. **Pinned dependency profiles.** `base`/`retrieval`/`voice`/`documents`/`qwen-mlx` with lock files; keep environments separate.
12. **No call/transcript persistence.** Audio is processed in memory; only saved cloning references live on disk. Hosted-model use must be disclosed for privacy-sensitive deployments.

## 6. Files modified

### Working tree at handoff (uncommitted)

- `AI_HANDOFF.md` — this file (new).
- `graphify-out/` — rebuilt graph outputs: `graph.json`, `GRAPH_REPORT.md`, `graph.html`, `manifest.json`, `cost.json`, labels/build state, AST + semantic caches, new `.graphify_python`.
- `.opencode/` — untracked OpenCode skills (graphify).

### Last commit — `5871ac4 Add one-command laptop setup`

- `setup.sh` (new), `tests/test_setup_script.py` (new), `README.md`, `.gitignore`.
- `graphify-out/*` regenerated.

### Previous commit — `35eeb0b Add self-service voice agent platform`

- New backend: `src/accounts.py`, `src/businesses.py`, `src/google_auth.py`, `src/mailer.py`, `src/telephony.py`.
- Extended: `src/app.py`, `src/assistant.py`, `src/config.py`.
- New converter: `scripts/convert_document.py`.
- New UI: `static/auth.{html,js}`, `static/business.{html,js,css}`, `static/portal.{html,js,css}`, `static/recovery.{html,js}`, `static/operator.*`, `static/google-g.svg`, `static/landing.html`, `static/index.html`, `static/saas.css`.
- New tests: `test_businesses.py`, `test_document_conversion.py`, `test_google_auth.py`, `test_saas.py`; updates to `conftest.py`, `test_api.py`, `test_policy.py`, `test_voice_cloning.py`.
- Dependency locks/definitions under `requirements/`.

## 7. Outstanding bugs and open risks

No confirmed open code bugs are tracked in the repo: there are no `TODO`/`FIXME`/`HACK` markers and no `xfail` tests (only `importorskip` for optional numpy/scipy). The following are **known limitations and risks** that should be validated before calling the pilot production-ready:

1. **Test suite not verified in this checkout** — dependencies are absent. Highest-priority action for the next session: install and run `pytest` + `ruff` (commands in §9).
2. **Graphify integrity warning** — the rebuild reports 414 dangling-endpoint edges, 2 self-loops, and 150 collapsed edge groups in the raw extraction. The built graph is usable, but inferred cross-file edges should not be trusted blindly.
3. **Live-answer quality not verified here** — Groq/Ollama calls and the live RAG evaluation require keys/models and consume quota; generated answers can still make mistakes even when citations validate.
4. **Apple Silicon lock-in** — custom voice cloning requires MLX on Apple Silicon. Other hardware falls back to standard Kokoro speech and cannot run business phone calls with the custom voice engine.
5. **Per-process voice warm-up** — saved voices must be prepared again after every server restart; calls are rejected until the business voice is ready. Cold start adds latency.
6. **Phone ownership is not verified automatically**; Exotel numbers are entered manually. A wrong number can be attached to a workspace.
7. **Pilot capacity** — default `MAX_PHONE_CALLS=2`; speech latency/capacity has not been benchmarked beyond that. Use one server process.
8. **Ingestion limits/gaps** — business knowledge is `.txt`/manual only (no in-app PDF import; converter is offline), 40 entries max, 24 KB/file, 1,200 chars/section. No OCR; scanned PDFs and DOCX footnotes need manual review. The original policy playground loads one KB file at a time.
9. **Scope gaps** — cloned speech is English-only; voice design from a description is not implemented; booking, order placement, billing, number provisioning, and persistent call transcripts are not implemented.
10. **Operational gaps** — no backups/observability/rate-limit dashboard; call and transcript persistence must be handled by the provider or added deliberately. Review healthcare/privacy requirements before real clinic use; the clinic guard is not a medical safety system.
11. **Hosted-model privacy** — questions, bounded history, selected source passages, and topic headings go to Groq when enabled; microphone audio and voice references stay local.

## 8. Next steps

Priority order for the next session/model:

1. Provision an environment and run the full gate:
   `./setup.sh --no-start` (Apple Silicon) or the manual text-only path, then `pytest -q` and `ruff check src scripts tests`.
2. Re-run offline retrieval/RAG evaluation and, if a key is available, the live Groq evaluation after any model/prompt change (`scripts/evaluate_rag.py --live ...`).
3. Review the graphify warning and decide whether to commit the rebuilt `graphify-out/` (large expected diff) along with `AI_HANDOFF.md`.
4. Verify `setup.sh` on a genuinely clean Apple Silicon machine (fresh clone, no `.env`, no models) and confirm preservation on re-run.
5. Production hardening before real customers: phone-number ownership verification, backup/restore for `runtime/`, structured metrics/alerting, and a capacity benchmark above two concurrent calls.
6. Decide the next product slice from the explicit gaps: in-app PDF ingestion, Hindi cloned speech, or persistent call records/analytics.

## 9. Build and test commands

### Install / run (Apple Silicon, full voice)

```sh
./setup.sh              # one-command install + start (downloads several GB)
./setup.sh --no-start   # install only
./run.sh                # later starts; VOICE_ENABLED=true by default
PORT=8001 ./run.sh      # alternate port
```

### Minimal text-only

```sh
uv venv --python 3.12 .venv-local
uv pip install --python .venv-local/bin/python -r requirements/base.lock
RETRIEVAL_MODE=lexical VOICE_ENABLED=false ./run.sh
```

Semantic retrieval: install `requirements/retrieval.lock`, run `scripts/setup_retrieval.py`, then `VOICE_ENABLED=false ./run.sh`.

### Tests and lint

```sh
uv pip install --python .venv-local/bin/python -r requirements/dev.txt
.venv-local/bin/python -m pytest -q
.venv-local/bin/ruff check src scripts tests
```

This checkout cannot run these until an environment is created; `python3 -m compileall -q src scripts tests` passes.

### Evaluation

```sh
# Offline context recall (no API calls)
.venv-local/bin/python scripts/evaluate_rag.py

# Live Groq answers (consumes quota; pacing reduces 429s)
.venv-local/bin/python scripts/evaluate_rag.py --live --interval 35 --compare-legacy
.venv-local/bin/python scripts/evaluate_rag.py --live --interval 35 \
  --cases tests/fixtures/rag_scope_cases.json --report runtime/rag-scope-evaluation.json

# Original extractive retrieval fixtures
.venv-local/bin/python scripts/evaluate_retrieval.py
.venv-local/bin/python scripts/evaluate_retrieval.py --cases tests/fixtures/retrieval_holdout.json --report runtime/retrieval-holdout.json
.venv-local/bin/python scripts/evaluate_retrieval.py --cases tests/fixtures/retrieval_challenge.json --report runtime/retrieval-challenge.json
```

### Optional integration checks

```sh
.venv-local/bin/python scripts/smoke_voice.py        # local TTS -> STT -> retrieval
.venv-local/bin/python scripts/smoke_ui.py           # browser flow, stub speech
.venv-local/bin/python scripts/smoke_clone_ui.py     # browser voice-manager flow
.venv-local/bin/python scripts/smoke_streaming_ui.py # streaming/interrupt/replay
.venv-local/bin/python scripts/smoke_qwen.py         # real cloning against running server
```

Playwright checks need `playwright` in `.venv-local` plus `python -m playwright install chromium`.

### Knowledge graph

```sh
graphify query "<question>"     # scoped context before reading source
graphify path "<A>" "<B>"       # relationships
graphify explain "<concept>"    # focused concept
graphify update .               # after code changes (AST-only, no API cost)
```

## 10. Model-switch protocol

`AGENTS.md` now makes this file the source of truth for task continuity: inspect it before continuing an existing task, use the graph instead of re-exploring the repo, and verify claims against actual files and Git rather than trusting the previous model.

When asked to "prepare for switching models", update this file **before** handing off:

1. Refresh §4 status (HEAD, branch, what is provisioned, checks actually run).
2. Refresh §6 with the real diff: `git status --porcelain` and recent commit file lists.
3. Update §7 with any confirmed bugs, failing tests, or new risks — mark confirmed bugs separately from limitations.
4. Rewrite §8 next steps in priority order, removing completed work.
5. Re-run the fastest available gate (`compileall`, then `pytest`/`ruff` if an environment exists) and record the exact result.
6. Do not copy README or graphify output into this file; link to them instead.
