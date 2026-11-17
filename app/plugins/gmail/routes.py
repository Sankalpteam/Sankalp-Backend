"""The six Gmail endpoints the app already calls.

Paths and payloads are copied from GmailPluginApi.kt, not invented: the app has been posting
{user_id, server_auth_code, scope} and {user_id, access_token, scope} to these addresses for as
long as it has had a Gmail card.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.security.errors import provider_failure
from app.plugins.gmail.oauth import (
    disconnect_gmail,
    exchange_server_auth_code,
    gmail_auth_config,
    gmail_connection_status,
    probe_mailbox,
    store_native_access_token,
)

router = APIRouter(tags=["gmail"])


class GmailUserRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=256)


class GmailServerAuthCodeRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=256)
    server_auth_code: str = Field(min_length=1, max_length=8_000)
    scope: str = Field(default="", max_length=1_000)


class GmailNativeTokenRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=256)
    access_token: str = Field(min_length=1, max_length=8_000)
    scope: str = Field(default="", max_length=1_000)


@router.get("/integrations/gmail/auth-config")
async def gmail_auth_configuration() -> dict[str, object]:
    try:
        return gmail_auth_config()
    except RuntimeError as exc:
        raise provider_failure("gmail authorization", exc, status_code=503) from exc


@router.get("/integrations/gmail/status")
async def gmail_status(user_id: str = Query(..., min_length=1, max_length=256)) -> dict[str, object]:
    try:
        return gmail_connection_status(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/integrations/gmail/server-auth-code")
async def gmail_server_auth_code(request: GmailServerAuthCodeRequest) -> dict[str, object]:
    try:
        return await exchange_server_auth_code(request.user_id, request.server_auth_code, request.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise provider_failure("Google rejected the Gmail authorization", exc) from exc
    except RuntimeError as exc:
        raise provider_failure("gmail authorization", exc, status_code=503) from exc


@router.post("/integrations/gmail/native-token")
async def gmail_native_token(request: GmailNativeTokenRequest) -> dict[str, object]:
    try:
        return store_native_access_token(request.user_id, request.access_token, request.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise provider_failure("gmail authorization", exc, status_code=503) from exc


@router.post("/integrations/gmail/probe")
async def gmail_probe(request: GmailUserRequest) -> dict[str, object]:
    try:
        summary = await probe_mailbox(request.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise provider_failure("Gmail did not answer", exc) from exc
    except RuntimeError as exc:
        raise provider_failure("gmail probe", exc, status_code=503) from exc

    return {"integration": "gmail", "user_id": request.user_id.strip(), "summary": summary}


@router.post("/integrations/gmail/disconnect")
async def gmail_disconnect(request: GmailUserRequest) -> dict[str, object]:
    try:
        return await disconnect_gmail(request.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
