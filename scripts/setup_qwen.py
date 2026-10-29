"""Download Qwen3-TTS Base for the isolated Apple Silicon cloning worker."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import snapshot_download  # noqa: E402

from src.config import Settings  # noqa: E402

REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
REVISION = "e7dd0585652209fa0d7783659aad4e8a324de11c"
WEIGHTS = {
    "model.safetensors": "b965c581ccf6aa852a4124feeb7a8a111542ee7b213139368b4cc7ba7fd4728b",
    "speech_tokenizer/model.safetensors": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258",
}


def main():
    target = Settings.from_env().qwen_model_dir
    if (target / ".ready").is_file() and (target / ".ready").read_text().strip() != REVISION:
        raise RuntimeError(
            "This directory holds a different model. Set QWEN_MODEL_DIR=models/qwen3-tts-natural "
            "to install the new model and preserve the existing weights."
        )
    print(
        "Downloading Qwen3-TTS 1.7B Base (8-bit) and its speech tokenizer, about 3.1 GB.",
        flush=True,
    )
    snapshot_download(
        REPO,
        revision=REVISION,
        local_dir=target,
        allow_patterns=["*.json", "*.txt", "*.safetensors"],
        max_workers=2,
    )
    for filename, expected in WEIGHTS.items():
        with (target / filename).open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError(f"Qwen model checksum failed: {filename}")
    (target / ".ready").write_text(REVISION)
    print("Qwen model verified. Start the app with ./run.sh.")


if __name__ == "__main__":
    main()
