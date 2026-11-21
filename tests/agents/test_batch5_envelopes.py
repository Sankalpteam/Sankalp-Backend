"""Batch 5: agents 21 to 25 account for resources in numbers.

Streaming, quota, pool, memory and money: five agents that answer "how much", never "how do
you feel about it".
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents import load

streamer = load("21_app_streamer")
quota = load("22_omnichannel_quota_guard")
pool = load("23_postgres_pool_watchdog")
pruner = load("24_lru_state_pruner")
ledger = load("25_token_cost_ledger")

MODULES = [streamer, quota, pool, pruner, ledger]
KOLKATA = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def _fresh():
    quota.reset_state_for_tests()
    ledger.TokenCostSupervisor.reset_state_for_tests()
    yield


# ---------------------------------------------------------------- 1. the envelope


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_every_agent_exposes_a_typed_envelope(module) -> None:
    shape = module.envelope(None)

    assert set(shape) == {"tool", "status", "payload"}
    assert shape["status"] == "skipped"


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_no_payload_carries_a_prose_key(module) -> None:
    for forbidden in ("message", "reply", "text", "summary", "formatted_summary"):
        assert forbidden not in module.envelope(None)["payload"]


# ---------------------------------------------------------------- 2. the streamer


def test_a_completed_stream_reports_what_it_sent() -> None:
    metrics = streamer.StreamMetrics(total_chunks=3, total_characters=42, duration_ms=1.0)

    shape = streamer.envelope(metrics)

    assert shape["status"] == "completed"
    assert shape["payload"]["chunks_count"] == 3
    assert shape["payload"]["bytes_streamed"] == 42
    assert shape["payload"]["early_abort"] is False


def test_an_early_disconnect_is_a_status_not_a_sentence() -> None:
    metrics = streamer.StreamMetrics(total_chunks=1, total_characters=10, aborted_early=True, tokens_saved=2)

    shape = streamer.envelope(metrics)

    assert shape["status"] == "client_disconnected"
    assert shape["payload"]["early_abort"] is True
    assert shape["payload"]["tokens_saved"] == 2


def test_the_bridge_stops_when_the_client_leaves() -> None:
    consumed: list[int] = []

    async def feed():
        for index in range(50):
            consumed.append(index)
            yield f"t{index}"

    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    async def run():
        return [frame async for frame in streamer.AppStreamSupervisor.bridge_stream(feed(), is_cancelled_fn=cancelled)]

    frames = asyncio.run(run())

    assert len(consumed) < 50
    assert any("aborted_early" in frame and "true" in frame for frame in frames)


# ---------------------------------------------------------------- 3. the quota guard


def test_within_quota_the_envelope_carries_numbers() -> None:
    now = datetime(2026, 11, 2, 10, 0, tzinfo=KOLKATA)
    for index in range(10):
        quota.record_usage(f"wa:9190000{index:05d}", now)

    shape = quota.envelope(quota.check_quota("wa:919999999999", now), session_turns=3)

    assert shape["status"] == "active"
    assert shape["payload"]["quota_exhausted"] is False
    assert shape["payload"]["remaining_quota"] == 890
    assert shape["payload"]["session_turns"] == 3


def test_an_exhausted_quota_is_the_deterministic_code() -> None:
    now = datetime(2026, 11, 2, 10, 0, tzinfo=KOLKATA)
    for index in range(900):
        quota.record_usage(f"wa:919{index:08d}", now)

    shape = quota.envelope(quota.check_quota("wa:919999999999", now))

    assert shape["status"] == "QUOTA_EXHAUSTED"
    assert shape["payload"]["quota_exhausted"] is True
    assert shape["payload"]["remaining_quota"] == 0


def test_telegram_is_never_capped() -> None:
    now = datetime(2026, 11, 2, 10, 0, tzinfo=KOLKATA)

    shape = quota.envelope(quota.check_quota("tg:1", now))

    assert shape["status"] == "active"
    assert shape["payload"]["remaining_quota"] == -1


# ---------------------------------------------------------------- 4. the pool watchdog


def test_a_healthy_pool_is_numeric() -> None:
    shape = pool.envelope(pool.audit_pool_state(5, 15, 20))

    assert shape["status"] == "healthy"
    assert shape["payload"]["saturation_pct"] == 25.0
    assert shape["payload"]["active_conns"] == 5
    assert shape["payload"]["hung_queries"] == 0


def test_a_starved_pool_is_saturated() -> None:
    shape = pool.envelope(pool.audit_pool_state(20, 0, 20))

    assert shape["status"] == "saturated"
    assert shape["payload"]["starvation"] is True


def test_a_hung_query_is_counted_not_described() -> None:
    records = [{"pid": 7, "state": "idle in transaction", "duration_seconds": 40.0, "query": "UPDATE t"}]

    shape = pool.envelope(pool.audit_pool_state(5, 15, 20, records))

    assert shape["payload"]["hung_queries"] == 1


def test_the_watchdog_stays_under_its_budget() -> None:
    fastest = float("inf")

    for _ in range(200):
        started = time.thread_time()
        pool.envelope(pool.audit_pool_state(5, 15, 20))
        fastest = min(fastest, (time.thread_time() - started) * 1000)

    assert fastest < 0.05, f"fastest {fastest:.4f} ms"


# ---------------------------------------------------------------- 5. the pruner


def test_an_idle_pruner_reports_idle() -> None:
    store = pruner.BoundedLRUStore(max_capacity=10, default_ttl_sec=10_000, tier=1)
    store.put("a", 1)

    shape = pruner.envelope(store.sweep(), memory_pressure_pct=20.0)

    assert shape["status"] == "idle"
    assert shape["payload"]["evicted_items"] == 0
    assert shape["payload"]["memory_pressure_pct"] == 20.0


def test_a_prune_counts_what_it_removed() -> None:
    sacrificial = pruner.BoundedLRUStore(max_capacity=1000, default_ttl_sec=10_000, tier=pruner.SACRIFICIAL)
    protected = pruner.BoundedLRUStore(max_capacity=1000, default_ttl_sec=10_000, tier=pruner.PROTECTED)

    for index in range(40):
        sacrificial.put(f"a{index}", index)
        protected.put(f"c{index}", index)

    report = pruner.orchestrate_fleet_prune([protected, sacrificial], 90)
    shape = pruner.envelope(report, memory_pressure_pct=90.0)

    assert shape["status"] == "pruned"
    assert shape["payload"]["evicted_items"] == 10
    assert shape["payload"]["pressure_level"] == "CRITICAL"
    # The cheap tier paid, the expensive one did not.
    assert len(sacrificial) == 30
    assert len(protected) == 40


def test_freed_kb_is_an_honest_zero() -> None:
    """Counting entries is what this pruner does; sys.getsizeof would be a made-up number."""
    store = pruner.BoundedLRUStore(max_capacity=2, default_ttl_sec=10_000, tier=1)
    for index in range(5):
        store.put(f"k{index}", index)

    assert pruner.envelope(store.sweep())["payload"]["freed_kb"] == 0.0


# ---------------------------------------------------------------- 6. the ledger


def test_the_ledger_reports_micro_dollars() -> None:
    ledger.TokenCostSupervisor.record_usage("app", "gemini-1.5-flash", 1_000_000, 0)

    shape = ledger.envelope(ledger.TokenCostSupervisor.get_summary())

    assert shape["status"] == "recorded"
    assert shape["payload"]["prompt_tokens"] == 1_000_000
    assert shape["payload"]["cost_microusds"] == 75_000


def test_the_ledger_is_not_the_per_user_sentry() -> None:
    """09 answers "may this person spend more"; 25 answers "what did the fleet spend"."""
    budget = load("09_token_budget_arbiter")

    assert not hasattr(ledger, "check_quota")
    assert hasattr(budget, "evaluate")


def test_the_ledger_gives_no_advice() -> None:
    ledger.TokenCostSupervisor.record_usage("app", "gemini-1.5-flash", 0, 1_000_000)

    shape = ledger.envelope(ledger.TokenCostSupervisor.get_summary())

    for forbidden in ("message", "warning", "advice", "recommendation"):
        assert forbidden not in shape["payload"]
