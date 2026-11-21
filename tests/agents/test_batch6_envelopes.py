"""Batch 6: the healer and the auditor close the fleet's contract.

Two agents that watch other agents, and the loop between them: the healer earns the recovery,
the breaker owns the state, and signal_recovery is the only place they meet.
"""

from __future__ import annotations

import pytest

from app.agents import load

healer = load("26_circuit_breaker_healer")
kingdom = load("27_master_kingdom_health_auditor")
breaker = load("06_circuit_breaker")

MODULES = [healer, kingdom]


@pytest.fixture(autouse=True)
def _fresh():
    breaker.reset()
    yield
    breaker.reset()


def _healed_healer():
    instance = healer.CircuitBreakerHealer()
    instance.notify_circuit_opened(now_epoch=1000.0)

    for index in range(3):
        instance.record_probe_result(True, now_epoch=1006.0 + index * 2)

    return instance


# ---------------------------------------------------------------- 1. the envelope


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_both_agents_expose_a_typed_envelope(module) -> None:
    shape = module.envelope(None)

    assert set(shape) == {"tool", "status", "payload"}


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_no_payload_carries_a_prose_key(module) -> None:
    for forbidden in ("message", "reply", "text", "summary", "summary_message"):
        assert forbidden not in module.envelope(None)["payload"]


# ---------------------------------------------------------------- 2. the healer


def test_a_closed_circuit_leaves_the_healer_dormant() -> None:
    assert healer.envelope(healer.CircuitBreakerHealer().get_telemetry())["status"] == "dormant"


def test_probing_is_recovering() -> None:
    instance = healer.CircuitBreakerHealer()
    instance.notify_circuit_opened(now_epoch=1000.0)

    shape = healer.envelope(instance.get_telemetry(1000.0))

    assert shape["status"] == "recovering"
    assert shape["payload"]["backoff_seconds"] == 5.0
    assert shape["payload"]["reset_ready"] is False


def test_three_successes_are_healed() -> None:
    shape = healer.envelope(_healed_healer().get_telemetry())

    assert shape["status"] == "healed"
    assert shape["payload"]["consecutive_successes"] == 3
    assert shape["payload"]["reset_ready"] is True


def test_a_failure_backs_off_and_is_never_healed() -> None:
    instance = healer.CircuitBreakerHealer()
    instance.notify_circuit_opened(now_epoch=1000.0)
    telemetry = instance.record_probe_result(False, now_epoch=1006.0)

    shape = healer.envelope(telemetry)

    assert shape["status"] == "recovering"
    assert shape["payload"]["backoff_seconds"] > 5.0
    assert shape["payload"]["reset_ready"] is False


# ---------------------------------------------------------------- 3. the loop between 26 and 06


def test_the_healer_closes_the_breakers_circuit() -> None:
    """The whole point of the pair: the healer earns it, the breaker does it."""
    for _ in range(3):
        breaker.note_failure("gemini")
    assert breaker.state_of("gemini") == "OPEN"

    assert _healed_healer().signal_recovery(breaker) is True
    assert breaker.state_of("gemini") == "CLOSED"


def test_a_healer_that_has_not_earned_it_says_nothing() -> None:
    for _ in range(3):
        breaker.note_failure("gemini")

    instance = healer.CircuitBreakerHealer()
    instance.notify_circuit_opened(now_epoch=1000.0)
    instance.record_probe_result(True, now_epoch=1006.0)

    assert instance.signal_recovery(breaker) is False
    assert breaker.state_of("gemini") == "OPEN"


def test_signalling_returns_the_healer_to_dormant() -> None:
    instance = _healed_healer()
    instance.signal_recovery(breaker)

    assert healer.envelope(instance.get_telemetry())["status"] == "dormant"


def test_a_breaker_without_the_hook_is_not_a_crash() -> None:
    class Old:
        pass

    assert _healed_healer().signal_recovery(Old()) is False


# ---------------------------------------------------------------- 4. the auditor


def test_all_pillars_healthy_is_a_healthy_pulse() -> None:
    shape = kingdom.envelope(kingdom.audit_kingdom())

    assert shape["status"] == "healthy"
    assert shape["payload"]["active_subsystems"] == 5
    assert set(shape["payload"]["pillars_summary"]) == {
        "resilience",
        "host",
        "persistence",
        "omnichannel",
        "financials",
    }


def test_an_open_circuit_is_unhealthy() -> None:
    shape = kingdom.envelope(kingdom.audit_kingdom(circuit_status="OPEN"))

    assert shape["status"] == "unhealthy"
    assert shape["payload"]["pillars_summary"]["resilience"] == "CRITICAL"


def test_pressure_is_degraded_not_unhealthy() -> None:
    shape = kingdom.envelope(kingdom.audit_kingdom(host_ram_used_pct=75.0))

    assert shape["status"] == "degraded"


def test_a_broken_pillar_is_counted_as_inactive_not_raised() -> None:
    def broken():
        raise RuntimeError("db on fire")

    shape = kingdom.envelope(kingdom.audit_kingdom(custom_subsystem_checks=[broken]))

    assert shape["payload"]["pillars_summary"]["broken"] == "UNREACHABLE"
    assert shape["payload"]["active_subsystems"] == 5
    assert shape["status"] == "degraded"


def test_the_pulse_carries_the_kolkata_timestamp() -> None:
    shape = kingdom.envelope(kingdom.audit_kingdom(now_epoch=1_700_000_000.0))

    assert "+05:30" in shape["payload"]["timestamp"]


def test_the_summary_sentence_stays_out_of_the_envelope() -> None:
    """It is for a human reading a log, not for a monitor parsing a response."""
    snapshot = kingdom.audit_kingdom()

    assert snapshot.summary_message
    assert "summary_message" not in kingdom.envelope(snapshot)["payload"]
