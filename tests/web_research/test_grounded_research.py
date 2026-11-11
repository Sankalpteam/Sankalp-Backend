"""The research path prefers Google Search grounding, and survives without it.

Grounding is a built-in tool: the API refuses it in the same request as function
declarations. So it is used only here, in a function-free call, and every failure - a Gemma
model, a tier without grounding, an exhausted daily allowance, an answer with no citations -
falls back to the search we run ourselves rather than costing the user an answer.
"""

from __future__ import annotations

import pytest

from app.ai.gateway.gemini_service import _grounding_sources


def test_sources_are_read_from_grounding_metadata() -> None:
    response = {
        "candidates": [{
            "groundingMetadata": {
                "groundingChunks": [
                    {"web": {"uri": "https://example.com/a", "title": "A"}},
                    {"web": {"uri": "https://example.com/b", "title": "B"}},
                    {"web": {"uri": "https://example.com/a", "title": "A"}},
                ]
            }
        }]
    }

    assert _grounding_sources(response) == [
        ("A", "https://example.com/a"),
        ("B", "https://example.com/b"),
    ]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"candidates": []},
        {"candidates": [{}]},
        {"candidates": [{"groundingMetadata": {}}]},
        {"candidates": [{"groundingMetadata": {"groundingChunks": []}}]},
    ],
)
def test_no_metadata_means_no_sources(response: dict) -> None:
    assert _grounding_sources(response) == []
