import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.assistant import Assistant
from src.config import ROOT, Settings
from src.documents import retrieval_passage
from src.models import AskRequest, Message
from src.rag_engine import KnowledgeBase

EXACT_QUESTION = "will i get back my accumulated premium after the term ends"
NO_CLAIM_QUESTION = (
    "Will I get a refund of my premium if I don't claim it during the insurance period?"
)


@pytest.fixture
def policy_settings(tmp_path):
    return Settings(index_path=tmp_path / "policy.sqlite3", retrieval_mode="lexical")


def test_default_knowledge_base_is_original_policy(policy_settings):
    assert policy_settings.kb_path == ROOT / "data/policy_booklets.txt"
    kb = KnowledgeBase(policy_settings.kb_path, policy_settings.index_path)
    assert len(kb.faqs) == 32
    assert all(f.source_file == "policy_booklets.txt" for f in kb.faqs)
    assert all(f.answer_hi for f in kb.faqs)


def test_chunks_keep_parent_context_without_changing_spoken_or_cited_facts(policy_settings):
    kb = KnowledgeBase(policy_settings.kb_path, policy_settings.index_path)
    clause = kb.by_id["grace_period_rules"]
    passage = retrieval_passage(clause)
    assert "HDFC Life Click 2 Protect Elite Pro" in passage
    assert "Life Insurance Plan" in passage
    assert "Grace Period Rules" in passage
    assert "30 days" in passage
    assert "HDFC Life" not in clause.answer_en
    assert "GRACE_PERIOD_RULES:" in clause.source_text
    assert all(entry.document_context for entry in kb.entries)


@pytest.mark.parametrize(
    "question,expected",
    [
        (EXACT_QUESTION, "maturity_benefit"),
        (NO_CLAIM_QUESTION, "maturity_benefit"),
        (NO_CLAIM_QUESTION.replace("don't", "don’t"), "maturity_benefit"),
        (NO_CLAIM_QUESTION.replace("don't", "do not"), "maturity_benefit"),
        ("Can I get my money back if I never claimed on the insurance?", "maturity_benefit"),
        ("If there are no claims during the term, are my premiums refunded?", "maturity_benefit"),
        ("Do unused premiums come back to me when my cover expires?", "maturity_benefit"),
        ("अगर मैंने कोई क्लेम नहीं किया तो क्या मेरा प्रीमियम वापस मिलेगा?", "maturity_benefit"),
        ("claim nahi kiya to premium wapas milega", "maturity_benefit"),
        ("Can I get a refund if I cancel during the free look period?", "free_look_cancellation"),
        ("I just bought this policy and want to cancel for a refund", "free_look_cancellation"),
        ("How quickly is a death claim paid?", "immediate_payout_rules"),
        ("Do I get my premiums back when the policy term ends?", "maturity_benefit"),
        ("What happens to the money I paid when my life cover expires?", "maturity_benefit"),
        ("What if I did not choose return of premium?", "maturity_benefit"),
        ("पॉलिसी की अवधि खत्म होने पर मेरे प्रीमियम वापस मिलेंगे?", "maturity_benefit"),
        ("बीमा की अवधि पूरी होने पर क्या मिलता है?", "maturity_benefit"),
        ("Can I take a loan against my policy?", "policy_loan_restriction"),
        ("What if I miss my monthly premium due date?", "grace_period_rules"),
        ("Can I use the smart exit option?", "smart_exit_benefit"),
        ("How do I take a premium break?", "premium_break_standard"),
    ],
)
def test_policy_paraphrases(policy_settings, question, expected):
    kb = KnowledgeBase(policy_settings.kb_path, policy_settings.index_path)
    hits = kb.search(question)
    assert hits and hits[0].faq.id == expected


@pytest.mark.parametrize("question", [EXACT_QUESTION, NO_CLAIM_QUESTION])
def test_exact_reported_question_returns_conditional_source_answer(policy_settings, question):
    with TestClient(create_app(policy_settings)) as client:
        result = client.post("/api/ask", json={"question": question})
        assert result.status_code == 200
        body = result.json()
        assert body["mode"] == "faq"
        assert "100 percent" in body["answer"]
        assert "chose the Return of Premium option when you bought" in body["answer"]
        assert "didn't choose that option" in body["answer"]
        assert "no maturity payout" in body["answer"]
        assert not body["needs_human"]
        assert body["sources"][0]["file"] == "policy_booklets.txt"
        assert "MATURITY_BENEFIT:" in body["sources"][0]["text"]
        assert "[0.1.6]" in body["sources"][0]["text"]
        status = client.get("/api/status").json()
        assert status["knowledge_base"]["name"] == "policy_booklets.txt"
        assert status["knowledge_base"]["entry_count"] == 32
        assert all("shop" not in s["question"] for s in status["knowledge_base"]["suggestions"])


