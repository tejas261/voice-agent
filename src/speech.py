"""Optional local models. Audio stays in memory and is never logged or saved."""

import io
import threading
import wave
from collections import OrderedDict
from math import gcd

from src.audio_stream import wav_packets
from src.config import Settings
from src.speech_text import speech_parts


class SpeechService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ready = False
        self.lock = threading.Lock()
        self.audio_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self.cache_bytes = 0

    def load(self):
        import onnxruntime as ort
        from faster_whisper import WhisperModel
        from kokoro_onnx import Kokoro

        if not self.settings.kokoro_model.is_file() or not self.settings.kokoro_voices.is_file():
            raise RuntimeError("Run python scripts/setup_models.py before enabling voice")
        self.stt = WhisperModel(
            self.settings.whisper_model,
            device=self.settings.whisper_device,
            compute_type=self.settings.whisper_compute_type,
            cpu_threads=self.settings.cpu_threads,
            num_workers=1,
            local_files_only=True,
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.settings.cpu_threads
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(self.settings.kokoro_model),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.tts = Kokoro.from_session(session, str(self.settings.kokoro_voices))
        voices = self.tts.get_voices()
        if self.settings.tts_en_voice not in voices or self.settings.tts_hi_voice not in voices:
            raise RuntimeError("Configured English or Hindi voice is missing from the voice pack")
        # Pay VAD/model initialization cost before the UI reports voice ready.
        self.transcribe(self.synthesize("Hello. How can I help you?", "en"), "en")
        self.ready = True

    def transcribe(self, body: bytes, language: str) -> dict:
        import numpy as np
        from scipy.signal import resample_poly

        try:
            with wave.open(io.BytesIO(body)) as wav:
                rate = wav.getframerate()
                frames = wav.getnframes()
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or not 8000 <= rate <= 48000:
                    raise ValueError("Audio must be mono, 16-bit PCM WAV at 8–48 kHz")
                if not 0 < frames <= rate * self.settings.max_audio_seconds:
                    raise ValueError(
                        f"Record between 0 and {self.settings.max_audio_seconds} seconds"
                    )
                pcm = wav.readframes(frames)
                if len(pcm) != frames * 2:
                    raise ValueError("Truncated WAV audio")
        except (wave.Error, EOFError) as exc:
            raise ValueError("Invalid PCM WAV audio") from exc
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if rate != 16000:
            factor = gcd(rate, 16000)
            samples = resample_poly(samples, 16000 // factor, rate // factor).astype(np.float32)
        if float(np.max(np.abs(samples))) < 0.003:
            return {"text": "", "language": language}
        with self.lock:
            segments, _ = self.stt.transcribe(
                samples,
                language=language,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt="यह हिन्दी में ग्राहक सहायता की बातचीत है।" if language == "hi" else None,
                vad_parameters={"min_silence_duration_ms": 400},
            )
            text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.8).strip()
        return {"text": text[:1000], "language": language}

    def synthesize(self, text: str, language: str) -> bytes:
        import numpy as np

        key = (language, text)
        with self.lock:
            if key in self.audio_cache:
                self.audio_cache.move_to_end(key)
                return self.audio_cache[key]
            voice = self.settings.tts_hi_voice if language == "hi" else self.settings.tts_en_voice
            samples, rate = self.tts.create(
                text, voice=voice, lang="hi" if language == "hi" else "en-us", speed=1.0
            )
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            audio = buffer.getvalue()
            self._cache_audio(key, audio)
            return audio

    def _cache_audio(self, key, audio):
        self.audio_cache[key] = audio
        self.cache_bytes += len(audio)
        while self.cache_bytes > 16 * 1024 * 1024 or len(self.audio_cache) > 64:
            _, old = self.audio_cache.popitem(last=False)
            self.cache_bytes -= len(old)

    def stream(self, text, language, cancel):
        for part in speech_parts(text):
            if cancel.is_set():
                return
            audio = self.synthesize(part.text, language)
            for packet in wav_packets(audio):
                if cancel.is_set():
                    return
                yield packet
