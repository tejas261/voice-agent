# Graph Report - voice-agent  (2026-10-30)

## Corpus Check
- 75 files · ~57,650 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 11 file(s) not represented in the graph (top: .lock 5, .css 4, .example 1)

## Summary
- 827 nodes · 2310 edges · 34 communities (27 shown, 7 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 327 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `adc08094`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Settings
- io
- Assistant
- test_google_auth.py
- .search
- app.js
- test_voice_cloning.py
- ClonedSpeech
- VoiceControls
- qwen_worker.py
- rag_engine.py
- create_app
- SpeechService
- business.js
- app.py
- run.sh
- PCMRecorder
- AGENTS.md
- embeddings.py
- pcm_packet
- split_windows
- test_businesses.py
- test_policy.py
- json
- test_api.py
- RAGRetriever
- KnowledgeBase
- FAQ
- test_saas.py
- same_origin
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
- `API` --references--> `text()`  [INFERRED]
  README.md → static/business.js
- `evaluate()` --uses--> `Settings`  [INFERRED]
  scripts/evaluate_rag.py → src/config.py

## Import Cycles
- None detected.

## Communities (34 total, 7 thin omitted)

### Community 0 - "Settings"
Cohesion: 0.06
Nodes (15): EvaluationLogs, normalize_answer(), download(), main(), main(), main(), GroundedAnswer, QueryPlan (+7 more)

### Community 1 - "io"
Cohesion: 0.14
Nodes (5): main(), reference_audio(), wav_packets(), normalize_reference(), test_reference_is_resampled_to_model_rate()

### Community 2 - "Assistant"
Cohesion: 0.11
Nodes (30): evaluate(), Assistant, Answer, AskRequest, test_followup_and_cache_isolation(), test_groq_adapter(), test_groq_json_validation_error_retries_and_checks_evidence(), test_grounded_generation() (+22 more)

### Community 3 - "test_google_auth.py"
Cohesion: 0.17
Nodes (12): client(), begin(), callback(), create_business(), google_settings(), local_signup(), test_config_disabled_without_credentials_and_rejects_wrong_origin(), test_existing_account_auto_link_and_non_authoritative_link() (+4 more)

### Community 4 - ".search"
Cohesion: 0.20
Nodes (12): main(), Hit, terms(), test_bilingual_questions(), test_contractions_and_possessives_do_not_add_query_terms(), test_explicit_unknown_subject_is_not_dropped_for_a_similar_action(), test_negation_and_inflections_survive_normalization(), test_supported_lower_candidate_survives_an_unsupported_top_hit() (+4 more)

### Community 5 - "app.js"
Cohesion: 0.17
Nodes (25): addMessage(), api(), ask(), brush, cancelRecording(), canvas, clearConversation(), connect() (+17 more)

### Community 6 - "test_voice_cloning.py"
Cohesion: 0.09
Nodes (21): generate(), VoiceProfile, VoiceStore, clone_settings(), cloned_client(), FakeStandard, FakeWorker, reference() (+13 more)

### Community 8 - "VoiceControls"
Cohesion: 0.11
Nodes (20): action(), api(), el(), loadBusiness(), notice(), progress(), refreshStatus(), renderEntries() (+12 more)

### Community 9 - "qwen_worker.py"
Cohesion: 0.10
Nodes (29): main(), emit(), send(), clone_phrase(), pcm(), PhraseFailure, speech_parts(), SpeechPart (+21 more)

### Community 10 - "rag_engine.py"
Cohesion: 0.24
Nodes (3): requires_personal_records(), test_customer_record_requests_need_a_separate_data_source(), test_first_person_general_rules_remain_answerable()

### Community 11 - "create_app"
Cohesion: 0.07
Nodes (29): create_app(), authorized(), business_access(), business_dashboard(), business_voice(), create_business(), create_business_voice(), create_stream_key() (+21 more)

### Community 12 - "SpeechService"
Cohesion: 0.16
Nodes (8): main(), SpeechService, test_reject_invalid_or_unbounded_audio(), test_resamples_to_whisper_rate_and_filters_silence(), test_silence_does_not_hallucinate(), test_speech_cache_separates_languages(), test_stream_passes_whole_sentence_and_preserves_model_timing(), wav_bytes()

### Community 13 - "business.js"
Cohesion: 0.08
Nodes (26): Add a policy, API, Configuration, Convert a document for the knowledge base, Google sign-in, How answers work, Knowledge base, Listen — voice support for businesses (+18 more)

### Community 14 - "app.py"
Cohesion: 0.06
Nodes (19): AccountStore, Credentials, EmailRequest, OnboardingProgress, OperatorLogin, password_hash(), PasswordReset, Signup (+11 more)

### Community 19 - "embeddings.py"
Cohesion: 0.18
Nodes (6): retrieval_passage(), get_embedder(), get_reranker(), LocalEmbedder, LocalReranker, test_chunks_keep_parent_context_without_changing_spoken_or_cited_facts()

### Community 20 - "pcm_packet"
Cohesion: 0.11
Nodes (20): stream_speech(), pcm_packet(), pcm_wav(), SpeechStream, Message, exotel_session(), respond(), send() (+12 more)

### Community 21 - "split_windows"
Cohesion: 0.23
Nodes (6): split_windows(), TextWindow, test_hindi_and_english_sentence_boundaries_are_preserved(), test_invalid_chunk_settings_fail_clearly(), test_long_passages_have_bounded_overlap_without_losing_words(), test_short_clauses_have_no_artificial_overlap()

### Community 22 - "test_businesses.py"
Cohesion: 0.20
Nodes (14): basic(), create(), credentials(), entry(), FakeSpeech, FakeVoice, platform(), sample_wav() (+6 more)

### Community 23 - "test_policy.py"
Cohesion: 0.26
Nodes (13): test_default_knowledge_base_is_original_policy(), test_exact_reported_question_returns_conditional_source_answer(), test_hindi_grouped_answers_preserve_source_conditions(), test_linked_context_and_overview_rebuild_from_changed_source(), test_missing_overview_source_fails_clearly(), test_nonpayment_answer_includes_grace_period_and_paid_up_conditions(), test_overview_after_nonpayment_is_sourced_and_does_not_answer_about_loans(), test_policy_identity_comes_from_the_name_clause() (+5 more)

### Community 24 - "json"
Cohesion: 0.13
Nodes (8): main(), speak(), wav(), main(), main(), speak(), silent_wav(), stream_wav()

### Community 25 - "test_api.py"
Cohesion: 0.12
Nodes (7): FakeSpeech, test_accent_does_not_silently_replace_a_clone_or_hindi(), test_ask_input_validation(), test_local_only_and_dns_rebinding(), test_streaming_speech_success_and_failure(), test_token_auth(), test_voice_disabled()

### Community 26 - "RAGRetriever"
Cohesion: 0.28
Nodes (6): Chunk, Context, RAGRetriever, test_context_budget_never_truncates_a_required_bundle(), test_context_deduplicates_overviews_and_keeps_required_conditions(), test_personal_account_records_are_not_substituted_with_product_facts()

### Community 27 - "KnowledgeBase"
Cohesion: 0.22
Nodes (5): SemanticIndex, KnowledgeBase, test_cached_passages_are_reused_and_deleted_content_is_removed(), test_policy_does_not_answer_unrelated_queries(), test_idempotent_reindex_and_deleted_entries()

### Community 28 - "FAQ"
Cohesion: 0.50
Nodes (3): FAQ, competitors_of(), supported()

### Community 31 - "test_saas.py"
Cohesion: 0.26
Nodes (10): test_google_id_token_signature_and_claims_are_verified(), token(), csrf(), signup(), test_email_verification_and_one_time_password_reset(), test_public_pages_and_operator_gate(), test_public_signup_requires_email_delivery_and_retries_after_failure(), test_self_service_tenants_are_isolated_and_onboarding_resumes() (+2 more)

### Community 32 - "same_origin"
Cohesion: 0.15
Nodes (18): account_email_link(), ask_business(), auth_capabilities(), cookie_secure(), exotel_stream(), google_callback(), lifespan(), login() (+10 more)

### Community 33 - "re"
Cohesion: 0.14
Nodes (15): convert(), _docx_blocks(), extract(), main(), pack_files(), split_blocks(), _text(), load_documents() (+7 more)

## Knowledge Gaps
- **1 isolated node(s):** `VOICE_ENABLED`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 208 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `create_app()` connect `create_app` to `Settings`, `same_origin`, `Assistant`, `test_google_auth.py`, `re`, `test_voice_cloning.py`, `ClonedSpeech`, `SpeechService`, `app.py`, `pcm_packet`, `test_businesses.py`, `test_policy.py`, `test_api.py`, `test_saas.py`?**
  _High betweenness centrality (0.237) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `create_app()` (e.g. with `Credentials` and `EmailRequest`) actually correct?**
  _`create_app()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **What connects `VOICE_ENABLED` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Settings` be split into smaller, more focused modules?**
  _Cohesion score 0.058653846153846154 - nodes in this community are weakly interconnected._
- **Why does `API` connect `business.js` to `VoiceControls`, `qwen_worker.py`?**
  _High betweenness centrality (0.223) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `Assistant` (e.g. with `Settings` and `Answer`) actually correct?**
  _`Assistant` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Should `io` be split into smaller, more focused modules?**
  _Cohesion score 0.14245014245014245 - nodes in this community are weakly interconnected._