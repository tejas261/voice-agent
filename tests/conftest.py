import pytest

from src.config import ROOT, Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        kb_path=ROOT / "tests/fixtures/faqs.json",
        index_path=tmp_path / "knowledge.sqlite3",
        businesses_dir=tmp_path / "businesses",
        retrieval_mode="lexical",
    )
