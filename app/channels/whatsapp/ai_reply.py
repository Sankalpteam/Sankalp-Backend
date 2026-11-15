from __future__ import annotations

import logging
from typing import Any

from app.capabilities.platform.channel_common import (
    _enabled,
    _generate_with_history,
    channel_owner,
    split_reply,
)
from app.capabilities.connectors.store import claim_channel_inbound_event, complete_channel_inbound_event
from app.channels.whatsapp.service import send_text_message as send_whatsapp_text_message

logger = logging.getLogger("indoone.whatsapp.ai_reply")


async def process_whatsapp_message(message: dict[str, Any]) -> bool:
    if not _enabled("INDOONE_WHATSAPP_AI_REPLY_ENABLED"):
        return False

    message_id = str(message.get("id") or "").strip()
    sender_id = str(message.get("from") or "").strip()

    # The bot answers anyone who messages the number, so the owner's connected accounts are
    # only reachable from a sender on the trusted list. Everyone else is answered under the
    # channel's own scope, where no connectors exist.
    owner_user_id, _trusted = channel_owner(
        "whatsapp", sender_id, "INDOONE_WHATSAPP_AI_OWNER_USER_ID"
    )
    message_type = str(message.get("type") or "").strip().lower()
    text = str(message.get("text") or "").strip()
    if not message_id or not sender_id or message_type != "text" or not text:
        return False

    event_key = f"whatsapp:{message_id}"
    if not claim_channel_inbound_event(event_key):
        return False

    try:
        reply = await _generate_with_history(owner_user_id, "whatsapp", sender_id, text)
        # A long answer is more than one message. Only the first piece used to be sent, so
        # everything past the channel's limit was lost without a trace.
        for piece in split_reply(reply):
            await send_whatsapp_text_message(sender_id, piece, approved=True)
    except Exception:
        complete_channel_inbound_event(event_key, success=False)
        logger.exception("WhatsApp AI reply failed", extra={"message_id": message_id})
        return False

    complete_channel_inbound_event(event_key, success=True)
    logger.info("WhatsApp AI reply sent", extra={"message_id": message_id, "sender_id": sender_id})
    return True
