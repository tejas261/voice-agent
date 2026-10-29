"""Check retrieval recall, and optionally real grounded model answers (--live)."""

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assistant import Assistant  # noqa: E402
from src.config import Settings  # noqa: E402
from src.models import AskRequest  # noqa: E402


def normalize_answer(text: str) -> str:
    """Compare words and numbers independently of Unicode typography/script."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in text)
    return " ".join(text.split())


class EvaluationLogs(logging.Handler):
    def emit(self, record):
        print(
            json.dumps(
                {
                    "event": record.getMessage(),
                    "reason": getattr(record, "reason", None),
                    "status_code": getattr(record, "status_code", None),
                }
            ),
            flush=True,
        )


async def evaluate(args):
    logging.getLogger("src.assistant").addHandler(EvaluationLogs())
    logging.getLogger("src.assistant").propagate = False
    settings = Settings.from_env().model_copy(update={"llm_provider": "groq"})
    if args.live and not settings.groq_api_key.get_secret_value():
        raise ValueError("Live evaluation requires a configured Groq API key")
    agent = Assistant(settings)
    raw = json.loads(args.cases.read_text())
    cases = []
    for item in raw:
        if isinstance(item, list):
            question, expected = item
            ids = (
                [d.id for d in agent.kb.answer_documents(agent.kb.by_id[expected])]
                if expected
                else []
            )
            item = {"question": question, "expected_sources": ids}
        cases.append(item)
    if args.limit:
        cases = cases[: args.limit]
    results = []
    started = 0
    try:
        for case in cases:
            request = AskRequest(
                **{k: case[k] for k in ("question", "language", "history") if k in case}
            )
            expected = set(case["expected_sources"])
            context = await asyncio.to_thread(
                agent.retriever.retrieve, agent.retrieval_query(request), request.language
            )
            available = {d.id for d in context.documents}
            row = {
                "question": request.question,
                "expected_sources": sorted(expected),
                "context_sources": sorted(available),
                "recall": expected.issubset(available) if expected else None,
            }
            if args.compare_legacy and expected and not request.history:
                old = await asyncio.to_thread(agent.kb.search, request.question)
                row["legacy_sources"] = (
                    [d.id for d in agent.kb.answer_documents(old[0].faq)] if old else []
                )
            if args.live:
                await asyncio.sleep(max(0, args.interval - (time.monotonic() - started)))
                started = time.monotonic()
                repairs_before = agent.usage["retrieval_repairs"]
                answer = await agent.answer(request)
                # Spoken text may contain narrow/non-breaking spaces from the
                # model. Compare wording without treating typography as an error.
                normalized_answer = normalize_answer(answer.answer)
                ids = {source.id for source in answer.sources}
                passed = (
                    expected.issubset(ids)
                    and answer.mode == "groq"
                    and all(
                        re.search(pattern, normalized_answer, re.I)
                        for pattern in case.get("answer_patterns", [])
                    )
                    and not any(
                        re.search(pattern, normalized_answer, re.I)
                        for pattern in case.get("forbidden_patterns", [])
                    )
                    if expected
                    else answer.mode == "no_match" and not ids
                )
                row.update(
                    {
                        "answer": answer.answer,
                        "sources": sorted(ids),
                        "mode": answer.mode,
                        "latency_ms": answer.latency_ms,
                        "retrieval_repairs": agent.usage["retrieval_repairs"] - repairs_before,
                        "passed": passed,
                    }
                )
            else:
                row["passed"] = row["recall"]
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        checked = [row for row in results if row["passed"] is not None]
        report = {
            "model": settings.groq_model if args.live else None,
            "live": args.live,
            "cases": len(results),
            "checks": len(checked),
            "passed": sum(row["passed"] for row in checked),
            "answerable": sum(bool(case["expected_sources"]) for case in cases),
            "initial_context_recall": sum(row["recall"] is True for row in results),
            "recall_stage": "raw query before hosted rewrite",
            "usage": agent.usage,
            "results": results,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        label = (
            "live answer checks"
            if args.live
            else "answerable context recall checks (no generation)"
        )
        print(f"{report['passed']}/{report['checks']} {label}; usage {agent.usage}", flush=True)
        if report["passed"] != report["checks"]:
            raise SystemExit(1)
    finally:
        await agent.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/fixtures/rag_cases.json")
    parser.add_argument("--report", type=Path, default=ROOT / "runtime/rag-evaluation.json")
    parser.add_argument(
        "--live", action="store_true", help="Call Groq; account quota/billing applies"
    )
    parser.add_argument("--compare-legacy", action="store_true")
    parser.add_argument("--interval", type=float, default=35, help="Seconds between live requests")
    parser.add_argument("--limit", type=int)
    asyncio.run(evaluate(parser.parse_args()))
