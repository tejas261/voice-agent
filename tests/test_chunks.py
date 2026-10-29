import pytest

from src.chunks import split_windows
from src.config import Settings


def test_short_clauses_have_no_artificial_overlap():
    text = "Minimum age is 18. Maximum age is 65."
    windows = split_windows(text)
    assert len(windows) == 1
    assert windows[0].text == text


@pytest.mark.parametrize("separator", [" ", "\n", "  "])
def test_long_passages_have_bounded_overlap_without_losing_words(separator):
    words = [f"word{i}" for i in range(150)]
    text = separator.join(words)
    windows = split_windows(text, size=32, overlap=5)
    assert len(windows) > 1
    seen = set()
    for i, window in enumerate(windows):
        assert window.text == text[window.start : window.end]
        chunk_words = window.text.split()
        assert len(chunk_words) <= 32
        if i:
            assert windows[i - 1].text.split()[-5:] == chunk_words[:5]
        seen.update(chunk_words)
    assert seen == set(words)


def test_hindi_and_english_sentence_boundaries_are_preserved():
    text = " ".join(["यह एक छोटा वाक्य है।"] * 12)
    windows = split_windows(text, size=32, overlap=5)
    assert all(window.text.endswith("।") for window in windows)


def test_invalid_chunk_settings_fail_clearly():
    with pytest.raises(ValueError, match="overlap"):
        split_windows("text", size=32, overlap=16)
    with pytest.raises(ValueError, match="overlap"):
        Settings(chunk_size_words=32, chunk_overlap_words=16)
