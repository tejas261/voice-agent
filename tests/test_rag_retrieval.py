from src.config import Settings
from src.rag_engine import KnowledgeBase
from src.rag_retrieval import RAGRetriever


def test_context_deduplicates_overviews_and_keeps_required_conditions(tmp_path):
    settings = Settings(index_path=tmp_path / "index.sqlite3", retrieval_mode="lexical")
    kb = KnowledgeBase(settings.kb_path, settings.index_path)
    retriever = RAGRetriever(kb, settings)
    context = retriever._pack(["policy_overview", "product_name", "paid_up_qualification"], "en")
    ids = [document.id for document in context.documents]
    assert len(ids) == len(set(ids))
    assert "product_name" in ids
    assert "paid_up_qualification" in ids
    assert "grace_period_rules" in ids
    assert all(record["text"] == kb.by_id[record["id"]].answer_en for record in context.passages)


def test_context_budget_never_truncates_a_required_bundle(tmp_path):
    settings = Settings(
        index_path=tmp_path / "index.sqlite3",
        retrieval_mode="lexical",
        context_max_sources=2,
        context_max_chars=1500,
    )
    kb = KnowledgeBase(settings.kb_path, settings.index_path)
    retriever = RAGRetriever(kb, settings)
    context = retriever._pack(["policy_overview", "paid_up_qualification"], "en")
    assert {d.id for d in context.documents} == {"grace_period_rules", "paid_up_qualification"}
    assert all(p["text"] == kb.by_id[p["id"]].answer_en for p in context.passages)


def test_personal_account_records_are_not_substituted_with_product_facts(settings):
    kb = KnowledgeBase(settings.kb_path, settings.index_path)
    retriever = RAGRetriever(kb, settings)
    assert not retriever.retrieve("What is my order status?").documents