@pytest.mark.asyncio
async def test_hindi_policy_answer_and_self_contained_topic_change(policy_settings):
    agent = Assistant(policy_settings)
    try:
        result = await agent.answer(
            AskRequest(question="पॉलिसी की अवधि खत्म होने पर मेरे प्रीमियम वापस मिलेंगे?", language="hi")
        )
        assert result.language == "hi"
        assert "100 प्रतिशत" in result.answer
        assert "शून्य" in result.answer
        assert "[0.1.6]" in result.sources[0].text
        changed = await agent.answer(
            AskRequest(
                question="Is it possible to borrow money?",
                history=[Message(role="user", content=EXACT_QUESTION)],
            )
        )
        assert changed.sources[0].id == "policy_loan_restriction"
    finally:
        await agent.close()


def test_updated_source_invalidates_index_and_stale_translation(policy_settings, tmp_path):
    source = tmp_path / "policy_booklets.txt"
    source.write_text(policy_settings.kb_path.read_text())
    annotation = source.with_suffix(".answers.json")
    annotation.write_text(policy_settings.kb_path.with_suffix(".answers.json").read_text())
    before = KnowledgeBase(source, policy_settings.index_path)
    source.write_text(
        source.read_text().replace("maturity benefit is 100%", "maturity benefit is 90%")
    )
    after = KnowledgeBase(source, policy_settings.index_path)
    assert before.version != after.version
    hit = after.search(EXACT_QUESTION)[0].faq
    assert "90%" in hit.answer_en
    assert hit.answer_hi is None  # Never speak an outdated Hindi benefit amount.
    assert hit.voice_answer_en is None  # Nor an outdated English paraphrase.
    assert "90%" in hit.source_text
    assert after.by_id["policy_overview"].voice_answer_en is None
    with TestClient(create_app(policy_settings.model_copy(update={"kb_path": source}))) as client:
        for question in (EXACT_QUESTION, "What is this policy about?"):
            answer = client.post("/api/ask", json={"question": question}).json()["answer"]
            assert "90%" in answer
            assert "100 percent" not in answer


@pytest.mark.asyncio
async def test_room_rent_question_is_not_answered_from_unrelated_clauses_after_json_retry(
    policy_settings,
):
    from pydantic import SecretStr

    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"code": "json_validate_failed"}})
        content = json.dumps({"in_scope": False, "search_query": ""})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    agent = Assistant(
        policy_settings.model_copy(update={"llm_provider": "groq", "groq_api_key": SecretStr("test")}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        result = await agent.answer(
            AskRequest(question="Is there any room rent limit cap associated with this policy?")
        )
        assert result.mode == "no_match"
        assert result.needs_human
        assert not result.sources
        assert len(calls) == 2
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_policy_model_failure_uses_spoken_fallback_with_original_citation(policy_settings):
    def unavailable(request):
        return httpx.Response(503)

    agent = Assistant(
        policy_settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(unavailable)),
    )
    try:
        result = await agent.answer(AskRequest(question=EXACT_QUESTION))
        assert result.mode == "fallback"
        assert "100 percent" in result.answer
        assert "when you bought the policy" in result.answer
        assert "no maturity payout" in result.answer
        assert "100%" in result.sources[0].text
        assert "selected at inception" in result.sources[0].text
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_stale_translation_falls_back_to_current_source_language(policy_settings, tmp_path):
    source = tmp_path / "policy_booklets.txt"
    source.write_text(policy_settings.kb_path.read_text())
    annotations = json.loads(policy_settings.kb_path.with_suffix(".answers.json").read_text())
    annotations["maturity_benefit"]["source_sha256"] = "stale"
    source.with_suffix(".answers.json").write_text(json.dumps(annotations))
    agent = Assistant(policy_settings.model_copy(update={"kb_path": source}))
    try:
        answer = await agent.answer(AskRequest(question=EXACT_QUESTION, language="hi"))
        assert answer.language == "en"
        assert "100%" in answer.answer
    finally:
        await agent.close()


def test_policy_does_not_answer_unrelated_queries(policy_settings):
    kb = KnowledgeBase(policy_settings.kb_path, policy_settings.index_path)
    assert not kb.search("What is the weather in Tokyo?")
    assert not kb.search("What is your online shop delivery tracking number?")


@pytest.mark.parametrize(
    "question,language",
    [
        ("What's the name of the policy?", "en"),
        ("What’s the name of the policy?", "en"),
        ("What is the policy's name?", "en"),
        ("Tell me the policy name", "en"),
        ("What is this insurance plan called?", "en"),
        ("What is the UIN of this product?", "en"),
        ("इस पॉलिसी का नाम क्या है?", "hi"),
        ("policy ka naam kya hai", "en"),
    ],
)
def test_policy_identity_comes_from_the_name_clause(policy_settings, question, language):
    with TestClient(create_app(policy_settings)) as client:
        body = client.post("/api/ask", json={"question": question, "language": language}).json()
        assert body["mode"] == "faq"
        assert body["language"] == language
        assert [source["id"] for source in body["sources"]] == ["product_name"]
        assert "101N193V01" in body["answer"]
        assert "HDFC Life Click 2 Protect Elite Pro" in body["sources"][0]["text"]
        if language == "en":
            assert "HDFC Life Click 2 Protect Elite Pro" in body["answer"]


@pytest.mark.parametrize(
    "question",
    ["What is my policy number?", "What is the name of my nominee?", "Can I change my name?"],
)
def test_policy_identity_does_not_answer_personal_account_questions(policy_settings, question):
    with TestClient(create_app(policy_settings)) as client:
        body = client.post("/api/ask", json={"question": question}).json()
        assert body["mode"] == "no_match"
        assert not body["sources"]


@pytest.mark.parametrize("ending", ["in between", "midway"])
def test_nonpayment_answer_includes_grace_period_and_paid_up_conditions(policy_settings, ending):
    with TestClient(create_app(policy_settings)) as client:
        body = client.post(
            "/api/ask", json={"question": f"What happens if I stop paying the premium {ending}?"}
        ).json()
        assert body["mode"] == "faq"
        assert {source["id"] for source in body["sources"]} == {
            "grace_period_rules",
            "paid_up_qualification",
        }
        assert "30 days" in body["answer"]
        assert "15 days" in body["answer"]
        assert "at least one full year" in body["answer"]
        assert "zero value" in body["answer"]
        assert len(body["answer"]) <= 1200  # speech request limit
        assert all(source["file"] == "policy_booklets.txt" for source in body["sources"])


def test_overview_after_nonpayment_is_sourced_and_does_not_answer_about_loans(policy_settings):
    with TestClient(create_app(policy_settings)) as client:
        question = "What happens if I stop paying the premium midway?"
        previous = client.post("/api/ask", json={"question": question}).json()
        request = {
            "question": "What is this policy about?",
            "history": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": previous["answer"]},
            ],
        }
        body = client.post("/api/ask", json=request).json()
        assert body["mode"] == "faq"
        assert "Click 2 Protect Elite Pro" in body["answer"]
        assert "life insurance plan" in body["answer"]
        assert "nominee" in body["answer"]
        assert "otherwise, there's no maturity payout" in body["answer"]
        assert "UIN" not in body["answer"]
        assert "loan" not in body["answer"].lower()
        assert {s["id"] for s in body["sources"]} == {
            "product_name",
            "plan_type",
            "death_benefit_summary",
            "maturity_benefit",
        }
        assert len(body["answer"]) <= 1200
        assert client.post("/api/ask", json=request).json()["cached"]
        specific = client.post(
            "/api/ask", json={"question": "Can I take a loan against my policy?"}
        ).json()
        assert [s["id"] for s in specific["sources"]] == ["policy_loan_restriction"]


