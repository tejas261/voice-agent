"""Download pinned, checksum-verified local multilingual retrieval weights."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.setup_models import download  # noqa: E402
from src.config import Settings  # noqa: E402
from src.embeddings import ARTIFACTS, MODEL_URL, RERANKER_ARTIFACTS, RERANKER_URL  # noqa: E402


def main():
    settings = Settings.from_env()
    for target, artifacts, url in [
        (settings.embedding_dir, ARTIFACTS, MODEL_URL),
        (settings.reranker_dir, RERANKER_ARTIFACTS, RERANKER_URL),
    ]:
        for name, size, checksum in artifacts:
            download(name, target / Path(name).name, size, checksum, base_url=url)
    print("Local semantic retrieval is ready. Restart the app to use it.")


if __name__ == "__main__":
    main()
