"""Separate static-document questions from requests for private account records."""

import re

# The app has a product/FAQ corpus, not an authenticated customer-record tool.
# Preserve general rules ("Can I join?", "If I miss a payment?") while declining
# requests to determine what a particular customer actually chose or received.
PERSONAL_RECORD_PATTERNS = [
    r"\b(?:did|have)\s+(?:i|we)\s+(?:choose|chosen|select|selected|opt|opted|pay|paid|receive|received)\b",
    r"\b(?:has|have|is|was|were|are)\s+(?:my|our)\b.{0,100}\b(?:approved|credited|settled|processed|delivered|lapsed|expired|renewed|cancelled|canceled|activated)\b",
    r"\b(?:my|our)\s+(?:current\s+)?(?:account|policy|claim|order|payment|premium|refund|application|loan|customer)\s+(?:number|id|balance|status)\b",
    r"\b(?:my|our)\s+(?:remaining|outstanding|current)\b.{0,60}\b(?:balance|premium|loan|refund|payment)\b",
    r"\b(?:name|phone|number|contact|identity)\b.{0,50}\b(?:my|our)\s+(?:nominee|beneficiary|agent|employer)\b",
    r"\b(?:my|our)\s+(?:nominee|beneficiary|agent|employer)(?:'s)?\s+(?:name|phone|number|contact)\b",
    r"\bwho\s+(?:is|are)\b.{0,20}\b(?:nominee|beneficiary|agent|employer)\b.{0,30}\b(?:my|our)\b",
    r"\b(?:my|our)\s+(?:personal\s+)?(?:bank\s+)?account\b.{0,30}\b(?:number|balance|status)\b",
    r"\b(?:number|id|status)\b.{0,40}\b(?:my|our)\s+(?:personal\s+)?(?:policy|account|application)\b",
    r"(?:मेरा|मेरी|मेरे|हमारा|हमारी).{0,40}(?:पॉलिसी|खात[ाे]|क्लेम|आवेदन).{0,30}(?:नंबर|संख्या|बैलेंस|स्थिति|मंजूर|स्वीकृत)",
]
PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in PERSONAL_RECORD_PATTERNS]
HINDI_PAST_CHOICE = re.compile(
    r"(?:मैंने|हमने).{0,50}(?:चुना|चुनी|भुगतान किया|जमा किया).{0,10}(?:था|थी|है)"
)


def requires_personal_records(question: str) -> bool:
    question = question.replace("’", "'")
    # Instructions for finding/changing a record can be documented in an FAQ;
    # they do not require the assistant to read that record itself.
    if re.search(
        r"\bhow\s+(?:(?:can|do|should|would)\s+(?:i|we)|to)\s+"
        r"(?:check|find|view|update|change|track|locate|look up)\b",
        question,
        re.IGNORECASE,
    ):
        return False
    if any(pattern.search(question) for pattern in PATTERNS):
        return True
    return bool(
        HINDI_PAST_CHOICE.search(question)
        and not re.search(r"(?:अगर|यदि).{0,15}(?:मैंने|हमने)", question)
    )
