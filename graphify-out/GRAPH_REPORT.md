# Graph Report - voice-agent  (2026-10-30)

## Corpus Check
- 77 files · ~59,179 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 11 file(s) not represented in the graph (top: .lock 5, .css 4, .example 1)

## Summary
- 842 nodes · 2341 edges · 34 communities (23 shown, 11 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 327 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `35eeb0b1`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- hashlib
- cloned_speech.py
- Assistant
- test_google_auth.py
- KnowledgeBase
- app.js
- test_voice_cloning.py
- ClonedSpeech
- VoiceControls
- qwen_worker.py
- assistant.py
- create_app
- SpeechService
- business.js
- app.py
- run.sh
- PCMRecorder
- AGENTS.md
- app.py
- pcm_packet
- split_windows
- test_businesses.py
- voices.py
- json
- test_api.py
- io
- evaluate_rag.py
- mailer.py
- setup.sh
- Settings
- re
- auth.js

## God Nodes (most connected - your core abstractions)
1. `create_app()` - 127 edges
2. `Assistant` - 56 edges
3. `Settings` - 55 edges
4. `AskRequest` - 44 edges
5. `KnowledgeBase` - 38 edges
6. `TestClient()` - 30 edges
7. `BusinessStore` - 27 edges
8. `AccountStore` - 23 edges
9. `ClonedSpeech` - 23 edges
10. `VoiceStore` - 22 edges

## Surprising Connections (you probably didn't know these)
- `API` --references--> `pcm()`  [INFERRED]
  README.md → src/clone_audio.py
- `API` --references--> `error()`  [INFERRED]
  README.md → static/recovery.js
- `API` --references--> `start()`  [INFERRED]
  README.md → static/reference-recorder.js
- `generate()` --indirect_call--> `reference()`  [INFERRED]
  scripts/qwen_worker.py → tests/test_voice_cloning.py
- `API` --references--> `text()`  [INFERRED]
  README.md → static/business.js

## Import Cycles
- None detected.

## Communities (34 total, 11 thin omitted)

### Community 0 - "hashlib"
Cohesion: 0.18
Nodes (4): download(), main(), main(), main()

### Community 2 - "Assistant"
Cohesion: 0.08
Nodes (36): evaluate(), EvaluationLogs, Assistant, GroundedAnswer, QueryPlan, Answer, AskRequest, Message (+28 more)

### Community 3 - "test_google_auth.py"
Cohesion: 0.18
Nodes (11): begin(), callback(), create_business(), google_settings(), local_signup(), test_config_disabled_without_credentials_and_rejects_wrong_origin(), test_existing_account_auto_link_and_non_authoritative_link(), test_google_signup_signin_preserves_workspace_and_checks_state() (+3 more)

### Community 4 - "KnowledgeBase"
Cohesion: 0.06
Nodes (43): get_embedder(), get_reranker(), LocalEmbedder, LocalReranker, SemanticIndex, Hit, KnowledgeBase, competitors_of() (+35 more)

### Community 5 - "app.js"
Cohesion: 0.17
Nodes (25): addMessage(), api(), ask(), brush, cancelRecording(), canvas, clearConversation(), connect() (+17 more)

### Community 6 - "test_voice_cloning.py"
Cohesion: 0.08
Nodes (22): VoiceProfile, VoiceStore, client(), FakeSpeech, clone_settings(), cloned_client(), FakeStandard, FakeWorker (+14 more)

### Community 8 - "VoiceControls"
Cohesion: 0.11
Nodes (20): action(), api(), el(), loadBusiness(), notice(), progress(), refreshStatus(), renderEntries() (+12 more)

### Community 9 - "qwen_worker.py"
Cohesion: 0.09
Nodes (31): main(), emit(), generate(), send(), clone_phrase(), pcm(), PhraseFailure, speech_parts() (+23 more)

### Community 10 - "assistant.py"
Cohesion: 0.15
Nodes (7): load_documents(), retrieval_passage(), FAQ, SpeakRequest, requires_personal_records(), test_customer_record_requests_need_a_separate_data_source(), test_first_person_general_rules_remain_answerable()

### Community 11 - "create_app"
Cohesion: 0.05
Nodes (54): create_app(), account_email_link(), ask_business(), auth_capabilities(), authorized(), business_access(), business_dashboard(), business_voice() (+46 more)

### Community 12 - "SpeechService"
Cohesion: 0.27
Nodes (4): main(), SpeechService, test_resamples_to_whisper_rate_and_filters_silence(), test_speech_cache_separates_languages()

### Community 13 - "business.js"
Cohesion: 0.08
Nodes (26): Add a policy, API, Configuration, Convert a document for the knowledge base, Google sign-in, How answers work, Knowledge base, Listen — voice support for businesses (+18 more)

### Community 14 - "app.py"
Cohesion: 0.11
Nodes (7): AccountStore, BusinessCreate, BusinessStore, digest(), KnowledgeEntry, KnowledgeUpdate, test_v2_business_database_migrates_without_losing_existing_data()

### Community 19 - "app.py"
Cohesion: 0.12
Nodes (10): Credentials, EmailRequest, OnboardingProgress, OperatorLogin, password_hash(), PasswordReset, Signup, TokenRequest (+2 more)

### Community 20 - "pcm_packet"
Cohesion: 0.10
Nodes (18): exotel_stream(), stream_speech(), pcm_packet(), SpeechStream, exotel_session(), send(), speak(), send_audio() (+10 more)

### Community 21 - "split_windows"
Cohesion: 0.29
Nodes (6): split_windows(), TextWindow, test_hindi_and_english_sentence_boundaries_are_preserved(), test_invalid_chunk_settings_fail_clearly(), test_long_passages_have_bounded_overlap_without_losing_words(), test_short_clauses_have_no_artificial_overlap()

### Community 22 - "test_businesses.py"
Cohesion: 0.20
Nodes (14): basic(), create(), credentials(), entry(), FakeSpeech, FakeVoice, platform(), sample_wav() (+6 more)

### Community 23 - "voices.py"
Cohesion: 0.17
Nodes (4): main(), speak(), wav(), stream_wav()

### Community 24 - "json"
Cohesion: 0.21
Nodes (4): main(), main(), speak(), silent_wav()

### Community 25 - "test_api.py"
Cohesion: 0.14
Nodes (7): test_accent_does_not_silently_replace_a_clone_or_hindi(), test_ask_input_validation(), test_frontend_build_urls_keep_modules_together_and_refresh_cached_players(), test_local_only_and_dns_rebinding(), test_streaming_speech_success_and_failure(), test_token_auth(), test_voice_disabled()

### Community 26 - "io"
Cohesion: 0.35
Nodes (7): main(), reference_audio(), normalize_reference(), test_silence_does_not_hallucinate(), test_stream_passes_whole_sentence_and_preserves_model_timing(), wav_bytes(), test_reference_is_resampled_to_model_rate()

### Community 32 - "Settings"
Cohesion: 0.13
Nodes (5): Settings, authorize_url(), configured(), exchange_code(), verify_identity()

### Community 33 - "re"
Cohesion: 0.08
Nodes (22): convert(), _docx_blocks(), extract(), main(), pack_files(), split_blocks(), _text(), tls_context() (+14 more)

## Knowledge Gaps
- **1 isolated node(s):** `VOICE_ENABLED`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 209 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `create_app()` connect `create_app` to `hashlib`, `Settings`, `Assistant`, `re`, `test_google_auth.py`, `KnowledgeBase`, `test_voice_cloning.py`, `ClonedSpeech`, `assistant.py`, `SpeechService`, `app.py`, `app.py`, `pcm_packet`, `test_businesses.py`, `test_api.py`, `mailer.py`?**
  _High betweenness centrality (0.231) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `create_app()` (e.g. with `Credentials` and `EmailRequest`) actually correct?**
  _`create_app()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **What connects `VOICE_ENABLED` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Assistant` be split into smaller, more focused modules?**
  _Cohesion score 0.08373205741626795 - nodes in this community are weakly interconnected._
- **Why does `API` connect `business.js` to `VoiceControls`, `qwen_worker.py`?**
  _High betweenness centrality (0.219) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `Assistant` (e.g. with `Settings` and `Answer`) actually correct?**
  _`Assistant` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Should `KnowledgeBase` be split into smaller, more focused modules?**
  _Cohesion score 0.055944055944055944 - nodes in this community are weakly interconnected._