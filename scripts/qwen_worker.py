"""Private, persistent Qwen worker with incremental PCM and cooperative cancellation."""

import argparse
import base64
import contextlib
import io
import json
import queue
import sys
import threading
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.clone_audio import PhraseFailure, clone_phrase  # noqa: E402
from src.speech_text import speech_parts  # noqa: E402


def send(value):
    sys.__stdout__.write(json.dumps(value) + "\n")
    sys.__stdout__.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    with contextlib.redirect_stdout(io.StringIO()):
        import mlx.core as mx
        from mlx_audio.tts.utils import load_model
        from mlx_audio.utils import load_audio

        model = load_model(args.model_dir)
    send({"ready": True})
    jobs, cancellations = queue.Queue(), {}
    command_lock = threading.Lock()

    def read_commands():
        for line in sys.stdin:
            request = json.loads(line)
            with command_lock:
                if request.get("op") == "cancel":
                    event = cancellations.get(request.get("id"))
                    if event is not None:
                        event.set()
                else:
                    event = threading.Event()
                    cancellations[request.get("id")] = event
                    jobs.put((request, event))
        jobs.put(None)

    threading.Thread(target=read_commands, daemon=True).start()
    last_voice, reference = None, None
    while (job := jobs.get()) is not None:
        request, cancelled = job
        request_id = request.get("id")
        tokens, total_samples = 0, 0
        retries = 0
        error_code = "model_error"
        try:
            prepare = request.get("op") == "prepare"
            text = "Hello." if prepare else request["text"]
            if not text.strip() or len(text) > 1200:
                error_code = "invalid_text"
                raise ValueError("Invalid text length")
            stream = prepare or request.get("stream", False)
            with contextlib.redirect_stdout(io.StringIO()):
                if last_voice != request["voice_id"]:
                    if hasattr(model, "_icl_cache"):
                        model._icl_cache.clear()
                    reference = load_audio(request["ref_audio"], sample_rate=model.sample_rate)
                    last_voice = request["voice_id"]
                chunks, total_samples, tokens = [], 0, 0

                def emit(pcm, rate):
                    nonlocal total_samples, error_code
                    total_samples += len(pcm) // 2
                    if total_samples > rate * 120:
                        error_code = "generation_limit"
                        raise PhraseFailure("generation_limit")
                    if stream:
                        packet_bytes = int(rate * 0.48) * 2
                        for offset in range(0, len(pcm), packet_bytes):
                            if cancelled.is_set():
                                break
                            send(
                                {
                                    "id": request_id,
                                    "sample_rate": rate,
                                    "pcm": base64.b64encode(
                                        pcm[offset : offset + packet_bytes]
                                    ).decode(),
                                }
                            )
                    else:
                        chunks.append(pcm)

                def generate(**options):
                    return model.generate(
                        **options,
                        ref_audio=reference,
                        ref_text=request["ref_text"],
                        lang_code="english",
                        verbose=False,
                        stream=True,
                        streaming_interval=0.48,
                    )

                for part in speech_parts(text):
                    if cancelled.is_set():
                        break
                    pcm, rate, part_tokens, attempts = clone_phrase(
                        generate,
                        mx.eval,
                        model.speech_tokenizer.decoder.reset_streaming_state,
                        part.text,
                        cancelled,
                        prepare=prepare,
                        # The small fallback keeps whole-sentence validation;
                        # its earlier decoding failures motivated that safeguard.
                        emit=emit
                        if stream
                        and not prepare
                        and getattr(model.config, "tts_model_size", "") == "1b7"
                        else None,
                    )
                    tokens += part_tokens
                    retries += attempts
                    if prepare:
                        break
                    if pcm and not cancelled.is_set():
                        emit(pcm, rate)
                if prepare:
                    send({"prepared": True})
                elif stream:
                    send(
                        {
                            "id": request_id,
                            "done": True,
                            "cancelled": cancelled.is_set(),
                            "retries": retries,
                        }
                    )
                else:
                    if not chunks:
                        error_code = "empty_audio"
                        raise ValueError("No audio generated")
                    output = io.BytesIO()
                    with wave.open(output, "wb") as audio:
                        audio.setnchannels(1)
                        audio.setsampwidth(2)
                        audio.setframerate(rate)
                        audio.writeframes(b"".join(chunks))
                    send(
                        {"audio": base64.b64encode(output.getvalue()).decode(), "retries": retries}
                    )
        except InterruptedError:
            send({"id": request_id, "done": True, "cancelled": True})
        except Exception as exc:
            if isinstance(exc, PhraseFailure):
                error_code = exc.code
                tokens += exc.tokens
                total_samples += exc.samples
            send(
                {
                    "id": request_id,
                    "error": type(exc).__name__,
                    "code": error_code,
                    "audio_tokens": tokens,
                    "audio_samples": total_samples,
                }
            )
        finally:
            model.speech_tokenizer.decoder.reset_streaming_state()
            with command_lock:
                cancellations.pop(request_id, None)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        send({"error": type(exc).__name__})
