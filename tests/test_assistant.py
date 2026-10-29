import json

import httpx
import pytest

from src.assistant import Assistant
from src.models import AskRequest, Message


@pytest.mark.asyncio
async def test_local_hindi_answer_and_cache(settings):
    agent = Assistant(settings)
    try:
        request = AskRequest(question="रिफंड कब मिलेगा?", language="hi")
        first = await agent.answer(request)
        second = await agent.answer(request)
        assert first.mode == "faq"
        assert "5 से 7" in first.answer
        assert first.sources[0].id == "refunds"
        assert second.cached
        assert not first.cached
        different_language = await agent.answer(request.model_copy(update={"language": "en"}))
        assert not different_language.cached
        assert "5 to 7" in different_language.answer
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_no_context_skips_llm(settings):
    def fail(request):
        raise AssertionError("LLM must not be called without sources")

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(fail)),
    )
    try:
        result = await agent.answer(AskRequest(question="Tell me about the weather"))
        assert result.mode == "no_match"
        assert result.needs_human
        assert result.sources == []
    finally:
        await agent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body",
    [
        (429, {}),
        (500, {}),
        (200, {"message": {"content": "not json"}}),
        (200, {"message": {"content": '{"answer":"Invented","source_ids":["fake"]}'}}),
    ],
)
async def test_provider_failure_falls_back_and_does_not_cache(settings, status, body):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        request = AskRequest(question="What is the return policy?")
        for _ in range(2):
            result = await agent.answer(request)
            assert result.mode == "fallback"
            assert "7 days" in result.answer
        assert len(calls) == 2
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_grounded_generation(settings):
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:11434/api/chat"
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"answer":"Return unused goods within 7 days.","source_ids":["returns"]}'
                }
            },
        )

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        result = await agent.answer(AskRequest(question="What is the return policy?"))
        assert result.mode == "ollama"
        assert result.sources[0].id == "returns"
    finally:
        await agent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "groq"])
