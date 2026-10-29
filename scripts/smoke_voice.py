"""Exercise real local TTS -> STT -> retrieval. Uses synthetic speech, not a mic."""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.assistant import Assistant  # noqa: E402
from src.config import Settings  # noqa: E402
from src.models import AskRequest  # noqa: E402
from src.speech import SpeechService  # noqa: E402


async def main():
    settings = Settings.from_env().model_copy(update={"llm_provider": "extractive"})
    speech = SpeechService(settings)
    speech.load()
    assistant = Assistant(settings)
    try:
        for language, question, source in [
            (
                "en",
                "Will I get a refund of my premium if I don't claim it during the insurance period?",
                "maturity_benefit",
            ),
            (
                "en",
                "Will I get back my accumulated premium after the term ends?",
                "maturity_benefit",
            ),
            ("hi", "पॉलिसी खत्म होने पर प्रीमियम वापस मिलेंगे?", "maturity_benefit"),
            ("en", "Can I take a loan against my policy?", "policy_loan_restriction"),
        ]:
            audio = speech.synthesize(question, language)
            started = time.perf_counter()
            transcript = speech.transcribe(audio, language)["text"]
            stt_ms = round((time.perf_counter() - started) * 1000)
            result = await assistant.answer(AskRequest(question=transcript, language=language))
            assert result.sources and result.sources[0].id == source, (question, transcript, result)
            started = time.perf_counter()
            answer_audio = speech.synthesize(result.answer, language)
            print(
                json.dumps(
                    {
                        "language": language,
                        "transcript": transcript,
                        "source": source,
                        "stt_ms": stt_ms,
                        "retrieval_ms": result.latency_ms,
                        "tts_ms": round((time.perf_counter() - started) * 1000),
                        "audio_bytes": len(answer_audio),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        await assistant.close()


if __name__ == "__main__":
    asyncio.run(main())
