from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai.quality.answer_quality import assess_answer
from app.ai.execution.account_context import accounts_instruction, without_stale_refusals
from app.ai.execution.request_context import conversation_instruction
from app.ai.execution.tool_failure import failure_line, take_tool_failure
from app.ai.execution.tool_loop import run_tool_loop
from app.ai.execution.turn_idempotency import claim_turn, mark_turn_replied
from app.ai.execution import action_chips
from app.ai.memory.conversation_store import ConversationStore
from app.ai.memory.file_context import read_text_file
from app.ai.memory.memory import extract_memory_candidates
from app.ai.gateway.service import stream_generate
from app.plugins.github.chat_tools import handle_github_chat_with_tools
from app.ai.execution.confirmation import classify_reply_async
from app.ai.execution.conversation_digest import conversation_history_answer
from app.ai.gateway.ai_rules import EMAIL_PENDING_ACTION
from app.capabilities.connectors.store import (
    get_pending_email_action,
    get_pending_github_action,
)
from app.plugins.email.email_tools import handle_email_chat
from app.plugins.festivals.calendar import festival_answer
from app.web_research.fast_path import web_research_answer
from app.web_research.grounding import split_grounded_sources
from app.api.security.errors import REQUEST_FAILED_MESSAGE, provider_failure

logger = logging.getLogger("indoone.chat.stream")

router = APIRouter(tags=["platform"])
_stream_store = ConversationStore()


class StreamChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: str | None = Field(default=None, max_length=128)
    file_id: str | None = Field(default=None, max_length=128)
    client_turn_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")


class MemoryCandidatesRequest(BaseModel):
    """The message to read durable memory candidates from."""

    message: str = Field(min_length=1, max_length=20_000)


