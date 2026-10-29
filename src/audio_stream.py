"""Bounded PCM transport between synchronous speech engines and async HTTP."""

import asyncio
import base64
import concurrent.futures
import io
import json
import logging
import threading
import time
import wave

log = logging.getLogger(__name__)


class SpeechCancelled(Exception):
    pass


def pcm_packet(pcm: bytes, rate: int) -> dict:
    if not pcm or len(pcm) % 2 or not 8000 <= rate <= 48000:
        raise ValueError("Invalid speech audio")
    return {"type": "audio", "sample_rate": rate, "pcm": base64.b64encode(pcm).decode()}


def pcm_wav(pcm: bytes, rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm)
    return output.getvalue()


def wav_packets(audio: bytes):
    with wave.open(io.BytesIO(audio)) as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("Expected mono PCM16 audio")
        rate = wav.getframerate()
        while pcm := wav.readframes(rate // 2):
            yield pcm_packet(pcm, rate)


class SpeechStream:
    """Keep inference on one thread, with bounded buffering and cancellation."""

    def __init__(self, generate, release):
        self.cancel = threading.Event()
        self.messages = asyncio.Queue(maxsize=4)
        self.loop = asyncio.get_running_loop()
        self.started = time.perf_counter()
        self.worker = asyncio.create_task(asyncio.to_thread(self._produce, generate))
        self.worker.add_done_callback(lambda _: release())

    def _send(self, message):
        pending = asyncio.run_coroutine_threadsafe(self.messages.put(message), self.loop)
        while not self.cancel.is_set():
            try:
                pending.result(timeout=0.1)
                return
            except concurrent.futures.TimeoutError:
                continue
        pending.cancel()

    def _produce(self, generate):
        iterator = None
        try:
            iterator = generate(self.cancel)
            first = True
            for packet in iterator:
                if self.cancel.is_set():
                    break
                if first:
                    log.info(
                        "speech_first_audio",
                        extra={"latency_ms": round((time.perf_counter() - self.started) * 1000)},
                    )
                    first = False
                self._send(packet)
            if not self.cancel.is_set():
                self._send({"type": "done"})
        except SpeechCancelled:
            pass
        except Exception as exc:
            log.warning("speech_stream_failed", extra={"reason": type(exc).__name__})
            self._send(
                {
                    "type": "error",
                    "error": {
                        "code": "speech_failed",
                        "message": "Speech could not finish. Please try again.",
                    },
                }
            )
        finally:
            if iterator is not None:
                try:
                    iterator.close()
                except Exception as exc:
                    log.warning("speech_cancel_failed", extra={"reason": type(exc).__name__})
            if not self.cancel.is_set():
                self._send(None)

    async def body(self):
        try:
            yield json.dumps({"type": "start", "encoding": "pcm_s16le", "channels": 1}) + "\n"
            while (message := await self.messages.get()) is not None:
                yield json.dumps(message) + "\n"
        finally:
            self.cancel.set()
