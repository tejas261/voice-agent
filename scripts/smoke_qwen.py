"""Real local Qwen cloning smoke test against a running server; synthetic sample only."""

import io
import json
import sys
import time
import wave
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import ROOT, Settings  # noqa: E402


def reference_audio(settings):
    import numpy as np
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    options = ort.SessionOptions()
    options.intra_op_num_threads = settings.cpu_threads
    session = ort.InferenceSession(
        str(settings.kokoro_model), sess_options=options, providers=["CPUExecutionProvider"]
    )
    model = Kokoro.from_session(session, str(settings.kokoro_voices))
    transcript = (
        "Hello, my name is Adam. I am here to help you understand your policy. "
        "Please tell me how I can help you today."
    )
    samples, rate = model.create(transcript, voice="am_adam", lang="en-us")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue(), transcript


def main():
    settings = Settings.from_env()
    token = settings.api_token.get_secret_value()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    directory = ROOT / "runtime/qwen-smoke"
    directory.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=200) as client:
        status = client.get("/api/voices")
        status.raise_for_status()
        assert status.json()["cloning"]["available"], status.json()
        reference, transcript = reference_audio(settings)
        (directory / "synthetic-reference.wav").write_bytes(reference)
        response = client.post(
            "/api/voices",
            data={"name": "Synthetic smoke test", "transcript": transcript},
            files={"audio": ("sample.wav", reference, "audio/wav")},
        )
        response.raise_for_status()
        voice_id = response.json()["id"]
        results = []
        try:
            answer = client.post(
                "/api/ask", json={"question": "Can I take a loan against my policy?", "language": "en"}
            )
            answer.raise_for_status()
            assert answer.json()["sources"][0]["id"] == "policy_loan_restriction"
            for index, (text, keywords) in enumerate([
                ("Hello. I am your support assistant. How can I help you today?", ["support", "help"]),
                (answer.json()["answer"], ["loan", "available"]),
            ]):
                started = time.perf_counter()
                response = client.post(
                    "/api/speak", json={"text": text, "language": "en", "voice_id": voice_id}
                )
                response.raise_for_status()
                audio = response.content
                assert response.headers["x-voice-id"] == voice_id
                with wave.open(io.BytesIO(audio), "rb") as wav:
                    duration = wav.getnframes() / wav.getframerate()
                elapsed = round(time.perf_counter() - started, 2)
                (directory / f"cloned-{index}.wav").write_bytes(audio)
                transcription = client.post(
                    "/api/transcribe?language=en", content=audio,
                    headers={"Content-Type": "audio/wav"},
                )
                transcription.raise_for_status()
                heard = transcription.json()["text"]
                assert all(word in heard.lower() for word in keywords), heard
                started = time.perf_counter()
                cached = client.post(
                    "/api/speak", json={"text": text, "language": "en", "voice_id": voice_id}
                )
                cached.raise_for_status()
                assert cached.content == audio
                results.append({
                    "synthesis_seconds": elapsed, "audio_seconds": round(duration, 2),
                    "transcript": heard, "cache_ms": round((time.perf_counter() - started) * 1000),
                })
                print(json.dumps(results[-1]), flush=True)
            rejected = client.post(
                "/api/speak", json={"text": "नमस्ते", "language": "hi", "voice_id": voice_id}
            )
            assert rejected.status_code == 422, rejected.text
            (directory / "result.json").write_text(json.dumps(results, indent=2))
        finally:
            deleted = client.delete(f"/api/voices/{voice_id}")
            deleted.raise_for_status()
            assert client.post(
                "/api/speak", json={"text": "Hello", "language": "en", "voice_id": voice_id}
            ).status_code == 404
    print("Real Qwen cloning, transcription, cache, Hindi guard and deletion passed.")


if __name__ == "__main__":
    main()
