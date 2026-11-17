"""Gmail authorization for the email connector.

The app has been calling these endpoints since it had a Gmail card - auth-config, status,
probe, disconnect, and the two token hand-ins - and none of them existed, so every call answered
404 and the card could only offer a button that failed. This is that missing side.

Two ways in, because the two clients get their token differently:

  server auth code   the Android app uses Google Sign-In on the device and hands over a
                     one-time code, which is exchanged here for an access token and a refresh
                     token. The refresh token is what keeps the connection alive.
  native access token   a client that already holds a short-lived access token posts it. There
                     is no refresh token in that case, so the connection lasts until it expires.

Tokens are encrypted at rest by the same store the other connectors use, and never leave the
backend.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from app.capabilities.connectors.integrations import _fernet, get_valid_access_token
from app.capabilities.connectors.store import (
    delete_integration_token,
    get_integration_token_metadata,
    upsert_integration_token,
)

logger = logging.getLogger("sankalp.gmail")

_DEFAULT_AUTH_BASE_URL = "https://oauth2.googleapis.com"
_DEFAULT_GMAIL_API_BASE_URL = "https://gmail.googleapis.com"

# The least this connector needs: read the mailbox, send and label, and know which address it
# belongs to. gmail.modify covers read, send and trash; userinfo.email names the account.
GMAIL_REQUIRED_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
)
_DEFAULT_SCOPE = " ".join(GMAIL_REQUIRED_SCOPES)

# Google's server-auth-code flow has no redirect: the app already has the code.
_TOKEN_TIMEOUT_SECONDS = 20.0


def _https_url(value: str, setting_name: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            f"{setting_name} must be a valid HTTPS URL without credentials, query, or fragment."
        )

    return normalized


def _auth_base() -> str:
    return _https_url(os.getenv("INDOONE_GMAIL_AUTH_BASE_URL", _DEFAULT_AUTH_BASE_URL), "INDOONE_GMAIL_AUTH_BASE_URL")


def _api_base() -> str:
    return _https_url(os.getenv("INDOONE_GMAIL_API_BASE_URL", _DEFAULT_GMAIL_API_BASE_URL), "INDOONE_GMAIL_API_BASE_URL")


def _client_id() -> str:
    return os.getenv("INDOONE_GMAIL_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.getenv("INDOONE_GMAIL_CLIENT_SECRET", "").strip()


def gmail_auth_config() -> dict[str, object]:
    """What the app needs to start Google Sign-In.

    The client id is public by design - it travels in every sign-in request - and it is the one
    thing the app cannot guess. The secret never appears here.
    """

    client_id = _client_id()

    return {
        "integration": "gmail",
        "configured": bool(client_id and _client_secret()),
        "server_client_id": client_id,
        "client_secret_configured": bool(_client_secret()),
        "scope": _DEFAULT_SCOPE,
        "secrets_exposed": False,
    }


def _require_configuration() -> tuple[str, str]:
    client_id = _client_id()
    client_secret = _client_secret()

    if not client_id or not client_secret:
        raise RuntimeError(
            "Gmail is not configured: INDOONE_GMAIL_CLIENT_ID and INDOONE_GMAIL_CLIENT_SECRET "
            "are required."
        )

    return client_id, client_secret


def _expiry_from(expires_in: object) -> str:
    try:
        seconds = int(expires_in)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        seconds = 3600

    if seconds <= 0:
        seconds = 3600

    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _store_tokens(
    user_id: str,
    access_token: str,
    refresh_token: str | None,
    token_type: str,
    scope: str,
    expires_at: str,
) -> None:
    cipher = _fernet()

    upsert_integration_token(
        user_id,
        "gmail",
        cipher.encrypt(access_token.encode("utf-8")),
        cipher.encrypt(refresh_token.encode("utf-8")) if refresh_token else None,
        token_type,
        scope,
        expires_at,
    )


async def exchange_server_auth_code(
    user_id: str,
    server_auth_code: str,
    scope: str = "",
) -> dict[str, object]:
    """Turn the app's one-time code into stored credentials."""

    normalized_user = user_id.strip()
    code = server_auth_code.strip()

    if not normalized_user:
        raise ValueError("user_id is required")

    if not code:
        raise ValueError("server_auth_code is required")

    client_id, client_secret = _require_configuration()

    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{_auth_base()}/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
            },
        )

    if response.status_code != 200:
        logger.warning("Gmail token exchange refused: %s", response.status_code)
        raise ValueError("Google did not accept the authorization code.")

    payload = response.json() if response.content else {}

    if not isinstance(payload, dict):
        raise ValueError("Google returned an unexpected token answer.")

    access_token = str(payload.get("access_token") or "").strip()

    if not access_token:
        raise ValueError("Google returned no access token.")

    granted_scope = str(payload.get("scope") or scope or _DEFAULT_SCOPE)
    _store_tokens(
        normalized_user,
        access_token,
        str(payload.get("refresh_token") or "").strip() or None,
        str(payload.get("token_type") or "Bearer"),
        granted_scope,
        _expiry_from(payload.get("expires_in")),
    )

    return {
        "integration": "gmail",
        "user_id": normalized_user,
        "connected": True,
        "scope": granted_scope,
        "summary": await probe_mailbox(normalized_user),
        "secrets_exposed": False,
    }


