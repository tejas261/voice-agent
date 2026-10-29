"""Download the local speech and retrieval models once. No API key required."""

import hashlib
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import Settings  # noqa: E402

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def download(name, target, expected_size, expected_hash, base_url=RELEASE):
    if (
        target.exists()
        and target.stat().st_size == expected_size
        and hashlib.sha256(target.read_bytes()).hexdigest() == expected_hash
    ):
        print(f"Already downloaded: {target.name}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {name} ({expected_size // 1_000_000} MB)...", flush=True)
    try:
        with httpx.stream("GET", base_url + name, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        if (
            temporary.stat().st_size != expected_size
            or hashlib.sha256(temporary.read_bytes()).hexdigest() != expected_hash
        ):
            raise RuntimeError(f"Download integrity check failed: {name}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Saved {target.name}; SHA256 {hashlib.sha256(target.read_bytes()).hexdigest()}")


def main():
    from faster_whisper.utils import download_model

    settings = Settings.from_env()
    download(
        "kokoro-v1.0.int8.onnx",
        settings.kokoro_model,
        92361271,
        "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    )
    download(
        "voices-v1.0.bin",
        settings.kokoro_voices,
        28214398,
        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    )
    print(f"Downloading Whisper {settings.whisper_model}...", flush=True)
    download_model(settings.whisper_model)
    if settings.retrieval_mode != "lexical":
        from scripts.setup_retrieval import main as setup_retrieval

        setup_retrieval()
    print(
        "Ready. Start with VOICE_ENABLED=true python -m uvicorn src.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers"
    )


if __name__ == "__main__":
    main()