@pytest.mark.parametrize(
    "language,reply,language_instruction",
    [
        ("en", "You can return unused goods within 7 days.", "English"),
        ("hi", "आप बिना इस्तेमाल किया सामान 7 दिनों में लौटा सकते हैं।", "Hindi in Devanagari"),
    ],
)
async def test_voice_guidance_reaches_answer_model(settings, provider, language, reply, language_instruction):
    from pydantic import SecretStr

    prompts = []

    def handler(request):
        payload = json.loads(request.content)
        if provider == "groq" and payload["max_completion_tokens"] == 300:
            content = json.dumps({"in_scope": True, "search_query": "return policy"})
        else:
            prompts.append(payload["messages"][0]["content"])
            evidence = json.loads(payload["messages"][-1]["content"])
            assert "returns" in {source["id"] for source in evidence["sources"]}
            content = json.dumps({"answer": reply, "source_ids": ["returns"]})
        if provider == "groq":
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(200, json={"message": {"content": content}})

    config = settings.model_copy(
        update={"llm_provider": provider, "groq_api_key": SecretStr("dummy-test-key")}
    )
    agent = Assistant(config, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        result = await agent.answer(AskRequest(question="What is the return policy?", language=language))
        assert result.answer == reply
        assert result.mode == provider
        assert [source.id for source in result.sources] == ["returns"]
        assert len(prompts) == 1
        assert language_instruction in prompts[0]
        assert "one to three short sentences" in prompts[0]
        assert "Focus on what they asked" in prompts[0]
        assert "Do not volunteer other benefits" in prompts[0]
        assert "conditions, exceptions, amounts, and time limits" in prompts[0]
        assert "examples illustrate style only, not facts" in prompts[0]
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_followup_and_cache_isolation(settings):
    agent = Assistant(settings)
    try:
        first = await agent.answer(
            AskRequest(
                question="How long does it take?",
                history=[Message(role="user", content="When will I get my refund?")],
            )
        )
        second = await agent.answer(
            AskRequest(
                question="How long does it take?",
                history=[Message(role="user", content="When will my order arrive?")],
            )
        )
        assert first.sources[0].id == "refunds"
        assert second.sources[0].id == "delivery"
        assert not second.cached
    finally:
        await agent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"])
async def test_groq_adapter(settings, model):
    import json

    from pydantic import SecretStr

    def handler(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer dummy-test-key"
        payload = json.loads(request.content)
        if payload["max_completion_tokens"] == 300:
            assert payload["response_format"]["type"] == "json_object"
            if model.startswith("qwen/"):
                assert payload["reasoning_effort"] == "none"
                assert payload["reasoning_format"] == "hidden"
            else:
                assert payload["reasoning_effort"] == "low"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"in_scope":true,"search_query":"return policy"}'}}
                    ]
                },
            )
        assert payload["max_completion_tokens"] == settings.model_max_tokens
        if "gpt-oss" in model:
            assert payload["reasoning_effort"] == "medium"
            assert payload["response_format"]["type"] == "json_schema"
            schema = payload["response_format"]["json_schema"]
            assert schema["strict"] is True
            assert "returns" in schema["schema"]["properties"]["source_ids"]["items"]["enum"]
        else:
            assert payload["reasoning_effort"] == "low"
            assert payload["reasoning_format"] == "hidden"
            assert payload["response_format"]["type"] == "json_object"
        assert len(payload["messages"]) <= 6
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"answer":"Returns are accepted within 7 days.","source_ids":["returns"]}'
                        }
                    }
                ]
            },
        )

    config = settings.model_copy(
        update={
            "llm_provider": "groq",
            "groq_model": model,
            "groq_api_key": SecretStr("dummy-test-key"),
        }
    )
    agent = Assistant(config, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        result = await agent.answer(AskRequest(question="return policy"))
        assert result.mode == "groq"
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_provider_timeout_uses_faq(settings):
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        result = await agent.answer(AskRequest(question="return policy"))
        assert result.mode == "fallback"
        assert "7 days" in result.answer
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_model_can_answer_retrieved_paraphrase_rejected_by_faq_gates(settings, monkeypatch):
    from src.rag_retrieval import Context

    def handler(request):
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"answer":"Yes, unused goods can be returned within 7 days.",'
                    '"source_ids":["returns"],"supported":true}'
                }
            },
        )

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    source = agent.kb.by_id["returns"]
    monkeypatch.setattr(agent.kb, "search", lambda _: [])
    monkeypatch.setattr(
        agent.retriever,
        "retrieve",
        lambda *args: Context(
            [source], [{"id": source.id, "text": source.answer_en}], "Store returns"
        ),
    )
    try:
        reply = await agent.answer(AskRequest(question="It wasn't the right fit. Can it go back?"))
        assert reply.mode == "ollama"
        assert reply.answer.startswith("Yes")
        assert [s.id for s in reply.sources] == ["returns"]
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_model_can_reject_related_but_insufficient_evidence(settings):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"answer":"This information is not available.","source_ids":[],"supported":false}'
                }
            },
        )

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        reply = await agent.answer(AskRequest(question="What is the return policy?"))
        assert reply.mode == "no_match"
        assert reply.needs_human
        assert not reply.sources
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_missing_topic_is_fetched_before_answering_and_cached(settings, monkeypatch):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        evidence = json.loads(payload["messages"][-1]["content"])
        calls.append(evidence)
        if len(calls) == 1:
            assert "refunds" not in {p["id"] for p in evidence["sources"]}
            assert "refunds" in {p["id"] for p in evidence["topic_index"]}
            assert all(set(p) == {"id", "topic"} for p in evidence["topic_index"])
            answer = {
                "answer": "Need the refund timeline.",
                "supported": False,
                "source_ids": [],
                "missing_source_ids": ["refunds"],
            }
        else:
            assert len(calls) == 2
            assert evidence["topic_index"] == []
            assert (
                next(p for p in evidence["sources"] if p["id"] == "refunds")["text"]
                == agent.kb.by_id["refunds"].answer_en
            )
            answer = {
                "answer": "Refunds take 5 to 7 business days.",
                "supported": True,
                "source_ids": ["refunds"],
                "missing_source_ids": [],
            }
        return httpx.Response(200, json={"message": {"content": json.dumps(answer)}})

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(
        agent.retriever, "retrieve", lambda *args: agent.retriever._pack(["returns"], "en")
    )
    try:
        question = AskRequest(question="When is my money coming back?")
        answer = await agent.answer(question)
        assert answer.mode == "ollama"
        assert [s.id for s in answer.sources] == ["refunds"]
        assert (await agent.answer(question)).cached
        assert len(calls) == 2
        assert agent.usage["retrieval_repairs"] == 1
    finally:
        await agent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected_calls", [("invented-source", 1), ("refunds", 2)])
