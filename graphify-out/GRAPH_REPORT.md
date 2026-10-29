# Graph Report - voice-agent  (2026-10-29)

## Corpus Check
- 60 files · ~40,103 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 7 file(s) not represented in the graph (top: .lock 4, .example 1, (none) 1)

## Summary
- 573 nodes · 1580 edges · 22 communities (15 shown, 7 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 241 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Settings
- SpeechService
- Assistant
- RAGRetriever
- KnowledgeBase
- app.js
- test_voice_cloning.py
- ClonedSpeech
- VoiceControls
- qwen_worker.py
- rag_engine.py
- create_app
- SemanticIndex
- Listen — local voice support agent
- test_embedding_cache.py
- run.sh
- PCMRecorder
- AGENTS.md
- GroundedAnswer
- pcm_packet
- split_windows

## God Nodes (most connected - your core abstractions)
1. `Assistant` - 53 edges
2. `create_app()` - 49 edges
3. `Settings` - 42 edges
4. `AskRequest` - 41 edges
5. `KnowledgeBase` - 38 edges
6. `ClonedSpeech` - 22 edges
7. `VoiceControls` - 22 edges
8. `SpeechService` - 19 edges
9. `VoiceStore` - 19 edges
10. `handler()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `API` --references--> `pcm()`  [INFERRED]
  README.md → src/clone_audio.py
- `generate()` --indirect_call--> `reference()`  [INFERRED]
  scripts/qwen_worker.py → tests/test_voice_cloning.py
- `API` --references--> `start()`  [INFERRED]
  README.md → static/reference-recorder.js
- `evaluate()` --uses--> `Settings`  [INFERRED]
  scripts/evaluate_rag.py → src/config.py
- `main()` --uses--> `Settings`  [INFERRED]
  scripts/smoke_voice.py → src/config.py

## Import Cycles
- None detected.

## Communities (22 total, 7 thin omitted)

### Community 0 - "Settings"
Cohesion: 0.05
Nodes (12): EvaluationLogs, normalize_answer(), main(), download(), main(), main(), main(), main() (+4 more)

### Community 1 - "SpeechService"
Cohesion: 0.18
Nodes (8): main(), SpeechService, test_reject_invalid_or_unbounded_audio(), test_resamples_to_whisper_rate_and_filters_silence(), test_silence_does_not_hallucinate(), test_speech_cache_separates_languages(), test_stream_passes_whole_sentence_and_preserves_model_timing(), wav_bytes()

### Community 2 - "Assistant"
Cohesion: 0.10
Nodes (34): evaluate(), Assistant, Answer, AskRequest, FAQ, Message, Source, SpeakRequest (+26 more)

### Community 3 - "RAGRetriever"
Cohesion: 0.23
Nodes (6): Chunk, Context, RAGRetriever, test_context_budget_never_truncates_a_required_bundle(), test_context_deduplicates_overviews_and_keeps_required_conditions(), test_personal_account_records_are_not_substituted_with_product_facts()

### Community 4 - "KnowledgeBase"
Cohesion: 0.18
Nodes (17): Hit, KnowledgeBase, competitors_of(), supported(), terms(), test_policy_does_not_answer_unrelated_queries(), test_updated_source_invalidates_index_and_stale_translation(), test_bilingual_questions() (+9 more)

### Community 5 - "app.js"
Cohesion: 0.17
Nodes (25): addMessage(), api(), ask(), brush, cancelRecording(), canvas, clearConversation(), connect() (+17 more)

### Community 6 - "test_voice_cloning.py"
Cohesion: 0.06
Nodes (30): main(), speak(), wav(), reference_audio(), main(), main(), speak(), silent_wav() (+22 more)

### Community 7 - "ClonedSpeech"
Cohesion: 0.10
Nodes (8): lifespan(), SpeechCancelled, ClonedSpeech, VoiceProfile, VoiceStore, settings(), FakeWorker, test_abandoned_clone_stream_cancels_worker_without_caching_partial_audio()

### Community 9 - "qwen_worker.py"
Cohesion: 0.10
Nodes (31): main(), emit(), generate(), send(), clone_phrase(), pcm(), PhraseFailure, speech_parts() (+23 more)

### Community 10 - "rag_engine.py"
Cohesion: 0.18
Nodes (5): retrieval_passage(), requires_personal_records(), test_chunks_keep_parent_context_without_changing_spoken_or_cited_facts(), test_customer_record_requests_need_a_separate_data_source(), test_first_person_general_rules_remain_answerable()

### Community 11 - "create_app"
Cohesion: 0.06
Nodes (23): create_app(), run_speech(), speak(), transcribe(), test_accent_does_not_silently_replace_a_clone_or_hindi(), test_ask_input_validation(), test_frontend_build_urls_keep_modules_together_and_refresh_cached_players(), test_local_only_and_dns_rebinding() (+15 more)

### Community 12 - "SemanticIndex"
Cohesion: 0.23
Nodes (5): get_embedder(), get_reranker(), LocalEmbedder, LocalReranker, SemanticIndex

### Community 13 - "Listen — local voice support agent"
Cohesion: 0.16
Nodes (17): API, Configuration, How answers work, Knowledge base, Listen — local voice support agent, Repository layout, Run locally, Tests and evaluation (+9 more)

### Community 20 - "pcm_packet"
Cohesion: 0.20
Nodes (10): stream_speech(), pcm_packet(), SpeechStream, test_first_audio_arrives_before_generation_finishes_and_cancel_releases_slot(), generate(), test_slow_reader_has_bounded_buffer_and_can_cancel(), generate(), test_stream_failure_is_explicit_not_a_successful_truncated_reply() (+2 more)

### Community 21 - "split_windows"
Cohesion: 0.23
Nodes (6): split_windows(), TextWindow, test_hindi_and_english_sentence_boundaries_are_preserved(), test_invalid_chunk_settings_fail_clearly(), test_long_passages_have_bounded_overlap_without_losing_words(), test_short_clauses_have_no_artificial_overlap()

## Knowledge Gaps
- **1 isolated node(s):** `VOICE_ENABLED`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 161 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `API` connect `Listen — local voice support agent` to `qwen_worker.py`?**
  _High betweenness centrality (0.209) - this node is a cross-community bridge._
- **Are the 31 inferred relationships involving `Assistant` (e.g. with `Settings` and `Answer`) actually correct?**
  _`Assistant` has 31 INFERRED edges - model-reasoned connections that need verification._
- **What connects `VOICE_ENABLED` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Settings` be split into smaller, more focused modules?**
  _Cohesion score 0.05163511187607573 - nodes in this community are weakly interconnected._
- **Why does `pcm()` connect `qwen_worker.py` to `Listen — local voice support agent`?**
  _High betweenness centrality (0.208) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `create_app()` (e.g. with `authorized()` and `lifespan()`) actually correct?**
  _`create_app()` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Should `Assistant` be split into smaller, more focused modules?**
  _Cohesion score 0.09624413145539906 - nodes in this community are weakly interconnected._