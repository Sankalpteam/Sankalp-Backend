"""Batch 4: agents 16 to 20 return structure, never prose.

The wire normalisers were already typed and already deduplicated; what was missing was the
envelope the batch asks for, and a duplicate being a status rather than an error.
"""

from __future__ import annotations

import json
import time

import pytest

from app.agents import load

debugger = load("16_stack_trace_debugger")
host = load("17_system_diagnostics_sentry")
whatsapp = load("18_whatsapp_ingress")
telegram = load("19_telegram_bot_marshaller")
instagram = load("20_instagram_dm_adapter")

MODULES = [debugger, host, whatsapp, telegram, instagram]

PYTHON_STACK = '''Traceback (most recent call last):
  File "/home/dev/app/main.py", line 12, in run
    return 1 / 0
ZeroDivisionError: division by zero'''


@pytest.fixture(autouse=True)
def _fresh():
    for module in (whatsapp, telegram, instagram):
        module.reset_dedup_cache()
    yield


# ---------------------------------------------------------------- 1. the envelope


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_every_agent_exposes_a_typed_envelope(module) -> None:
    shape = module.envelope(None)

    assert set(shape) == {"tool", "status", "payload"}
    assert shape["status"] == "skipped"


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_no_payload_carries_a_prose_key(module) -> None:
    for forbidden in ("message", "explanation", "reply", "formatted_summary", "sanitized_output"):
        assert forbidden not in module.envelope(None)["payload"]


# ---------------------------------------------------------------- 2. the debugger


def test_a_traceback_becomes_structure_not_advice() -> None:
    report = debugger.diagnose_traceback(PYTHON_STACK)
    shape = debugger.envelope(report)

    assert shape["status"] == "success"
    assert shape["payload"]["error_type"] == "ZeroDivisionError"
    assert shape["payload"]["traceback_frames"]
    assert "culprit_line" in shape["payload"]["ast_summary"]
    # The deterministic fix is for the model, not the envelope.
    assert "deterministic_fix" not in shape["payload"]


def test_text_that_is_not_a_traceback_is_skipped() -> None:
    assert debugger.envelope(debugger.diagnose_traceback("just a sentence"))["status"] == "skipped"


# ---------------------------------------------------------------- 3. host telemetry


def test_host_telemetry_is_numbers() -> None:
    shape = host.envelope(host.collect_host_telemetry())

    assert shape["status"] == "success"
    for key in ("ram_used_pct", "disk_used_pct", "cpu_pct"):
        assert isinstance(shape["payload"][key], float)
    assert isinstance(shape["payload"]["process_threads"], int)


def test_the_sampler_stays_under_its_budget() -> None:
    fastest = float("inf")

    for _ in range(100):
        started = time.thread_time()
        host.collect_host_telemetry()
        fastest = min(fastest, (time.thread_time() - started) * 1000)

    assert fastest < 5.0, f"fastest {fastest:.4f} ms"


# ---------------------------------------------------------------- 4. the wire normalisers


def _whatsapp(text: str = "naale raja na?", wamid: str = "wamid.1") -> str:
    return json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": wamid,
                                        "from": "919876543210",
                                        "timestamp": "1720000000",
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    )


def test_a_whatsapp_message_normalises_to_the_internal_schema() -> None:
    shape = whatsapp.envelope(whatsapp.parse_and_normalize(_whatsapp()))

    assert shape["status"] == "success"
    assert shape["payload"]["sender_id"] == "wa:919876543210"
    assert shape["payload"]["text"] == "naale raja na?"
    assert shape["payload"]["channel"] == "whatsapp"


def test_a_repeated_wamid_is_a_duplicate_status_not_an_error() -> None:
    payload = _whatsapp(wamid="wamid.2")
    whatsapp.parse_and_normalize(payload)

    shape = whatsapp.envelope(whatsapp.parse_and_normalize(payload))

    assert shape["status"] == "duplicate"


def test_an_invalid_meta_signature_is_an_error_envelope() -> None:
    assert whatsapp.verify_webhook_signature(b"{}", "sha256=" + "0" * 64, "secret") is False


def test_a_telegram_update_normalises_and_deduplicates() -> None:
    payload = json.dumps(
        {
            "update_id": 11,
            "message": {
                "message_id": 5,
                "from": {"id": 987654321, "first_name": "Asha"},
                "chat": {"id": 987654321},
                "date": 1720000000,
                "text": "namaskara",
            },
        }
    )

    first = telegram.envelope(telegram.parse_and_normalize(payload))
    second = telegram.envelope(telegram.parse_and_normalize(payload))

    assert first["status"] == "success"
    assert first["payload"]["sender_id"] == "tg:987654321"
    assert second["status"] == "duplicate"


def test_the_telegram_secret_is_compared_in_constant_time() -> None:
    import inspect

    source = inspect.getsource(telegram.verify_secret_token)

    assert "compare_digest" in source


def test_a_bad_telegram_secret_is_refused() -> None:
    assert telegram.verify_secret_token("wrong", "right") is False
    assert telegram.verify_secret_token("", "right") is False


def test_an_instagram_dm_normalises_and_deduplicates() -> None:
    payload = json.dumps(
        {
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "17841400123456789"},
                            "timestamp": 1720000000000,
                            "message": {"mid": "m.9", "text": "hello"},
                        }
                    ]
                }
            ]
        }
    )

    first = instagram.envelope(instagram.parse_and_normalize(payload))
    second = instagram.envelope(instagram.parse_and_normalize(payload))

    assert first["status"] == "success"
    assert first["payload"]["sender_id"] == "ig:17841400123456789"
    assert second["status"] == "duplicate"


def test_an_instagram_echo_is_not_a_turn() -> None:
    payload = json.dumps(
        {
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "1"},
                            "message": {"mid": "m.10", "is_echo": True, "text": "our reply"},
                        }
                    ]
                }
            ]
        }
    )

    shape = instagram.envelope(instagram.parse_and_normalize(payload))

    assert shape["payload"]["message_type"] == "echo"


@pytest.mark.parametrize("module", [whatsapp, telegram, instagram], ids=lambda m: m.TOOL_NAME)
def test_unreadable_wire_input_is_an_error_envelope(module) -> None:
    shape = module.envelope(module.parse_and_normalize("{not json"))

    assert shape["status"] == "error"