def store_native_access_token(user_id: str, access_token: str, scope: str = "") -> dict[str, object]:
    """Store a short-lived token a client obtained itself.

    There is no refresh token in this flow, so the row carries an expiry and the connection
    ends with it. Saying so is better than pretending it is permanent.
    """

    normalized_user = user_id.strip()
    token = access_token.strip()

    if not normalized_user:
        raise ValueError("user_id is required")

    if not token:
        raise ValueError("access_token is required")

    _store_tokens(
        normalized_user,
        token,
        None,
        "Bearer",
        scope.strip() or _DEFAULT_SCOPE,
        _expiry_from(3600),
    )

    return {
        "integration": "gmail",
        "user_id": normalized_user,
        "connected": True,
        "scope": scope.strip() or _DEFAULT_SCOPE,
        "secrets_exposed": False,
    }


def gmail_connection_status(user_id: str) -> dict[str, object]:
    normalized_user = user_id.strip()

    if not normalized_user:
        raise ValueError("user_id is required")

    metadata = get_integration_token_metadata(normalized_user, "gmail")

    return {
        "integration": "gmail",
        "user_id": normalized_user,
        "connected": metadata is not None,
        "scope": str((metadata or {}).get("scope") or ""),
        "updated_at": (metadata or {}).get("updated_at"),
        "secrets_exposed": False,
    }


async def probe_mailbox(user_id: str) -> dict[str, object]:
    """Ask Gmail which address this connection belongs to.

    The app shows that address on the card, and it is also the cheapest proof that the token
    still works - a revoked token answers 401 here before it fails anywhere else.
    """

    token = await get_valid_access_token(user_id.strip(), "gmail")

    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{_api_base()}/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == 401:
        raise ValueError("Gmail refused the stored token; reconnect the account.")

    if response.status_code != 200:
        raise ValueError(f"Gmail answered {response.status_code} when asked for the mailbox.")

    payload = response.json() if response.content else {}
    address = str((payload or {}).get("emailAddress") or "").strip()

    return {"email_address": address, "provider_ok": True}


async def disconnect_gmail(user_id: str) -> dict[str, object]:
    """Revoke what Google will revoke, then forget the row.

    A revoke that fails must not leave the connection looking alive, so the local row goes
    either way; the log records that the remote side may still hold a grant.
    """

    normalized_user = user_id.strip()

    if not normalized_user:
        raise ValueError("user_id is required")

    try:
        token = await get_valid_access_token(normalized_user, "gmail")

        async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_SECONDS) as client:
            await client.post(f"{_auth_base()}/revoke", data={"token": token})
    except Exception as exc:  # noqa: BLE001 - the local delete must happen regardless
        logger.warning("Gmail revoke did not complete: %s", exc.__class__.__name__)

    delete_integration_token(normalized_user, "gmail")

    return {"integration": "gmail", "user_id": normalized_user, "connected": False, "secrets_exposed": False}