def test_hindi_grouped_answers_preserve_source_conditions(policy_settings):
    with TestClient(create_app(policy_settings)) as client:
        for question, expected in [
            (
                "यह पॉलिसी किस बारे में है?",
                {"product_name", "plan_type", "death_benefit_summary", "maturity_benefit"},
            ),
            (
                "प्रीमियम बंद करने पर पॉलिसी का क्या होता है?",
                {"grace_period_rules", "paid_up_qualification"},
            ),
        ]:
            body = client.post("/api/ask", json={"question": question, "language": "hi"}).json()
            assert body["language"] == "hi"
            assert {s["id"] for s in body["sources"]} == expected
            assert "आरओपी" in body["answer"]
            assert len(body["answer"]) <= 1200


def test_linked_context_and_overview_rebuild_from_changed_source(policy_settings, tmp_path):
    source = tmp_path / "policy_booklets.txt"
    source.write_text(
        policy_settings.kb_path.read_text().replace("30 days for yearly", "45 days for yearly")
    )
    source.with_suffix(".answers.json").write_text(
        policy_settings.kb_path.with_suffix(".answers.json").read_text()
    )
    settings = policy_settings.model_copy(update={"kb_path": source})
    with TestClient(create_app(settings)) as client:
        body = client.post(
            "/api/ask",
            json={"question": "What happens if I stop paying premiums?", "language": "hi"},
        ).json()
        # Never mix an outdated translation with the changed grace period.
        assert body["language"] == "en"
        assert "45 days" in body["answer"]
        assert "45 days" in body["sources"][0]["text"]


def test_missing_overview_source_fails_clearly(policy_settings, tmp_path):
    source = tmp_path / "policy.txt"
    source.write_text(policy_settings.kb_path.read_text())
    annotations = json.loads(policy_settings.kb_path.with_suffix(".answers.json").read_text())
    annotations["policy_overview"]["source_ids"].append("nonexistent_clause")
    source.with_suffix(".answers.json").write_text(json.dumps(annotations))
    with pytest.raises(ValueError, match="Invalid overview sources"):
        KnowledgeBase(source, policy_settings.index_path)


def test_retrieval_mode_is_explicit_when_models_are_missing(policy_settings, tmp_path):
    settings = policy_settings.model_copy(
        update={
            "embedding_dir": tmp_path / "missing",
            "reranker_dir": tmp_path / "missing",
            "retrieval_mode": "auto",
        }
    )
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/status").json()
        assert status["retrieval_mode"] == "lexical"
        assert status["knowledge_base"]["entry_count"] == 32
        assert status["knowledge_base"]["passage_count"] == 33
    with pytest.raises(ValueError, match="setup_retrieval"):
        Assistant(settings.model_copy(update={"retrieval_mode": "hybrid"}))
