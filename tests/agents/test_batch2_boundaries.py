"""Batch 2 boundaries: agents 06 to 10.

Each test is a red line from the spec, expressed against what the code actually does rather
than against a description of it.
"""

from __future__ import annotations

import pytest

from app.agents import load

breaker = load("06_circuit_breaker")
treasury = load("07_treasury_cache")
budget = load("09_token_budget_arbiter")
egress = load("10_model_arbiter")
healer = load("26_circuit_breaker_healer")


@pytest.fixture(autouse=True)
def _fresh():
    breaker.reset()
    treasury.reset()
    yield
    breaker.reset()
    treasury.reset()


# ---------------------------------------------------------------- 06: no user text


def test_the_breaker_opens_on_repeated_failures() -> None:
    for _ in range(3):
        breaker.note_failure("gemini")

    assert breaker.state_of("gemini") == "OPEN"


def test_the_breaker_emits_no_user_facing_text() -> None:
    """Its contract is state, not sentences: note_failure returns nothing at all."""
    for _ in range(3):
        assert breaker.note_failure("gemini") is None


def test_the_healer_recovery_signal_closes_the_circuit() -> None:
    """Agent 26's three-in-a-row verdict is a deliberate close, not another data point."""
    for _ in range(3):
        breaker.note_failure("gemini")
    assert breaker.state_of("gemini") == "OPEN"

    assert breaker.accept_healer_recovery("gemini") == "CLOSED"


def test_the_healer_sees_the_closed_circuit_it_asked_for() -> None:
    """The two agents agree about the same event, because only one of them counts it."""
    healer_instance = healer.CircuitBreakerHealer()
    healer_instance.notify_circuit_opened(now_epoch=1000.0)

    for index in range(3):
        healer_instance.record_probe_result(True, now_epoch=1006.0 + index * 2)

    assert healer_instance.get_telemetry().is_circuit_reset_ready is True

    for _ in range(3):
        breaker.note_failure("gemini")
    breaker.accept_healer_recovery("gemini")

    assert healer_instance.sync_from_circuit(breaker) == healer.IDLE


# ---------------------------------------------------------------- 07: never cache PII


@pytest.mark.parametrize(
    "text",
    [
        "my aadhaar is [Aadhaar Redacted]",
        "call me on [Phone Redacted]",
        "my pan is [PAN Redacted]",
        "the key is [API Key Redacted]",
    ],
)
def test_a_question_that_carried_pii_is_never_cached(text: str) -> None:
    """The placeholder is proof that the question carried somebody's data."""
    assert treasury.should_store(text) is False


def test_an_ordinary_question_is_still_cacheable() -> None:
    assert treasury.should_store("when is deepavali") is True


def test_an_expired_entry_is_a_miss_not_a_stale_hit() -> None:
    treasury.reset()
    treasury.put(
        "when is deepavali", "en", "casual", "8 November", tokens=3, ttl_seconds=0
    )

    import time

    time.sleep(0.05)

    assert treasury.get("when is deepavali", "en", "casual").cache_hit is False


# ---------------------------------------------------------------- 09: the typed refusal


def test_the_budget_refusal_carries_its_own_code() -> None:
    assert budget.BUDGET_EXHAUSTED == "BUDGET_EXHAUSTED"
    assert budget.PROMPT_TOO_LARGE == "PROMPT_TOO_LARGE"


def test_an_over_budget_user_is_refused_before_the_model(monkeypatch) -> None:
    # The suite switches the arbiter off; this test is about what it does when on.
    monkeypatch.setenv("INDOONE_TOKEN_ARBITER_ENABLED", "true")

    budget.reset_quota("user-x")

    for _ in range(3):
        budget.record_usage("user-x", 300_000)

    verdict = budget.evaluate("user-x", "hello", [], intent="GENERAL_CHAT", language="en")

    assert verdict.allowed is False
    assert verdict.rejection_reason in {"BUDGET_EXHAUSTED", "QUOTA_EXHAUSTED"}


def test_the_sentry_is_not_the_ledger() -> None:
    """09 governs permission; 25 governs the fleet's money. They are different objects."""
    ledger = load("25_token_cost_ledger")

    assert not hasattr(budget, "get_summary")
    assert hasattr(ledger.TokenCostSupervisor, "get_summary")


# ---------------------------------------------------------------- 10: no content synthesis


def test_the_egress_arbiter_does_not_add_text() -> None:
    """Formatting may remove markup; it may not invent a sentence."""
    source = "Deepavali is on 8 November."
    result = egress.synthesize(source, "app")

    assert result.formatted_text == source


def test_it_strips_leaked_scaffolding_without_touching_the_answer() -> None:
    result = egress.synthesize("You are an AI assistant.\nDeepavali is on 8 November.", "app")

    assert result.formatted_text == "Deepavali is on 8 November."
    assert result.leaks_redacted is True


def test_each_channel_gets_its_own_markup() -> None:
    source = "**bold** text"

    assert "**" in egress.synthesize(source, "app").formatted_text
    assert "**" not in egress.synthesize(source, "instagram").formatted_text
