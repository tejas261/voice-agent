"""Local reference-voice profiles. Customer question recordings are never saved."""

import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
import threading
import wave
from datetime import datetime, timezone
from math import gcd
from pathlib import Path

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
VOICE_ID = re.compile(r"^[a-f0-9]{24}$")


class VoiceProfile(BaseModel):
    id: str = Field(pattern=VOICE_ID.pattern)
    name: str = Field(min_length=1, max_length=60)
    transcript: str = Field(min_length=1, max_length=1000)
    duration_seconds: float = Field(ge=3, le=20)
    created_at: str

    def public(self):
        return {**self.model_dump(exclude={"transcript"}), "languages": ["en"]}


def normalize_reference(body: bytes) -> tuple[bytes, float]:
    import numpy as np
    from scipy.signal import resample_poly

    if len(body) > 3 * 1024 * 1024:
        raise ValueError("Reference audio is too large")
    try:
        with wave.open(io.BytesIO(body)) as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or not 8000 <= rate <= 48000:
                raise ValueError("Use mono 16-bit PCM WAV at 8–48 kHz")
            duration = frames / rate
            if not 3 <= duration <= 20:
                raise ValueError("Use a reference clip between 3 and 20 seconds")
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError("Reference audio is truncated")
    except (wave.Error, EOFError) as exc:
        raise ValueError("Reference must be a valid PCM WAV recording") from exc
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    if np.sqrt(np.mean(samples * samples)) < 0.003:
        raise ValueError("The reference is silent or too quiet. Use a clear speech sample")
    if rate != 24000:
        factor = gcd(rate, 24000)
        samples = resample_poly(samples, 24000 // factor, rate // factor)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue(), round(duration, 2)


class VoiceStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.RLock()

    def _path(self, voice_id: str) -> Path:
        if not VOICE_ID.fullmatch(voice_id):
            raise ValueError("Invalid voice ID")
        path = self.directory / voice_id
        if path.is_symlink():
            raise ValueError("Invalid voice profile")
        return path

    def get(self, voice_id: str) -> VoiceProfile:
        with self.lock:
            path = self._path(voice_id)
            if not (path / "profile.json").is_file() or not (path / "reference.wav").is_file():
                raise KeyError("This voice no longer exists. Choose another voice")
            if (path / "reference.wav").is_symlink() or (path / "profile.json").is_symlink():
                raise ValueError("Invalid voice profile")
            profile = VoiceProfile.model_validate_json((path / "profile.json").read_text())
            if profile.id != voice_id:
                raise ValueError("Invalid voice profile")
            return profile

    def reference_path(self, voice_id: str) -> Path:
        self.get(voice_id)
        return self._path(voice_id) / "reference.wav"

    def list(self) -> list[VoiceProfile]:
        with self.lock:
            profiles = []
            if not self.directory.exists():
                return profiles
            for path in self.directory.iterdir():
                if VOICE_ID.fullmatch(path.name):
                    try:
                        profiles.append(self.get(path.name))
                    except (ValueError, KeyError, OSError):
                        log.warning("invalid_voice_profile_skipped")
            return sorted(profiles, key=lambda profile: profile.created_at)

    def create(self, name: str, transcript: str, body: bytes) -> VoiceProfile:
        name, transcript = name.strip(), transcript.strip()
        if not 1 <= len(name) <= 60 or not 1 <= len(transcript) <= 1000:
            raise ValueError("Add a voice name and the words spoken in the reference clip")
        audio, duration = normalize_reference(body)
        identity = json.dumps([name, transcript], ensure_ascii=False).encode() + b"\0" + audio
        voice_id = hashlib.sha256(identity).hexdigest()[:24]
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = self._path(voice_id)
            if target.exists():
                return self.get(voice_id)
            if len(self.list()) >= 10:
                raise ValueError("You can save up to 10 voices. Delete one before adding another")
            profile = VoiceProfile(
                id=voice_id,
                name=name,
                transcript=transcript,
                duration_seconds=duration,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=self.directory))
            try:
                (temporary / "reference.wav").write_bytes(audio)
                (temporary / "reference.wav").chmod(0o600)
                (temporary / "profile.json").write_text(profile.model_dump_json())
                (temporary / "profile.json").chmod(0o600)
                temporary.rename(target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
            log.info("voice_profile_created")
            return profile

    def delete(self, voice_id: str):
        with self.lock:
            self.get(voice_id)
            shutil.rmtree(self._path(voice_id))
            log.info("voice_profile_deleted")
