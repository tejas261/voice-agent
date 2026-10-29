import pytest

from src.query_scope import requires_personal_records


@pytest.mark.parametrize(
    "question",
    [
        "Did I choose the extended warranty?",
        "What shipping option did we select?",
        "Has my payment been processed?",
        "Is our application approved?",
        "Tell me my order status",
        "What is my remaining loan balance?",
        "What is the name of my beneficiary?",
        "What's my nominee's phone number?",
        "Who is the beneficiary on my account?",
        "मेरे खाते का बैलेंस बताएं",
        "क्या मैंने यह विकल्प चुना था?",
    ],
)
def test_customer_record_requests_need_a_separate_data_source(question):
    assert requires_personal_records(question)


@pytest.mark.parametrize(
    "question",
    [
        "What is the name of my policy?",
        "Can I join at age 17?",
        "How do I choose the return of premium option?",
        "Will my nominee receive a lump sum?",
        "What happens if I stop paying premiums?",
        "If I did not choose ROP, do I receive a maturity benefit?",
        "When does education support for my child finish?",
        "अगर मैंने कोई क्लेम नहीं किया तो क्या मेरा प्रीमियम वापस मिलेगा?",
        "मेरे बच्चे की पढ़ाई के लिए क्या लाभ है?",
        "अगर मैंने आरओपी चुना था तो क्या प्रीमियम वापस मिलेगा?",
        "How can I check my order status?",
        "How to find my account number?",
    ],
)
def test_first_person_general_rules_remain_answerable(question):
    assert not requires_personal_records(question)
