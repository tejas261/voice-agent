# Listen — voice support for businesses

Listen lets restaurants, clinics, and support teams create a workspace, add their own knowledge and custom voice, and connect an Exotel phone flow. Each business signs up separately and its workspace and calls use its own information and voice. The original English/Hindi insurance playground remains available to operators for testing.

Retrieval and speech run locally. For natural answers to unfamiliar paraphrases, the recommended setup uses **Qwen 3.8 27B on Groq**, with low reasoning effort and a small, bounded source context. A no-key mode can copy matching clauses locally. The interface supports typed questions, push-to-talk, a voice-only toggle, playback interruption, and recording a voice-cloning reference.

## Run locally

Use **Python 3.12** and [uv](https://docs.astral.sh/uv/). From the project directory:

```sh
uv venv --python 3.12 .venv-local
uv pip install --python .venv-local/bin/python -r requirements/voice.lock
.venv-local/bin/python scripts/setup_models.py
./run.sh
```

Open **http://127.0.0.1:8000**, create an account, and follow the workspace setup. Local accounts do not require email verification unless configured below. To use the original testing screen, set `API_TOKEN` to a strong secret, restart, and sign in at **http://127.0.0.1:8000/internal**; the playground is at `/testing`. Allow microphone access there. Recordings stop after 20 seconds.

If the environment and models are already installed, just run `./run.sh`. Use `PORT=8001 ./run.sh` for another port. Keep one server worker so models are loaded only once. The browser interface is served by FastAPI; no separate frontend server or build is needed.

Use [.env.example](.env.example) as a reference for your `.env`; keep existing keys and settings. Environment variables override `.env`. To enable the recommended answer model, set:

```dotenv
LLM_PROVIDER=groq
GROQ_API_KEY=your-key
GROQ_MODEL=qwen/qwen3.8-27b
```

Restart after changing configuration. Without these settings, the default `extractive` mode needs no API key and returns source clauses directly, with more limited handling of unfamiliar phrasing. `GET /api/status` reports the active provider and model.

Groq lists Qwen 3.8 27B at **$0.80 per million input tokens and $4.00 per million output tokens**. A turn using 2,200 input and 450 output tokens across query preparation and answering costs about **$0.00356**, or **$3.56 per 1,000 turns**, before any free quota or caching. Output usage includes reasoning tokens; usage varies with language, history and source recovery. Groq currently lists this model as a preview; check [its model page](https://console.groq.com/docs/model/qwen/qwen3.8-27b) for current pricing and availability. Questions, bounded conversation history, selected source passages, and a compact topic index are sent to Groq. Microphone recordings and cloned voice references stay local.

For a minimal text-only installation, install `requirements/base.lock` instead of `requirements/voice.lock`, then run:

```sh
RETRIEVAL_MODE=lexical VOICE_ENABLED=false ./run.sh
```

For text with semantic retrieval, install `requirements/retrieval.lock`, run `scripts/setup_retrieval.py`, and start with `VOICE_ENABLED=false ./run.sh`.

## Voice cloning

The optional cloning worker uses Qwen3-TTS through MLX Audio and requires an **Apple Silicon Mac**. Install it in its own environment to keep its dependencies separate from the main app:

```sh
uv venv --python 3.12 .venv-qwen
uv pip install --python .venv-qwen/bin/python -r requirements/qwen-mlx.lock
.venv-local/bin/python scripts/setup_qwen.py
./run.sh
```

In a business workspace, choose **Your voice**, then record or upload 3–20 seconds of clear English speech, enter its exact transcript, confirm the voice owner's permission, and save it. Select **Prepare for calls** and preview it. The internal playground has a separate voice studio for testing.

Speech uses the selected neural voice throughout the reply. The Indian/American/British macOS presets have been removed: they switched speakers and did not meet the conversational-quality goal. Saved clones keep the identity and accent from their recording. For new samples, speak naturally as if helping one customer; the reference's pacing, expression, and recording quality affect the clone.

The configured cloning model, sentence handling, prosody settings, and streaming pipeline apply to **every saved and newly created cloned voice**. Only the selected reference recording and transcript change. Existing voices do not need to be cloned again after an engine upgrade; their reference conditioning and reply caches remain isolated by voice ID.

The default cloning model is [Qwen3-TTS 1.7B Base, MLX 8-bit](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit), about 3.1 GB including its speech tokenizer. It runs locally with no speech API fees. Existing references are reused. The previous 0.6B model is left intact if installed; `QWEN_MODEL_DIR=models/qwen3-tts-base` can select it on a smaller machine. Run `scripts/setup_qwen.py` to install the new default into `models/qwen3-tts-natural`.

Cloned replies are enabled for **English** in this app. Hindi uses the standard Kokoro voice. Voice design from a text description is not implemented. Selecting or restoring a saved voice prepares its model and reference in the background, and the worker stays warm. Cold preparation still takes several seconds; selecting a voice before asking avoids putting that work after the answer.

The model receives complete sentences for coherent phrasing, while the 1.7B model streams audio from within each sentence. Playback does not wait for the sentence or full reply to finish. Commas, semicolons, questions, and parentheticals stay inside the utterance: the neural model controls their rhythm and emphasis. The app no longer restarts synthesis every 18 words or inserts fixed silence after every punctuation mark. Short leads such as “Yes.” stay with the following explanation. Only unusually long sentences are split, at a maximum of 48 words with preference for existing punctuation and clause boundaries. Decimal values, numeric separators, and common abbreviations are preserved; mixed letter/number identifiers and percent signs are normalized for pronunciation.

Cloned speech uses sampled decoding for varied intonation. The 1.7B streaming path checks audio with a brief lookahead (about 1.44 seconds of generated sound) and retains a trailing chunk for silence trimming. A failed attempt can retry once with deterministic decoding before playback starts. If generation fails after audio has started, the stream stops with an error; it never repeats an already-heard sentence or caches an incomplete reply. Quiet outer margins are trimmed while preserving internal timing and up to 240 ms of trailing pause. Audio is delivered in packets of up to 0.48 seconds. The optional small model and complete-WAV endpoint retain whole-sentence validation. Cold model loading and RAG generation still add to first-response latency.

The answer model is prompted to sound like an approachable insurance agent on a call: give the answer first, explain it in everyday English or Hindi, and usually use one to three short sentences. Broad questions get a simple overview rather than a list of policy features. Material conditions, exceptions, amounts, and evidence validation still take priority over brevity. Source-only fallbacks retain their original wording.

The browser schedules chunks continuously with a short playback buffer. Interrupting a reply cancels inference at the next generation chunk boundary, keeps the cloned model loaded, and discards the unfinished audio. Completed replies retain the bounded replay cache. The voice-studio preview and the original `/api/speak` endpoint still return complete WAV files. Frontend files use content-versioned URLs so refreshing after a restart loads the current player and matching voice controls.

Saved references and profiles live in `runtime/voices/`. Keep this directory when cleaning or moving the project. Deleting a voice through the interface removes its stored reference and cached replies.

## How answers work

| Stage | Implementation |
| --- | --- |
| Speech recognition | Local faster-whisper `small`, CPU INT8 |
| Query preparation | Groq Qwen 3.8 27B: check product scope and rewrite for search |
| Candidate retrieval | SQLite FTS5 + multilingual E5 Small, combined using reciprocal rank fusion |
| Reranking | Local BGE v2 M3 cross-encoder, INT8 |
| Recommended answer | Groq Qwen 3.8 27B, JSON validated against retrieved source IDs |
| No-key answer | Original source clauses or current Hindi translations |
| Standard speech | Local Kokoro ONNX for English/Hindi |
| Optional cloned speech | Qwen3-TTS 1.7B Base, MLX 8-bit |

With generation enabled, all questions go through the same retrieval and answer pipeline:

1. In Groq mode, use a short model request to check whether the question concerns this product and rewrite it for search. It resolves follow-ups, translates Hindi search wording into English, and preserves the subject, event, negation and numbers. This step sees product scope, available topic headings and bounded history, without retrieved clauses that could bias an unrelated question toward a similar-looking rule.
2. Split long clauses into sentence-aware windows of at most **160 words**, with **24 words of overlap**; short clauses remain intact. Combine chunk similarity, keyword rankings, and existing FAQ-example similarity using reciprocal rank fusion. Reuse the query embedding and rerank at most **16 candidate topics** against their best matching chunks.
3. Pack up to **5 result groups**, **8 original sources**, and **5,000 characters** of source context. Expand matches to their original clauses and required supporting clauses, then deduplicate by source ID. A bundle that cannot fit is skipped rather than silently cutting off its conditions; keep input clauses reasonably short.
4. Ask the model to answer the original question conversationally, usually in one to three short sentences (up to four when needed), preserving conditions and citing supplied sources. Search rewrites are not treated as customer facts or answer evidence. Unlike extractive mode, word-overlap gates do not reject a paraphrase before the answer model sees the evidence.
5. If a relevant section is missing, the model can request up to three IDs from a compact topic index. Fetch their actual text and make **one** additional model call. Topic titles alone cannot be cited as evidence. This recovery is bounded to the first 64 omitted topics, suitable for the current small corpus.
6. Validate the response structure and source IDs; unsupported questions receive a refusal. Groq mode normally takes two model stages (query preparation and answering), or at most three with source recovery. Unrelated questions can finish after the scope check. Known private-record requests are rejected before calling the model.
7. Repeated requests reuse a five-minute cache keyed by the question, language, history, and knowledge-base version. Cached answers make no API calls.

Query preparation has a 300-token completion cap; answer generation defaults to 600 tokens. These caps include reasoning. Qwen query preparation disables reasoning, while answer generation uses low reasoning effort to help compare conditions and preserve exceptions. The complete query/retrieval/generation/recovery sequence has a fifteen-second deadline. Spoken answers stay within four short sentences and 1,200 characters. Local embedding caches persist in SQLite; no hosted vector database or paid retrieval service is needed. Valid citations establish where evidence came from, but do not guarantee that every generated statement is correct.

Free-tier quotas can limit rapid exchanges: the development account encountered a limit of 1,000 output tokens per minute. Each model stage retries a 429 once only when the provider asks for a wait of ten seconds or less, within the same fifteen-second deadline. Model/network/quota failures use the conservative source-answer fallback and are not cached. If that fallback has no reliable source, the app reports temporary service unavailability instead of claiming that the knowledge base lacks an answer.

The knowledge base contains product information, not customer account records. Requests for personal identifiers, actual claim status, or undocumented information may require support. Regression fixtures check changes across topics and languages; generated answers can still make mistakes.

`LLM_PROVIDER=ollama` uses local generation and source recovery with a separately running server and installed model; it skips the hosted query-preparation step. `LLM_PROVIDER=extractive` keeps the original strict FAQ/source matching path. Semantic retrieval requires the retrieval models; lexical-only installations have more limited paraphrase recall. See `.env.example` for adapter settings. Re-run the live fixtures when changing the answer model.

## Knowledge base

The original playground uses the following policy files:

- [`data/policy_booklets.txt`](data/policy_booklets.txt): original English clauses in `LABEL: text` format, grouped by `SECTION:` headings.
- [`data/policy_booklets.answers.json`](data/policy_booklets.answers.json): question examples, spoken English wording, Hindi translations, and links between clauses.

The current corpus has 32 original clauses and one derived overview. The original clauses remain the retrieval evidence and citations. Extractive answers and model-failure fallbacks use `voice_answer_en` when available; Hindi uses `answer_hi`. Both are used only when `source_sha256` matches the SHA-256 of the exact stripped source line, otherwise the app uses current English text. `source_ids` compose an overview from originals, and `context_ids` attach supporting clauses to an answer. An overview's optional `voice_answer_en` is used only when `source_sha256_by_id` matches every referenced source line. Review and update the wording and hashes together after changing a clause.

After editing either file, restart the server. The derived index updates automatically, reusing unchanged embeddings. Update linked IDs when deleting or renaming clauses. These excerpts are the app's supplied evidence, not a live feed of insurer policy changes.

You can set `KB_PATH` to another structured TXT/Markdown file or a JSON list of FAQs. JSON entries require `id`, `title`, `questions`, and `answer_en`; `answer_hi` is optional. Arbitrary PDF ingestion is not implemented. The small shop corpus under `tests/fixtures/` is test data.

### Add a policy

The original playground loads **one knowledge-base file at a time**; it does not automatically combine or distinguish multiple policy files. To switch its policy:

1. Transcribe the original policy into a UTF-8 file such as `data/new_policy.txt`. Put the product name and type before the first section, and each clause on one line with a unique uppercase label. Include the relevant conditions and source references. PDF files are not ingested directly. For example (fictional text):

   ```text
   PRODUCT_NAME: Example Life Plan
   PLAN_TYPE: Individual life insurance
   SECTION: ELIGIBILITY
   AGE_AT_ENTRY: Applicants must be at least 18 years old.
   ```

2. Optionally create `data/new_policy.answers.json` alongside it. Keys are lowercase clause labels; add question examples, conversational English and Hindi only when they match the source. For the example above:

   ```json
   {
     "age_at_entry": {
       "questions": ["How old must I be to join?"],
       "voice_answer_en": "You must be at least 18 to join.",
       "answer_hi": "पॉलिसी लेने के लिए आपकी उम्र कम से कम 18 साल होनी चाहिए।",
       "source_sha256": "9836a082bbcc649abf5bff5707efb0ccfff6630825d4fd1995a0b9777d4e63f5"
     }
   }
   ```

   Calculate the hash from the exact source line, including its label and any references, with leading and trailing whitespace removed:

   ```sh
   python3 - <<'PY'
   import hashlib
   from pathlib import Path

   line = next(line.strip() for line in Path('data/new_policy.txt').read_text(encoding='utf-8').splitlines()
               if line.strip().startswith('AGE_AT_ENTRY:'))
   print(hashlib.sha256(line.encode('utf-8')).hexdigest())
   PY
   ```

3. Set `KB_PATH=data/new_policy.txt` in `.env`, then restart the server (`Ctrl+C` followed by `./run.sh` if it runs in your terminal). The index rebuilds automatically. Ask a few questions through the app and check the answers and cited source text, including conditions, exclusions and unsupported questions.

### Update a policy

1. Edit the relevant source line in the active TXT/Markdown file. Keep labels unique, clauses on one line, and the source wording and references accurate.
2. Review the matching entry in the `.answers.json` file. Update question examples, `voice_answer_en` and `answer_hi` as needed, then recalculate `source_sha256` for the changed line using the command above, replacing the filename and label. Until the hash matches, the app uses current English source text instead of the old spoken wording or translation.
3. If a changed clause appears in an overview with `source_sha256_by_id`, review its spoken overview and update that clause's hash there too. When renaming or removing a clause, update every `context_ids`, `source_ids` and `source_sha256_by_id` reference to it.
4. Restart the server and check representative answers and citations. The index refreshes on startup; there is no need to edit it by hand.

### Convert a document for the knowledge base

Install the optional converter dependencies, then convert a PDF, DOCX, TXT or Markdown document locally:

```sh
uv pip install --python .venv-local/bin/python -r requirements/documents.lock
.venv-local/bin/python scripts/convert_document.py clinic-guide.pdf --output clinic-guide.txt
```

Upload the resulting `.txt` under **Knowledge → Import text** in a business workspace. Large outputs are split into numbered files of at most 24 KB. Review all extracted text, especially tables and conditions, before importing; the converter does not rewrite or summarize it. A workspace currently allows 40 entries in total, including existing entries, so a large document may need to be reduced or divided among workspaces. Re-importing the same filename replaces its earlier sections; if the output filenames change, remove obsolete sections manually.

For the original policy playground, generate its `LABEL: text` format instead:

```sh
.venv-local/bin/python scripts/convert_document.py policy.docx --format policy --output data/new_policy.txt
```

Set `KB_PATH=data/new_policy.txt`, restart, and test the result. The converter reads selectable PDF text and DOCX paragraphs and tables. It does not perform OCR; scanned pages, text in images, and DOCX footnotes/endnotes need separate review. Headers and footers are appended with labels. Layout and reading order can differ from the original document.

## Self-service business setup

1. Sign up at `/signup`, then create a workspace with the business name and type. Sign back in at `/login`; setup progress is saved. Each account can create one workspace, and the account session only grants access to that workspace.
2. Under **Knowledge**, add customer questions and answers or import a UTF-8 `.txt` file. Separate sections with blank lines; files are limited to 24 KB and each section to 1,200 characters. Importing the same filename replaces its earlier sections. Save edits and try representative questions under **Test answers**. PDF import is not supported.
3. Under **Your voice**, record or upload a 3–20 second mono PCM WAV sample, enter the exact transcript, and confirm permission from the voice owner. Save it, select **Prepare for calls**, and preview it. Preparing must be repeated after a server restart. Calls are rejected until this custom voice is ready; Exotel does not supply the agent's voice. This local cloning engine requires Apple Silicon and currently supports **English only**.
4. Under **Phone connection**, enter the ExoPhone destination number exactly as Exotel sends it in the stream's `start.to` field. Generate and save the connection URL; the key is shown only once. Generating a new key invalidates the old one for new calls. Adding the number alone does not verify the connection.
5. Configure a public HTTPS/WSS reverse proxy with a valid TLS certificate. In Exotel, enable the **bidirectional Voicebot applet**, enter the generated URL (replace `YOUR_PUBLIC_HOST` if generated locally), and use 8 kHz mono PCM. Exotel sends its URL credentials as HTTP Basic authentication. Set the next applet to route to staff or voicemail; pressing 0 or requesting a person ends the stream so Exotel can continue there. Test the call flow before forwarding a real business line. See the [Exotel Voicebot protocol](https://support.exotel.com/support/solutions/articles/3000108630-working-with-the-stream-and-voicebot-applet).

Set `VOICE_ENABLED=true` and a strong `API_TOKEN` for the phone integration. The internal playground and operator console (`/business`) are behind the operator sign-in at `/internal` and are not linked from the public site.

### Public sign-up configuration

For an HTTPS deployment, set `PUBLIC_ORIGIN` to the exact public origin (for example, `https://listen.example`), configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` and, if required, `SMTP_USERNAME` and `SMTP_PASSWORD`. The relay must support STARTTLS. Public sign-up requires email verification, and password reset uses the same relay. Sign-up fails if verification cannot be delivered. Without these settings, localhost sign-up is intended for development and password recovery is unavailable; `REQUIRE_EMAIL_VERIFICATION=true` also enables verification locally when the relay and public origin are configured.

### Google sign-in

1. In [Google Auth Platform](https://console.cloud.google.com/auth/clients), configure the consent screen and create an OAuth **Web application** client. Add the exact redirect URI `http://127.0.0.1:8000/api/auth/google/callback` for local testing. For a public deployment, register `https://your-domain/api/auth/google/callback` and set `PUBLIC_ORIGIN=https://your-domain`. Add test users if the consent screen is in testing mode.
2. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` in `.env`, using the matching registered URI, then restart. Open the app at the same host and port as the callback. The Google button appears on sign-in and sign-up when configured.
3. A verified Gmail or Google Workspace address can sign in to an existing verified account with the same email. Other Google accounts can be connected after signing in with email and password using **Connect Google** in the workspace. New Google accounts create their own workspace through the usual onboarding. Google identities are stored by their stable account ID; Google access and ID tokens are not saved.

See Google's [web-server OAuth setup](https://developers.google.com/identity/protocols/oauth2/web-server) for consent-screen publishing and redirect URI requirements. An actual Google client is needed to test the live sign-in; the application does not create one automatically. Configure any reverse proxy or external access logger to omit the callback query string, which contains a one-time authorization code.

This is a **pilot**, not a production-ready multi-customer deployment. Phone ownership is not automatically verified, and local voice workers must be prepared on the serving process. The default limit is two simultaneous phone sessions; benchmark speech latency and capacity on your hardware. Use one server process for this pilot. Configure TLS, network access and backups appropriately before exposing it. Calls and transcripts are not persisted by this app; provider recording settings are separate. Local workspace knowledge and voice samples are stored under `BUSINESSES_DIR`; old knowledge snapshots remain there after updates. Hosted answer models receive the question and retrieved text. Review privacy and applicable healthcare requirements before using real clinic calls; the clinic guard is not a medical safety system. Booking, order placement, billing and automatic number provisioning are not implemented.

## Configuration

| Setting | Purpose |
| --- | --- |
| `KB_PATH` | Knowledge-base file; defaults to `data/policy_booklets.txt` |
| `RETRIEVAL_MODE` | `auto` uses installed models or falls back to lexical search; `hybrid` requires models; `lexical` skips them |
| `LLM_PROVIDER` | `extractive`, `ollama`, or `groq` |
| `GROQ_MODEL` | Defaults to `qwen/qwen3.8-27b`; requires `GROQ_API_KEY` |
| `MODEL_MAX_TOKENS`, `MODEL_TIMEOUT_SECONDS` | Answer completion budget and pipeline deadline; defaults `600`, `15` |
| `CHUNK_SIZE_WORDS`, `CHUNK_OVERLAP_WORDS` | Retrieval window size and overlap; defaults `160`, `24` |
| `RETRIEVAL_CANDIDATES`, `RETRIEVAL_TOP_K` | Reranking shortlist and packed result groups; defaults `16`, `5` |
| `CONTEXT_MAX_SOURCES`, `CONTEXT_MAX_CHARS` | Source-count and serialized-context limits; defaults `8`, `5000` |
| `VOICE_ENABLED` | `./run.sh` enables speech by default; use `VOICE_ENABLED=false` to skip loading speech models |
| `CPU_THREADS` | CPU inference threads; default `4` |
| `API_TOKEN` | Operator secret required for the internal playground, operator API and Exotel phone streams |
| `PUBLIC_ORIGIN` | Exact HTTPS origin for public deployments and account email links |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | Google OAuth web client credentials and exact callback URL |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` | STARTTLS email relay and sender for verification and password reset |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Optional SMTP relay credentials |
| `REQUIRE_EMAIL_VERIFICATION` | Require verification even in local development; defaults to `false` |
| `BUSINESSES_DIR`, `MAX_PHONE_CALLS` | Workspace storage and simultaneous phone sessions; defaults `runtime/businesses`, `2` |
| `QWEN_PYTHON`, `QWEN_MODEL_DIR` | Isolated cloning interpreter and model directory |
| `VOICES_DIR` | Persistent voice profiles; defaults to `runtime/voices` |

Microphone capture requires localhost or HTTPS. Customer question audio is processed in memory; saved cloning references are stored on disk. Answer/audio caches are bounded and in memory, and conversation history comes from the browser. The server does not log question bodies or save customer recordings.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process health |
| `GET /api/status` | Effective retrieval models, speech readiness, and active knowledge base |
| `POST /api/ask` | Answer a question with source citations |
| `POST /api/transcribe?language=en` | Transcribe mono PCM16 WAV audio |
| `POST /api/speak` | Generate speech from `text`, `language`, optional `voice_id` |
| `POST /api/speak/stream` | Same fields; incremental PCM16 audio in NDJSON `start`, `audio`, `done` events |
| `GET /api/voices` | List saved voices and cloning availability |
| `POST /api/voices/{voice_id}/prepare` | Warm a saved cloned voice without playing audio |
| `POST /api/voices` | Save multipart `name`, `transcript`, and `audio` |
| `DELETE /api/voices/{voice_id}` | Delete a saved voice |

```sh
curl http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{"question":"What is the name of the policy?","language":"en"}'
```

These playground endpoints require the operator token. `/api/ask` also accepts up to six history messages. Audio uploads must be mono PCM16 WAV at 8–48 kHz, at most 3 MB, and within the configured duration limit. Errors use `{"error":{"code":"...","message":"..."}}`.

For streaming speech, each `audio` event contains `sample_rate` and base64 `pcm` (mono, signed 16-bit little-endian samples). A `done` event confirms completion; an `error` event or EOF without `done` means the reply was incomplete. Cancelling the HTTP stream stops inference. Validation/authentication failures use normal HTTP errors before streaming begins. Audio buffers and response lengths are bounded; only completed cloned replies enter the replay cache.

The retired `accent: "original"` value remains accepted for older clients. Indian/American/British accent parameters return 422; the interface no longer sends them. Old saved accent preferences are cleared on page load.

## Tests and evaluation

```sh
uv pip install --python .venv-local/bin/python -r requirements/dev.txt
.venv-local/bin/python -m pytest -q
.venv-local/bin/ruff check src scripts tests

# New generation pipeline: local first-pass context recall, no API calls.
.venv-local/bin/python scripts/evaluate_rag.py

# Real answers: sends fixture questions/sources to Groq; consumes account quota.
# Pacing reduces rate-limit failures; adjust to your account's token limit.
.venv-local/bin/python scripts/evaluate_rag.py --live --interval 35 --compare-legacy
.venv-local/bin/python scripts/evaluate_rag.py --live --interval 35 \
  --cases tests/fixtures/rag_scope_cases.json --report runtime/rag-scope-evaluation.json

# Original extractive pipeline; uses real local retrieval models.
.venv-local/bin/python scripts/evaluate_retrieval.py
.venv-local/bin/python scripts/evaluate_retrieval.py \
  --cases tests/fixtures/retrieval_holdout.json \
  --report runtime/retrieval-holdout.json
.venv-local/bin/python scripts/evaluate_retrieval.py \
  --cases tests/fixtures/retrieval_challenge.json \
  --report runtime/retrieval-challenge.json
```

Unit/API tests use fixtures and mocked inference, including query rewriting, scope checks, missing-source recovery, invalid citations, bounded retries, timeouts, and context budgets. `rag_cases.json` contains 26 questions covering new English/Hindi paraphrases, combined questions, numeric conditions, follow-ups, and unsupported requests. `rag_scope_cases.json` adds eight checks distinguishing policy questions from unrelated subjects that share vocabulary. Offline evaluation measures raw-query context recall before any hosted rewrite; the live check measures final answers, citations, required and forbidden answer conditions, and refusals. Evaluation normalizes Unicode whitespace and digits so Hindi numerals are checked consistently.

The three original retrieval fixtures contain 203 questions across every searchable policy entry, additional paraphrases, and unsupported requests. They can also be passed to `evaluate_rag.py --cases <path>` to check the new context retrieval. These are regression checks, not a general accuracy guarantee. Generated reports live under `runtime/`. Live evaluation reports include token usage; it does not run by default in unit tests.

Optional integration checks:

- `scripts/smoke_voice.py`: real local TTS → transcription → retrieval using synthetic speech.
- `scripts/smoke_ui.py` and `scripts/smoke_clone_ui.py`: browser flows against a running server, with simulated microphone input and stub speech. Install `playwright` in `.venv-local` and run `.venv-local/bin/python -m playwright install chromium` first.
- `scripts/smoke_streaming_ui.py`: browser checks for playback before completion, fragmented packets, interruption, replay, voice warm-up, and truncated/error responses; answer and speech generation are stubbed.
- `scripts/smoke_qwen.py`: real cloning against a running server; creates a synthetic reference and deletes its temporary profile afterward.

Run each with `.venv-local/bin/python`. Browser checks assume the default local address. Synthetic checks do not measure physical microphone quality or similarity to a real person's voice.

## Repository layout

```text
src/                  API, retrieval, answers, speech, and voice storage
static/               Browser UI and microphone worklets
data/                 Active policy source and annotations
requirements/         Dependency definitions and pinned installation files
scripts/              Model installers, document converter, Qwen worker, and integration checks
tests/                Unit/API tests and retrieval fixtures
run.sh                Local server entry point
.env.example          Configuration reference
models/               Downloaded runtime models (ignored)
runtime/              Index, saved voices, and generated reports (ignored)
```

Dependency profiles are `base` (API), `retrieval` (API + search), `voice` (API + search + speech), `documents` (optional PDF/DOCX converter), and `qwen-mlx` (isolated cloning worker). Install from the corresponding `.lock` file; `.txt` files describe dependencies for maintenance. `dev.txt` adds test/lint tools. Lock files retain the exact package versions; they are not duplicate environments.

Keep `.venv-local/`, `.venv-qwen/`, required models, `.env`, and `runtime/voices/` for the installed app. `runtime/knowledge.sqlite3` caches the derived search index and can rebuild if removed. Screenshots, evaluation reports, synthetic smoke-test audio, `__pycache__/`, `.pytest_cache/`, and `.ruff_cache/` can be regenerated. Local environments, models, state, and secrets are excluded by `.gitignore`.

Model projects: [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [multilingual E5](https://huggingface.co/intfloat/multilingual-e5-small), [BGE reranker](https://huggingface.co/BAAI/bge-reranker-v2-m3), [Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx), [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), and [MLX Audio](https://github.com/Blaizzy/mlx-audio). Retrieval and cloning installers pin revisions and verify model artifacts.