async def test_invalid_or_repeated_source_requests_are_bounded(
    settings, monkeypatch, requested, expected_calls
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "answer": "Need more evidence.",
                            "supported": False,
                            "source_ids": [],
                            "missing_source_ids": [requested],
                        }
                    )
                }
            },
        )

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "ollama"}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(
        agent.retriever, "retrieve", lambda *args: agent.retriever._pack(["returns"], "en")
    )
    monkeypatch.setattr(agent.kb, "search", lambda _: [])
    try:
        answer = await agent.answer(AskRequest(question="When is my money coming back?"))
        assert answer.mode == "fallback"
        assert "temporarily unavailable" in answer.answer
        assert not answer.sources
        assert not agent.cache
        assert len(calls) == expected_calls
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_incomplete_model_output_is_not_used_or_cached(settings):
    from pydantic import SecretStr

    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": '{"answer":"Made up","source_ids":["returns"],"supported":true}'
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 600},
            },
        )

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "groq", "groq_api_key": SecretStr("test")}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        answer = await agent.answer(AskRequest(question="What is the return policy?"))
        assert answer.mode == "fallback"
        assert "Made up" not in answer.answer
        assert not agent.cache
        assert agent.usage["completion_tokens"] == 600
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_query_rewrite_handles_new_wording_without_new_aliases(settings):
    from pydantic import SecretStr

    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            result = {"in_scope": True, "search_query": "return policy"}
        else:
            evidence = json.loads(payload["messages"][-1]["content"])
            assert "returns" in {p["id"] for p in evidence["sources"]}
            result = {
                "supported": True,
                "source_ids": ["returns"],
                "answer": "Yes, unused goods can be returned within 7 days.",
            }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "groq", "groq_api_key": SecretStr("test")}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        request = AskRequest(question="It arrived but isn't the right fit. May I send it back?")
        reply = await agent.answer(request)
        assert reply.mode == "groq"
        assert [source.id for source in reply.sources] == ["returns"]
        assert (await agent.answer(request)).cached
        assert len(calls) == 2
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_scope_check_stops_unrelated_questions_before_retrieval(settings, monkeypatch):
    from pydantic import SecretStr

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"in_scope":false,"search_query":""}'}}]},
        )

    def fail(*args):
        raise AssertionError("Unrelated questions must not retrieve product facts")

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "groq", "groq_api_key": SecretStr("test")}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(agent.retriever, "retrieve", fail)
    try:
        reply = await agent.answer(AskRequest(question="Can I borrow against my house?"))
        assert reply.mode == "no_match"
        assert not reply.sources
        assert len(calls) == 1
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_invalid_query_plan_falls_back_without_caching(settings):
    from pydantic import SecretStr

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"in_scope":true,"search_query":""}'}}]}
        )

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "groq", "groq_api_key": SecretStr("test")}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        for _ in range(2):
            reply = await agent.answer(AskRequest(question="What is the return policy?"))
            assert reply.mode == "fallback"
            assert "7 days" in reply.answer
        assert not agent.cache
        assert len(calls) == 2
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_personal_record_request_skips_hosted_model(settings):
    def fail(request):
        raise AssertionError("Known private-record requests should not reach the model")

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "groq"}),
        httpx.AsyncClient(transport=httpx.MockTransport(fail)),
    )
    try:
        reply = await agent.answer(AskRequest(question="What is my order status?"))
        assert reply.mode == "no_match"
        assert not reply.sources
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_groq_json_validation_error_retries_and_checks_evidence(settings):
    from pydantic import SecretStr

    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            content = {"in_scope": True, "search_query": "return policy"}
        elif len(payloads) == 2:
            return httpx.Response(400, json={"error": {"code": "json_validate_failed"}})
        else:
            content = {"answer": "You can return unused goods within 7 days.", "source_ids": ["returns"]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "groq", "groq_api_key": SecretStr("test")}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        result = await agent.answer(AskRequest(question="What is the return policy?"))
        assert result.mode == "groq"
        assert [source.id for source in result.sources] == ["returns"]
        assert len(payloads) == 3
        assert payloads[1]["response_format"]["type"] == "json_object"
        assert "response_format" not in payloads[2]
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_short_rate_limit_retries_once_then_returns_grounded_answer(settings):
    from pydantic import SecretStr

    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        result = (
            {"in_scope": True, "search_query": "return policy"}
            if len(calls) == 2
            else {"answer": "Returns are accepted within 7 days.", "source_ids": ["returns"]}
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "groq", "groq_api_key": SecretStr("test")}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        request = AskRequest(question="What is the return policy?")
        reply = await agent.answer(request)
        assert reply.mode == "groq"
        assert [source.id for source in reply.sources] == ["returns"]
        assert (await agent.answer(request)).cached
        assert len(calls) == 3
        assert agent.usage["rate_limit_retries"] == 1
    finally:
        await agent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after,expected_calls", [("0", 2), ("20", 1), ("invalid", 1)])
async def test_rate_limit_failure_is_bounded_and_distinct_from_missing_knowledge(
    settings, monkeypatch, retry_after, expected_calls
):
    from pydantic import SecretStr

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": retry_after})

    agent = Assistant(
        settings.model_copy(update={"llm_provider": "groq", "groq_api_key": SecretStr("test")}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(agent.kb, "search", lambda _: [])
    try:
        reply = await agent.answer(AskRequest(question="Could I send my purchase back?"))
        assert reply.mode == "fallback"
        assert "temporarily unavailable" in reply.answer
        assert "knowledge base" not in reply.answer
        assert not reply.sources
        assert not agent.cache
        assert len(calls) == expected_calls
    finally:
        await agent.close()
