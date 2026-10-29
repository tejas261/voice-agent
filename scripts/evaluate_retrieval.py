"""Evaluate real local retrieval models against reviewed positive/negative cases."""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.rag_engine import KnowledgeBase  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/fixtures/retrieval_cases.json")
    parser.add_argument("--report", type=Path, default=ROOT / "runtime/retrieval-evaluation.json")
    args = parser.parse_args()
    settings = Settings.from_env()
    kb = KnowledgeBase(
        settings.kb_path,
        ROOT / "runtime/retrieval-evaluation.sqlite3",
        embedding_dir=settings.embedding_dir,
        reranker_dir=settings.reranker_dir,
        retrieval_mode="hybrid",
        cpu_threads=settings.cpu_threads,
    )
    cases = json.loads(args.cases.read_text())
    failed, timings, outcomes = [], [], []
    for question, expected in cases:
        started = time.perf_counter()
        hits = kb.search(question)
        timings.append((time.perf_counter() - started) * 1000)
        actual = hits[0].faq.id if hits else None
        outcomes.append({"question": question, "expected": expected, "actual": actual})
        if actual != expected:
            ranked = kb.rank_candidates(question)
            failure = {
                "question": question,
                "expected": expected,
                "actual": actual,
                "candidates": [
                    {
                        "id": h.faq.id,
                        "score": round(h.score, 3),
                        "semantic": round(h.semantic_score, 3),
                        "lexical": round(h.lexical_score, 3),
                        "rerank": round(h.rerank_score, 3),
                        "coverage": round(h.coverage, 3),
                    }
                    for h in ranked[:3]
                ],
            }
            failed.append(failure)
            print(json.dumps(failure, ensure_ascii=False), flush=True)
    timings.sort()
    answers = defaultdict(list)
    for entry in kb.faqs:
        answers[" ".join(entry.answer_en.casefold().split())].append(entry.id)
    report = {
        "reranker": settings.reranker_dir.name,
        "knowledge_version": kb.version,
        "cases": len(cases),
        "passed": len(cases) - len(failed),
        "failures": failed,
        "median_ms": round(timings[len(timings) // 2], 1),
        "p95_ms": round(timings[int(len(timings) * 0.95)], 1),
        "answerable_cases": sum(expected is not None for _, expected in cases),
        "correct_answers": sum(
            r["expected"] is not None and r["expected"] == r["actual"] for r in outcomes
        ),
        "unsupported_cases": sum(expected is None for _, expected in cases),
        "correct_refusals": sum(r["expected"] is None and r["actual"] is None for r in outcomes),
        "wrong_answers": sum(
            r["actual"] is not None and r["actual"] != r["expected"] for r in outcomes
        ),
        "knowledge_audit": {
            "source_clauses": len(kb.faqs),
            "searchable_entries": len(kb.entries),
            "entries_without_evaluation": sorted(
                set(kb.by_id) - {expected for _, expected in cases}
            ),
            "entries_without_question_examples": [f.id for f in kb.faqs if len(f.questions) < 2],
            "missing_or_stale_hindi": [f.id for f in kb.faqs if not f.answer_hi],
            "duplicate_answer_groups": [ids for ids in answers.values() if len(ids) > 1],
        },
        "results": outcomes,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        f"{report['passed']}/{len(cases)} passed; median {report['median_ms']} ms, p95 {report['p95_ms']} ms"
    )
    print(
        f"Correct answers: {report['correct_answers']}/{report['answerable_cases']}; "
        f"correct refusals: {report['correct_refusals']}/{report['unsupported_cases']}; "
        f"wrong answers: {report['wrong_answers']}"
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
