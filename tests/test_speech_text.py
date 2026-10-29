import pytest

from src.speech_text import speech_parts


def test_commas_semicolons_and_conditions_stay_in_one_utterance():
    text = "You're covered, subject to payment; if you stop paying, the plan's conditions apply."
    assert [part.text for part in speech_parts(text)] == [text]


def test_numbers_abbreviations_and_identifiers_are_not_sentence_boundaries():
    text = "Dr. Shah pays Rs. 1,000.50 at 10:30; code 101N193V01 earns 5%."
    parts = speech_parts(text)
    assert len(parts) == 1
    assert "Rs. 1,000.50 at 10:30" in parts[0].text
    assert "1 0 1 N 1 9 3 V 0 1" in parts[0].text
    assert "5 percent" in parts[0].text


def test_long_sentences_remain_bounded_without_dropping_words():
    words = [f"word{i}" for i in range(130)]
    parts = speech_parts(" ".join(words))
    assert all(len(part.text.split()) <= 48 for part in parts)
    assert " ".join(part.text.rstrip(".,") for part in parts).split() == words
    with pytest.raises(ValueError):
        speech_parts("Hello", max_words=0)
    assert len(speech_parts("and then continue", max_words=1)) == 3


def test_short_answer_lead_stays_with_its_explanation():
    text = "Yes. You can keep the policy active by paying within the grace period."
    assert [part.text for part in speech_parts(text)] == [text]


def test_hindi_and_question_intonation_survive():
    text = "अगर आपने यह विकल्प चुना है, तो भुगतान मिलेगा; बाकी शर्तें भी लागू होंगी।"
    assert [p.text for p in speech_parts(text)] == [text]
    assert speech_parts("Would you like me to explain the options?")[0].text.endswith("?")
    assert speech_parts(" ; ... ") == []


def test_long_sentence_prefers_existing_break_without_splitting_parenthetical():
    text = (
        "You can cancel the contract after the agreed period "
        "(excluding the final five years) for a full premium refund."
    )
    parts = speech_parts(text, max_words=18)
    assert parts[-1].text == "for a full premium refund."
    assert "(excluding the final five years)" in parts[0].text
    assert " ".join(part.text.rstrip(".,") for part in parts) == text.rstrip(".")
