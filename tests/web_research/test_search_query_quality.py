"""The query that reaches the search engine is the question, not the complaint around it.

"ಅರ್ಥ ಆಗಲಿಲ್ಲ ಕೊಡು correct agi news en ede evattu latest" is a person saying they did not
understand and then asking for the news. The whole sentence was sent as the query, and a search
engine cannot answer that - so the research came back empty and the user was told there was no
news. The research worked; the query was noise.
"""

from __future__ import annotations

import pytest

from app.web_research.fast_path import search_query_from
from app.web_research.research import build_research_query_variants


@pytest.mark.parametrize(
    "message, expected",
    [
        ("ಅರ್ಥ ಆಗಲಿಲ್ಲ ಕೊಡು correct agi news en ede evattu latest", "news today latest"),
        ("ivattina latest news helu", "today latest news"),
        ("today gold price in bangalore huduku", "today gold price bangalore"),
        ("latest news about ISRO", "latest news ISRO"),
    ],
)
def test_the_complaint_does_not_reach_the_search_engine(message: str, expected: str) -> None:
    assert search_query_from(message) == expected


def test_today_survives_as_a_search_signal() -> None:
    """A query that loses "today" returns last month's headlines."""

    assert "today" in search_query_from("ಇವತ್ತಿನ ಬೆಂಗಳೂರು ಹವಾಮಾನ ಹೇಳು")


def test_the_english_variant_keeps_its_time_words() -> None:
    variants = build_research_query_variants("today latest news")

    assert variants == ["today latest news"]


def test_a_kannada_noun_is_not_cut_into_a_non_word() -> None:
    """"ಬೆಂಗಳೂರಿನಲ್ಲಿ" (in Bengaluru) used to become "ಬೆಂಗಳೂರಿ", which is not a word.

    A Kannada-only query then searched for a string nothing could match, and the answer came
    back empty for a question the index could have answered.
    """

    query = search_query_from("ಬೆಂಗಳೂರಿನಲ್ಲಿ ಫೋನ್ ಬೆಲೆ ಎಷ್ಟು ಹುಡುಕು")

    assert "ಬೆಂಗಳೂರಿನಲ್ಲಿ" in query
    assert "ಬೆಂಗಳೂರಿ " not in query