# The Control Center gateway expects /api/platform/chat/stream; both paths map
# to this one handler.
@router.post("/platform/chat/stream")
@router.post("/chat/stream")
async def stream_chat(request: Request, body: StreamChatRequest) -> StreamingResponse:
    user_id = str(getattr(request.state, "principal_id", "")).strip()

    conversation_id = body.conversation_id or str(uuid4())
    if _stream_store.is_closed(conversation_id, user_id):
        conversation_id = str(uuid4())

    # A conversation that does not exist yet has no history - that is a new thread, not an
    # error. Reading it used to raise "conversation not found" and fail the whole request.
    try:
        history = without_stale_refusals(
            _stream_store.recent(conversation_id, user_id), user_id
        ) or []
    except (ValueError, PermissionError):
        history = []
    # The same two context lines /api/chat sends, and for the same reason. Without the date
    # the model answers "what is the date" from its training memory: the web, which streams,
    # was told "Today is Wednesday, May 22, 2024" while the app, which does not stream, was
    # right. The streaming endpoint must not be a lesser door into the same model.
    accounts = conversation_instruction(user_id)
    document_context = ""
    if body.file_id:
        try:
            document_context = read_text_file(body.file_id, user_id=user_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Same admission control as /api/chat. A streaming request that skips the quota is a
    # second, unmetered door into the same model.
    if user_id:
        from app.api.chat.chat import _admit

        _admit(request, user_id)

    # This admission runs *before* any GitHub/email tool. A client can retry
    # its transport, but a turn that may have written data cannot be replayed.
    if body.client_turn_id:
        if not user_id:
            raise HTTPException(status_code=401, detail="authenticated user required for turn idempotency")
        try:
            status = claim_turn(
                user_id, body.client_turn_id, message=body.message,
                conversation_id=body.conversation_id, file_id=body.file_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if status != "new":
            raise HTTPException(
                status_code=409,
                detail="This chat turn was already received. Its outcome may be uncertain; check the conversation or connected app before trying again.",
            )

    action_chips.begin()

    # The same pipeline as /api/chat, not a lesser copy of it.
    #
    # The GitHub router runs before the model, for the same reason it does there: a model
    # answering a repository question from memory invents branch names. Streaming must not
    # become the endpoint where that comes back. Its answer is deterministic, so it is sent
    # as one chunk - there is nothing to reveal gradually.
    tool_reply: str | None = None
    if user_id:
        try:
            # The same executors, in the same order, as /api/chat. Each one exists there
            # because a model cannot answer that question from four messages of history,
            # and every one of them was missing here: the streaming endpoint was a lesser
            # door into the same model. A question it could not answer became either an
            # invented answer or "could not complete".
            _pending_email = get_pending_email_action(user_id, conversation_id)
            email_owns_this = bool(
                _pending_email
                and str(_pending_email.get("integration_id") or "").casefold() != "github"
            )

            # A GitHub write waiting on this conversation is resolved first: a bare
            # "ok create maadu" names no repository, so nothing else would reach it.
            if get_pending_github_action(user_id, conversation_id) is not None:
                tool_reply = await handle_github_chat_with_tools(
                    user_id,
                    body.message,
                    history=history,
                    conversation_id=conversation_id,
                )

            # A question about the thread itself is answered from a digest of what is
            # stored. The model only sees four messages, so it could not answer this.
            if tool_reply is None and not email_owns_this:
                tool_reply = await conversation_history_answer(
                    user_id, conversation_id, body.message
                )

            # A pending email confirmation must be resolved before anything else.
            if tool_reply is None and _pending_email:
                action_type = str(_pending_email.get("action_type") or "")
                if (
                    action_type == "await_recipient"
                    or await classify_reply_async(body.message) in {"confirm", "cancel"}
                ):
                    tool_reply = await handle_email_chat(
                        user_id, conversation_id, body.message
                    )
                else:
                    tool_reply = EMAIL_PENDING_ACTION

            if tool_reply is None:
                tool_reply = await handle_github_chat_with_tools(
                    user_id,
                    body.message,
                    history=history,
                    conversation_id=conversation_id,
                )

            # A festival date is a calendar lookup against a state gazette, not something
            # to compute: a model asked for one produces a confident date that is wrong.
            if tool_reply is None and not email_owns_this:
                tool_reply = festival_answer(body.message)

            # A research request is answered in THIS turn, before the model speaks. Without
            # this the model answered "latest news" from its training memory, the answer
            # carried no Sources block, and the freshness gate below replaced the whole
            # reply with the failure message.
            if tool_reply is None and not email_owns_this:
                tool_reply = await web_research_answer(
                    body.message, history=history, user_id=user_id,
                )

            if tool_reply is None:
                tool_reply = await run_tool_loop(
                    user_id, conversation_id, body.message, history=history,
                    extra_system_instruction=accounts,
                )
        except Exception:
            logger.exception("Sankalp streaming tool routing failed")
            from app.ai.execution.tool_intent import requires_tool_result
            if requires_tool_result(body.message):
                raise HTTPException(
                    status_code=503,
                    detail="The requested tool operation could not be verified.",
                )
            tool_reply = None

    # The same rule as /api/chat: a failed tool hands its reason to the model.
    tool_failure = take_tool_failure()
    if tool_failure:
        accounts = f"{accounts} {failure_line(tool_failure)}".strip()

    def event(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def events():
        yield event({"type": "meta", "conversation_id": conversation_id})

        text = ""
        failed = False

        try:
            if tool_reply is not None and str(tool_reply).strip():
                text = tool_reply
                yield event({"type": "delta", "text": text})
            else:
                # Live chunks, not a finished string chopped into 48-character pieces.
                async for chunk in stream_generate(
                    body.message,
                    history=history,
                    document_context=document_context,
                    extra_system_instruction=accounts,
                ):
                    if not chunk:
                        continue
                    text += chunk
                    yield event({"type": "delta", "text": chunk})
        except RuntimeError as exc:
            # The answer had already started, so a 503 is no longer possible: the headers
            # are gone. The last event carries the failure instead, and the client replaces
            # what it streamed with it.
            logger.error("Streaming answer failed: %s", exc)
            failed = True

        if failed:
            yield event(
                {"type": "done", "reply": REQUEST_FAILED_MESSAGE, "action_chips": []}
            )
            return

        quality = assess_answer(body.message, text)
        reply = text

        if not quality.passed:
            logger.error("Model answer failed quality checks: %s", quality.reason)
            reply = REQUEST_FAILED_MESSAGE

        # The blocking endpoint has always returned the pages a research answer used; this one
        # returned the text alone, so a streamed answer on the web carried no citations at all.
        # The sources are separated from the text here for the same reason: they are data, and
        # the client draws them as rows.
        reply, web_sources = split_grounded_sources(reply)

        try:
            _stream_store.append(
                conversation_id,
                [("user", body.message), ("assistant", reply)],
                user_id=user_id,
            )
        except (ValueError, PermissionError) as exc:
            logger.warning("Streaming answer could not be stored: %s", exc)

        if body.client_turn_id:
            try:
                mark_turn_replied(user_id, body.client_turn_id)
            except Exception:
                logger.warning("Unable to mark streamed chat turn replied", exc_info=True)

        # The chips ride on the last event, so a client that only streams has the same
        # follow-ups the blocking endpoint returns: Confirm/Cancel for a held write, the
        # obvious next step after a list.
        yield event(
            {
                "type": "done",
                "reply": reply,
                "sources": [
                    {"title": source.title, "url": source.url} for source in web_sources
                ],
                "action_chips": action_chips.collected(),
            }
        )

    async def events_with_reset():
        try:
            async for event in events():
                yield event
        finally:
            action_chips.reset()

    return StreamingResponse(
        events_with_reset(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ai/memory/candidates")
async def memory_candidates(request: MemoryCandidatesRequest) -> dict[str, object]:
    candidates = extract_memory_candidates(request.message)
    return {
        "candidates": [
            {
                "key": item.key,
                "value": item.value,
                "confidence": item.confidence,
                "reason": item.reason,
            }
            for item in candidates
        ]
    }
