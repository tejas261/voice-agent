# Graph Report - voice-agent  (2026-11-06)

## Corpus Check
- 92 files · ~59,179 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 11 file(s) not represented in the graph (top: .lock 5, .css 4, .example 1)

## Summary
- 882 nodes · 2413 edges · 38 communities (27 shown, 11 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 362 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Accounts and OAuth Sessions
- Qwen Worker and Voice Cloning
- Grounded Answer Pipeline
- Insurance Policy Knowledge Base
- Voice Profile Store
- Business Admin UI
- App Routes and Account API
- Scripts and Model Evaluation
- Document Loading and Models
- Browser Voice Interface
- HTTP Client and Mailer
- Business Test Fixtures
- Local Embeddings and Reranking
- Retrieval Tests and Hits
- Document Conversion Pipeline
- Local Speech and Voices
- API Route Handlers
- Google Auth Tests
- Speech Streaming
- RAG Retriever Core
- Policy Behaviour Tests
- Browser Smoke Tests
- Telephony WebSocket
- Cloned Speech Client
- API Security Tests
- Chunking and Retrieval Windows
- Business Portal UI
- SaaS Onboarding Tests
- Recovery UI
- Run Script
- Setup Script
- PCM Recorder Worklet
- Auth Frontend Helper
- Google Icon Asset

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
- `generate()` --indirect_call--> `reference()`  [INFERRED]
  scripts/qwen_worker.py → tests/test_voice_cloning.py
- `evaluate()` --uses--> `Settings`  [INFERRED]
  scripts/evaluate_rag.py → src/config.py
- `main()` --uses--> `Settings`  [INFERRED]
  scripts/smoke_voice.py → src/config.py
- `auth_capabilities()` --indirect_call--> `settings()`  [INFERRED]
  src/app.py → tests/conftest.py
- `business_voice()` --indirect_call--> `settings()`  [INFERRED]
  src/app.py → tests/conftest.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Grounded Answer Pipeline** — readme_qwen_38_27b_groq, readme_hybrid_retrieval, readme_bge_reranker, readme_retrieval_budget, readme_source_recovery, readme_answer_cache [EXTRACTED 1.00]
- **Local Speech Recognition and Synthesis Stack** — readme_faster_whisper_asr, readme_kokoro_onnx_tts, readme_qwen3_tts_cloning, readme_streaming_speech [EXTRACTED 1.00]
- **Account Access and Recovery Flow** — static_auth_html_account_auth_page, static_recovery_html_password_recovery, static_operator_html_operator_login, readme_google_oauth, readme_email_verification [INFERRED 0.85]

## Communities (38 total, 11 thin omitted)

### Community 0 - "Accounts and OAuth Sessions"
Cohesion: 0.06
Nodes (21): AccountStore, Credentials, EmailRequest, OnboardingProgress, OperatorLogin, password_hash(), PasswordReset, Signup (+13 more)

### Community 1 - "Qwen Worker and Voice Cloning"
Cohesion: 0.06
Nodes (38): main(), emit(), generate(), send(), main(), clone_phrase(), pcm(), PhraseFailure (+30 more)

### Community 2 - "Grounded Answer Pipeline"
Cohesion: 0.10
Nodes (31): evaluate(), Assistant, GroundedAnswer, QueryPlan, AskRequest, test_followup_and_cache_isolation(), test_groq_adapter(), test_groq_json_validation_error_retries_and_checks_evidence() (+23 more)

### Community 3 - "Insurance Policy Knowledge Base"
Cohesion: 0.06
Nodes (53): Graphify Knowledge Graph Workflow, Customer Service and Grievance Escalation, Death Benefit Calculation, Education Income Benefit, Free Look Period and Suicide Exclusion, HDFC Life Click 2 Protect Elite Pro Policy, High Sum Assured and Salaried Discounts, Immediate Interim Death Payout (+45 more)

### Community 4 - "Voice Profile Store"
Cohesion: 0.08
Nodes (22): VoiceProfile, VoiceStore, client(), FakeSpeech, clone_settings(), cloned_client(), FakeStandard, FakeWorker (+14 more)

### Community 5 - "Business Admin UI"
Cohesion: 0.12
Nodes (18): api(), badge(), listBusinesses(), notice(), openBusiness(), renderEntries(), run(), state (+10 more)

### Community 6 - "App Routes and Account API"
Cohesion: 0.08
Nodes (25): create_app(), authorized(), business_access(), business_dashboard(), business_voice(), create_business(), create_business_voice(), create_stream_key() (+17 more)

### Community 7 - "Scripts and Model Evaluation"
Cohesion: 0.11
Nodes (10): EvaluationLogs, normalize_answer(), main(), download(), main(), main(), main(), main() (+2 more)

### Community 8 - "Document Loading and Models"
Cohesion: 0.14
Nodes (9): load_documents(), retrieval_passage(), Answer, FAQ, Source, SpeakRequest, requires_personal_records(), test_customer_record_requests_need_a_separate_data_source() (+1 more)

### Community 9 - "Browser Voice Interface"
Cohesion: 0.17
Nodes (25): addMessage(), api(), ask(), brush, cancelRecording(), canvas, clearConversation(), connect() (+17 more)

### Community 10 - "HTTP Client and Mailer"
Cohesion: 0.13
Nodes (11): tls_context(), configured(), send_account_email(), executable(), installer_fixture(), run_setup(), test_fresh_setup_and_rerun_preserve_existing_data(), test_incompatible_or_broken_environment_is_not_replaced() (+3 more)

### Community 11 - "Business Test Fixtures"
Cohesion: 0.20
Nodes (13): basic(), create(), credentials(), entry(), FakeSpeech, FakeVoice, platform(), test_custom_voice_requires_consent_and_is_tenant_scoped() (+5 more)

### Community 12 - "Local Embeddings and Reranking"
Cohesion: 0.15
Nodes (6): get_embedder(), get_reranker(), LocalEmbedder, LocalReranker, SemanticIndex, test_cached_passages_are_reused_and_deleted_content_is_removed()

### Community 13 - "Retrieval Tests and Hits"
Cohesion: 0.15
Nodes (15): Hit, competitors_of(), supported(), terms(), test_policy_does_not_answer_unrelated_queries(), test_bilingual_questions(), test_contractions_and_possessives_do_not_add_query_terms(), test_explicit_unknown_subject_is_not_dropped_for_a_similar_action() (+7 more)

### Community 14 - "Document Conversion Pipeline"
Cohesion: 0.16
Nodes (13): convert(), _docx_blocks(), extract(), main(), pack_files(), split_blocks(), _text(), make_pdf() (+5 more)

### Community 15 - "Local Speech and Voices"
Cohesion: 0.19
Nodes (9): main(), speak(), wav(), silent_wav(), stream_wav(), wav_packets(), normalize_reference(), sample_wav() (+1 more)

### Community 16 - "API Route Handlers"
Cohesion: 0.13
Nodes (20): account_email_link(), ask_business(), auth_capabilities(), cookie_secure(), exotel_stream(), google_callback(), google_link_start(), google_login_start() (+12 more)

### Community 17 - "Google Auth Tests"
Cohesion: 0.19
Nodes (11): begin(), callback(), create_business(), google_settings(), local_signup(), test_config_disabled_without_credentials_and_rejects_wrong_origin(), test_existing_account_auto_link_and_non_authoritative_link(), test_google_signup_signin_preserves_workspace_and_checks_state() (+3 more)

### Community 18 - "Speech Streaming"
Cohesion: 0.20
Nodes (10): stream_speech(), pcm_packet(), SpeechStream, test_first_audio_arrives_before_generation_finishes_and_cancel_releases_slot(), generate(), test_slow_reader_has_bounded_buffer_and_can_cancel(), generate(), test_stream_failure_is_explicit_not_a_successful_truncated_reply() (+2 more)

### Community 19 - "RAG Retriever Core"
Cohesion: 0.25
Nodes (7): KnowledgeBase, Chunk, Context, RAGRetriever, test_context_budget_never_truncates_a_required_bundle(), test_context_deduplicates_overviews_and_keeps_required_conditions(), test_personal_account_records_are_not_substituted_with_product_facts()

### Community 20 - "Policy Behaviour Tests"
Cohesion: 0.20
Nodes (15): policy_settings(), test_chunks_keep_parent_context_without_changing_spoken_or_cited_facts(), test_default_knowledge_base_is_original_policy(), test_exact_reported_question_returns_conditional_source_answer(), test_hindi_grouped_answers_preserve_source_conditions(), test_linked_context_and_overview_rebuild_from_changed_source(), test_missing_overview_source_fails_clearly(), test_nonpayment_answer_includes_grace_period_and_paid_up_conditions() (+7 more)

### Community 21 - "Browser Smoke Tests"
Cohesion: 0.18
Nodes (3): main(), main(), speak()

### Community 22 - "Telephony WebSocket"
Cohesion: 0.19
Nodes (10): pcm_wav(), Message, exotel_session(), respond(), send(), speak(), send_audio(), pcm_for_phone() (+2 more)

### Community 24 - "API Security Tests"
Cohesion: 0.14
Nodes (7): test_accent_does_not_silently_replace_a_clone_or_hindi(), test_ask_input_validation(), test_frontend_build_urls_keep_modules_together_and_refresh_cached_players(), test_local_only_and_dns_rebinding(), test_streaming_speech_success_and_failure(), test_token_auth(), test_voice_disabled()

### Community 25 - "Chunking and Retrieval Windows"
Cohesion: 0.23
Nodes (6): split_windows(), TextWindow, test_hindi_and_english_sentence_boundaries_are_preserved(), test_invalid_chunk_settings_fail_clearly(), test_long_passages_have_bounded_overlap_without_losing_words(), test_short_clauses_have_no_artificial_overlap()

### Community 26 - "Business Portal UI"
Cohesion: 0.24
Nodes (11): action(), api(), el(), loadBusiness(), notice(), progress(), refreshStatus(), renderEntries() (+3 more)

### Community 27 - "SaaS Onboarding Tests"
Cohesion: 0.26
Nodes (10): test_google_id_token_signature_and_claims_are_verified(), token(), csrf(), signup(), test_email_verification_and_one_time_password_reset(), test_public_pages_and_operator_gate(), test_public_signup_requires_email_delivery_and_retries_after_failure(), test_self_service_tenants_are_isolated_and_onboarding_resumes() (+2 more)

## Knowledge Gaps
- **10 isolated node(s):** `VOICE_ENABLED`, `Graphify Knowledge Graph Workflow`, `BGE v2 M3 Cross-Encoder Reranker`, `Reciprocal Rank Fusion`, `Five-Minute Answer Cache` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 209 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `create_app()` connect `App Routes and Account API` to `Accounts and OAuth Sessions`, `Qwen Worker and Voice Cloning`, `Grounded Answer Pipeline`, `Voice Profile Store`, `Scripts and Model Evaluation`, `Document Loading and Models`, `HTTP Client and Mailer`, `Business Test Fixtures`, `Document Conversion Pipeline`, `API Route Handlers`, `Google Auth Tests`, `Speech Streaming`, `Policy Behaviour Tests`, `Telephony WebSocket`, `Cloned Speech Client`, `API Security Tests`, `SaaS Onboarding Tests`?**
  _High betweenness centrality (0.188) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `create_app()` (e.g. with `Credentials` and `EmailRequest`) actually correct?**
  _`create_app()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **What connects `VOICE_ENABLED`, `Graphify Knowledge Graph Workflow`, `BGE v2 M3 Cross-Encoder Reranker` to the rest of the system?**
  _10 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Accounts and OAuth Sessions` be split into smaller, more focused modules?**
  _Cohesion score 0.05574229691876751 - nodes in this community are weakly interconnected._
- **Why does `Settings` connect `Scripts and Model Evaluation` to `Accounts and OAuth Sessions`, `Qwen Worker and Voice Cloning`, `Grounded Answer Pipeline`, `Voice Profile Store`, `App Routes and Account API`, `Document Loading and Models`, `HTTP Client and Mailer`, `Local Speech and Voices`, `API Route Handlers`, `RAG Retriever Core`, `Policy Behaviour Tests`, `Cloned Speech Client`, `Chunking and Retrieval Windows`, `Audio Transport Docs`, `Config Validation`?**
  _High betweenness centrality (0.070) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `Assistant` (e.g. with `Settings` and `Answer`) actually correct?**
  _`Assistant` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Should `Qwen Worker and Voice Cloning` be split into smaller, more focused modules?**
  _Cohesion score 0.06467661691542288 - nodes in this community are weakly interconnected._