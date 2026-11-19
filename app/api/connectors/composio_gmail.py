"""Authenticated consent entry point for optional Composio-hosted Gmail OAuth.

The route never accepts a user_id in the body or URL. The signed backend
principal alone selects the per-user Composio session.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from app.api.security.dependencies import current_user_id
from app.ai.execution.composio_bridge import gmail_connect_link

router = APIRouter(prefix="/composio", tags=["integrations"])


@router.post("/gmail/connect")
async def connect_gmail(request: Request) -> dict[str, object]:
    user_id = current_user_id(request)
    try:
        url = await gmail_connect_link(user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Composio Gmail authentication is unavailable"
        ) from None
    return {"provider": "composio", "toolkit": "gmail", "authorization_url": url}
