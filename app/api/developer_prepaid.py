"""Internal-only prepaid reservations for a future Sankalp B2B model gateway.

No public credit/funding route, payment processing, model catalogue, or paid
inference is enabled. The gateway must use server-vetted prices, reserve before
provider dispatch, never dispatch an idempotency retry, and settle ONLY from
authoritative provider usage. An ambiguous timeout retains its hold.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.storage import database
from app.storage.migrations import ensure_schema

_MAX_AMOUNT_PAISE = 1_000_000_000
_MAX_RATE_PAISE = 1_000_000_000
_MAX_TOKENS = 10_000_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(value: int, name: str, *, minimum: int = 0, maximum: int = _MAX_AMOUNT_PAISE) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} outside allowed integer range")
    return value


@dataclass(frozen=True)
class ServerPrice:
    """Construct only from a separately approved server-side pricing catalogue."""
    alias: str
    provider: str
    upstream_model: str
    input_rate_paise: int
    output_rate_paise: int

    def validate(self) -> None:
        if not self.alias or len(self.alias) > 160 or not self.upstream_model or len(self.upstream_model) > 160:
            raise ValueError("invalid model alias or upstream model")
        if self.provider not in ("gemini", "groq"):
            raise ValueError("unsupported provider")
        _bounded(self.input_rate_paise, "input_rate", maximum=_MAX_RATE_PAISE)
        _bounded(self.output_rate_paise, "output_rate", maximum=_MAX_RATE_PAISE)
        if self.input_rate_paise + self.output_rate_paise == 0:
            raise ValueError("unpriced models cannot be billed")


def metered_charge_paise(tokens_in: int, tokens_out: int, *, input_rate_paise: int, output_rate_paise: int) -> int:
    """Never use client counts, rough token estimates, or floating-point money."""
    _bounded(tokens_in, "input_tokens", maximum=_MAX_TOKENS)
    _bounded(tokens_out, "output_tokens", maximum=_MAX_TOKENS)
    _bounded(input_rate_paise, "input_rate", maximum=_MAX_RATE_PAISE)
    _bounded(output_rate_paise, "output_rate", maximum=_MAX_RATE_PAISE)
    raw = tokens_in * input_rate_paise + tokens_out * output_rate_paise
    return (raw + 999_999) // 1_000_000


def wallet_snapshot(owner_id: str) -> dict[str, object]:
    """Caller must use its authenticated principal, never a browser-provided UID."""
    if not owner_id:
        raise ValueError("owner_id required")
    ensure_schema()
    with closing(database.connect()) as db:
        row = db.execute(
            "SELECT available_paise, held_paise FROM developer_prepaid_accounts WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        pending = db.execute(
            "SELECT COUNT(*) AS n FROM developer_prepaid_reservations "
            "WHERE owner_id = ? AND status = 'reserved'", (owner_id,),
        ).fetchone()
    return {
        "currency": "INR",
        "available_paise": int(row["available_paise"]) if row else 0,
        "held_paise": int(row["held_paise"]) if row else 0,
        "pending_requests": int(pending["n"]),
        "payments_enabled": False,
        "paid_inference_enabled": False,
        "notice": "Ledger preparation only. No checkout, paid request, or payment credit is enabled.",
    }


def reserve_prepaid(*, owner_id: str, key_id: str, client_request_id: str,
                    price: ServerPrice, max_charge_paise: int) -> tuple[str, bool]:
    """Hold a server-calculated maximum before provider dispatch.

    (reservation_id, True) for new work; (id, False) for an identical retry.
    The caller MUST NOT send another upstream request on False.
    """
    price.validate()
    _bounded(max_charge_paise, "max_charge_paise", minimum=1)
    if not owner_id or not key_id or not (1 <= len(client_request_id) <= 128):
        raise ValueError("invalid reservation identifiers")
    ensure_schema()
    with closing(database.connect()) as db:
        try:
            db.begin_write()
            key = db.execute(
                "SELECT owner_id, environment, status FROM developer_api_keys "
                "WHERE key_id = ?" + database.for_update(), (key_id,),
            ).fetchone()
            if (key is None or key["owner_id"] != owner_id or
                    key["environment"] != "live" or key["status"] != "active"):
                raise PermissionError("live developer key is unavailable")
            prior = db.execute(
                "SELECT reservation_id, owner_id, model, provider, upstream_model, "
                "input_rate_paise, output_rate_paise, max_charge_paise FROM developer_prepaid_reservations "
                "WHERE key_id = ? AND client_request_id = ?", (key_id, client_request_id),
            ).fetchone()
            if prior is not None:
                if (prior["owner_id"] != owner_id or prior["model"] != price.alias or
                    prior["provider"] != price.provider or prior["upstream_model"] != price.upstream_model or
                    int(prior["max_charge_paise"]) != max_charge_paise or
                    int(prior["input_rate_paise"]) != price.input_rate_paise or
                    int(prior["output_rate_paise"]) != price.output_rate_paise):
                    raise ValueError("idempotency key reused for different request parameters")
                db.commit()
                return str(prior["reservation_id"]), False
            account = db.execute(
                "SELECT available_paise FROM developer_prepaid_accounts WHERE owner_id = ?" + database.for_update(),
                (owner_id,),
            ).fetchone()
            if account is None or int(account["available_paise"]) < max_charge_paise:
                raise PermissionError("insufficient verified prepaid balance")
            updated = db.execute(
                "UPDATE developer_prepaid_accounts SET available_paise = available_paise - ?, "
                "held_paise = held_paise + ?, updated_at = ? WHERE owner_id = ? AND available_paise >= ?",
                (max_charge_paise, max_charge_paise, _now(), owner_id, max_charge_paise),
            )
            if updated.rowcount != 1:
                raise PermissionError("prepaid budget changed")
            reservation_id = str(uuid4())
            now = _now()
            db.execute(
                "INSERT INTO developer_prepaid_reservations "
                "(reservation_id, owner_id, key_id, client_request_id, model, provider, upstream_model, "
                "input_rate_paise, output_rate_paise, max_charge_paise, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)",
                (reservation_id, owner_id, key_id, client_request_id, price.alias, price.provider,
                 price.upstream_model, price.input_rate_paise, price.output_rate_paise,
                 max_charge_paise, now, now),
            )
            db.commit()
            return reservation_id, True
        except BaseException:
            db.rollback()
            raise


def capture_verified(*, reservation_id: str, provider: str, upstream_model: str,
                     input_tokens: int, output_tokens: int, provider_reported: bool = False) -> tuple[int, bool]:
    """Atomically capture verified usage and refund unused held paise.

    On timeout, missing metadata, or cap violation the hold stays reserved for
    operator reconciliation. Repeating exact evidence never captures twice.
    """
    if provider_reported is not True:
        raise PermissionError("unverified tokens cannot be billed")
    _bounded(input_tokens, "input_tokens", maximum=_MAX_TOKENS)
    _bounded(output_tokens, "output_tokens", maximum=_MAX_TOKENS)
    ensure_schema()
    with closing(database.connect()) as db:
        try:
            db.begin_write()
            row = db.execute(
                "SELECT * FROM developer_prepaid_reservations WHERE reservation_id = ?" + database.for_update(),
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown reservation")
            if row["provider"] != provider or row["upstream_model"] != upstream_model:
                raise ValueError("provider or upstream model mismatch")
            charge = metered_charge_paise(input_tokens, output_tokens,
                input_rate_paise=int(row["input_rate_paise"]),
                output_rate_paise=int(row["output_rate_paise"]))
            if row["status"] == "captured":
                evidence = db.execute(
                    "SELECT provider, model, input_tokens, output_tokens FROM developer_verified_usage "
                    "WHERE request_id = ?", (reservation_id,),
                ).fetchone()
                if (evidence is None or evidence["provider"] != provider or evidence["model"] != upstream_model or
                    int(evidence["input_tokens"]) != input_tokens or int(evidence["output_tokens"]) != output_tokens or
                    int(row["captured_paise"]) != charge):
                    raise ValueError("conflicting duplicate provider usage")
                db.commit()
                return charge, False
            if row["status"] != "reserved":
                raise PermissionError("reservation already released")
            maximum = int(row["max_charge_paise"])
            if charge > maximum:
                raise PermissionError("provider usage exceeds authorized cap; hold retained")
            now = _now()
            db.execute(
                "INSERT INTO developer_verified_usage "
                "(event_id, request_id, key_id, owner_id, model, provider, input_tokens, output_tokens, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid4()), reservation_id, row["key_id"], row["owner_id"], upstream_model,
                 provider, input_tokens, output_tokens, now),
            )
            updated = db.execute(
                "UPDATE developer_prepaid_accounts SET held_paise = held_paise - ?, "
                "available_paise = available_paise + ?, updated_at = ? "
                "WHERE owner_id = ? AND held_paise >= ?",
                (maximum, maximum - charge, now, row["owner_id"], maximum),
            )
            if updated.rowcount != 1:
                raise RuntimeError("prepaid hold invariant violated")
            updated = db.execute(
                "UPDATE developer_prepaid_reservations SET status = 'captured', "
                "captured_paise = ?, updated_at = ? WHERE reservation_id = ? AND status = 'reserved'",
                (charge, now, reservation_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("reservation state changed")
            db.execute("UPDATE developer_api_keys SET last_used_at = ? WHERE key_id = ?", (now, row["key_id"]))
            db.commit()
            return charge, True
        except BaseException:
            db.rollback()
            raise


def release_before_dispatch(*, reservation_id: str, provider_dispatch_started: bool) -> bool:
    """Safe cancellation ONLY before first provider attempt, not after timeout."""
    if provider_dispatch_started is not False:
        raise PermissionError("cannot release after provider dispatch")
    ensure_schema()
    with closing(database.connect()) as db:
        try:
            db.begin_write()
            row = db.execute(
                "SELECT owner_id, max_charge_paise, status FROM developer_prepaid_reservations "
                "WHERE reservation_id = ?" + database.for_update(), (reservation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown reservation")
            if row["status"] != "reserved":
                db.commit()
                return False
            maximum = int(row["max_charge_paise"])
            updated = db.execute(
                "UPDATE developer_prepaid_accounts SET held_paise = held_paise - ?, "
                "available_paise = available_paise + ?, updated_at = ? "
                "WHERE owner_id = ? AND held_paise >= ?",
                (maximum, maximum, _now(), row["owner_id"], maximum),
            )
            if updated.rowcount != 1:
                raise RuntimeError("prepaid hold invariant violated")
            db.execute(
                "UPDATE developer_prepaid_reservations SET status = 'released', updated_at = ? "
                "WHERE reservation_id = ?", (_now(), reservation_id),
            )
            db.commit()
            return True
        except BaseException:
            db.rollback()
            raise
