"""Bounded IPC to an isolated, persistent local Qwen3-TTS worker."""

import base64
import json
import logging
import os
import platform
import queue
import subprocess
import threading
import time
import uuid
from collections import OrderedDict

from src.audio_stream import SpeechCancelled, pcm_packet, pcm_wav, wav_packets
from src.config import ROOT, Settings
from src.voices import VoiceStore

log = logging.getLogger(__name__)


class ClonedSpeech:
    def __init__(self, settings: Settings, store: VoiceStore):
        self.settings, self.store = settings, store
        self.lock = threading.RLock()
        self.process = None
        self.replies = None
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.prepared_voice = None

    def status(self):
        available = (
            platform.system() == "Darwin"
            and platform.machine() == "arm64"
            and self.settings.qwen_python.is_file()
            and (self.settings.qwen_model_dir / ".ready").is_file()
        )
        return {
            "available": available,
            "loaded": self.process is not None and self.process.poll() is None,
            "backend": "qwen3-tts-mlx",
            "languages": ["en"],
            "reason": None if available else "Voice cloning is not installed on this server yet.",
        }

    def _receive(self, deadline, cancel=None):
        while True:
            if cancel is not None and cancel.is_set():
                raise SpeechCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Cloned speech took too long. Try the standard voice")
            try:
                message = self.replies.get(timeout=min(0.1, remaining))
                break
            except queue.Empty:
                continue
        if message is None:
            raise RuntimeError("The local Qwen voice worker stopped. Try again")
        result = json.loads(message)
        if result.get("retries"):
            log.info("speech_phrases_recovered", extra={"retries": result["retries"]})
        if result.get("error"):
            log.warning(
                "qwen_worker_error",
                extra={
                    "reason": result.get("code", result["error"]),
                    "audio_tokens": result.get("audio_tokens"),
                    "audio_samples": result.get("audio_samples"),
                },
            )
            raise RuntimeError(
                "The local Qwen voice could not be generated. Try again or select Standard"
            )
        return result

    def _send(self, request):
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        self.process.stdin.flush()

    def _reference(self, voice_id):
        profile = self.store.get(voice_id)
        return {
            "voice_id": voice_id,
            "ref_text": profile.transcript,
            "ref_audio": str(self.store.reference_path(voice_id)),
        }

    def prepare(self, voice_id):
        reference = self._reference(voice_id)
        # Speculative warm-up must not queue behind active conversation audio.
        if not self.lock.acquire(blocking=False):
            return False
        try:
            deadline = time.monotonic() + self.settings.qwen_timeout_seconds
            self._start(deadline)
            if self.prepared_voice != voice_id:
                self._send({**reference, "op": "prepare"})
                if not self._receive(deadline).get("prepared"):
                    raise RuntimeError("Voice could not be prepared")
                self.prepared_voice = voice_id
            return True
        except Exception:
            self._stop()
            raise
        finally:
            self.lock.release()

    def stream(self, text, language, voice_id, cancel):
        if language != "en":
            raise ValueError("Qwen voice cloning supports English; choose Standard for Hindi")
        with self.lock:
            reference = self._reference(voice_id)
            key = (voice_id, language, text)
            if key in self.cache:
                self.cache.move_to_end(key)
                for packet in wav_packets(self.cache[key]):
                    if cancel.is_set():
                        return
                    yield packet
                return
            request_id = uuid.uuid4().hex
            deadline = time.monotonic() + self.settings.qwen_timeout_seconds
            sent, finished = False, False
            chunks, rate, total = [], None, 0
            try:
                if cancel.is_set():
                    return
                self._start(deadline)
                if cancel.is_set():
                    return
                self._send({**reference, "id": request_id, "text": text, "stream": True})
                sent = True
                while True:
                    result = self._receive(deadline, cancel)
                    if result.get("id") != request_id:
                        raise RuntimeError("Unexpected speech response")
                    if result.get("done"):
                        finished = True
                        if result.get("cancelled") or cancel.is_set():
                            return
                        break
                    current_rate = result["sample_rate"]
                    pcm = base64.b64decode(result["pcm"], validate=True)
                    packet = pcm_packet(pcm, current_rate)
                    if rate is not None and rate != current_rate:
                        raise ValueError("Speech sample rate changed")
                    rate = current_rate
                    total += len(pcm)
                    if total > rate * 2 * 120:
                        raise ValueError("Speech exceeded duration limit")
                    chunks.append(pcm)
                    yield packet
                if not chunks:
                    raise RuntimeError("No speech generated")
                self._cache(key, pcm_wav(b"".join(chunks), rate))
                self.prepared_voice = voice_id
            except SpeechCancelled:
                return
            except Exception:
                self._stop()
                raise
            finally:
                if sent and not finished and self.process is not None:
                    # The worker checks this between small audio chunks. Drain its
                    # acknowledgement before allowing the next turn to use the pipe.
                    try:
                        self._send({"op": "cancel", "id": request_id})
                        cancel_deadline = time.monotonic() + 10
                        while not self._receive(cancel_deadline).get("done"):
                            pass
                    except Exception:
                        self._stop()

    def _cache(self, key, audio):
        if key in self.cache:
            self.cache_bytes -= len(self.cache.pop(key))
        self.cache[key] = audio
        self.cache_bytes += len(audio)
        while len(self.cache) > 32 or self.cache_bytes > 16 * 1024 * 1024:
            _, old = self.cache.popitem(last=False)
            self.cache_bytes -= len(old)

    def _start(self, deadline):
        if self.process is not None and self.process.poll() is None:
            return
        self._stop()
        if not self.status()["available"]:
            raise RuntimeError(self.status()["reason"])
        replies = queue.Queue()
        self.replies = replies
        self.process = subprocess.Popen(
            [
                str(self.settings.qwen_python),
                str(ROOT / "scripts/qwen_worker.py"),
                "--model-dir",
                str(self.settings.qwen_model_dir),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false",
            },
        )
        stdout = self.process.stdout

        def read():
            try:
                for line in stdout:
                    replies.put(line)
            except (OSError, ValueError):
                # The parent can close the pipe while stopping a timed-out worker.
                pass
            finally:
                replies.put(None)

        threading.Thread(target=read, daemon=True).start()
        if not self._receive(deadline).get("ready"):
            raise RuntimeError("The local Qwen voice worker could not start")
        log.info("qwen_worker_ready")

    def synthesize(self, text: str, language: str, voice_id: str) -> bytes:
        if language != "en":
            raise ValueError(
                "Qwen voice cloning supports English in this app. Choose Standard for Hindi"
            )
        with self.lock:
            # Check profile existence even for cached replies; deleted voices cannot replay.
            profile = self.store.get(voice_id)
            key = (voice_id, language, text)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            deadline = time.monotonic() + self.settings.qwen_timeout_seconds
            try:
                self._start(deadline)
                request = {
                    "text": text,
                    "voice_id": voice_id,
                    "ref_text": profile.transcript,
                    "ref_audio": str(self.store.reference_path(voice_id)),
                }
                self.process.stdin.write((json.dumps(request) + "\n").encode())
                self.process.stdin.flush()
                result = self._receive(deadline)
                audio = base64.b64decode(result["audio"], validate=True)
                if not audio.startswith(b"RIFF") or len(audio) > 10 * 1024 * 1024:
                    raise RuntimeError("Invalid audio from Qwen voice worker")
            except Exception:
                self._stop()
                raise
            self._cache(key, audio)
            return audio

    def delete_voice(self, voice_id: str):
        with self.lock:
            self.store.delete(voice_id)
            for key in list(self.cache):
                if key[0] == voice_id:
                    self.cache_bytes -= len(self.cache.pop(key))
            # Release any reference embeddings held inside the model as well.
            self._stop()

    def _stop(self):
        self.prepared_voice = None
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def close(self):
        with self.lock:
            self._stop()
            self.cache.clear()
            self.cache_bytes = 0
