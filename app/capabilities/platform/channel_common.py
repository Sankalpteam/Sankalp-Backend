from __future__ import annotations

import hashlib
import logging
import re
import os

from app.agents import load as load_agent
from app.ai.execution.account_context import accounts_instruction, without_stale_refusals
from app.ai.execution.request_context import conversation_instruction
from app.ai.execution.tool_failure import failure_line, take_tool_failure
from app.plugins.festivals.calendar import festival_answer
from app.web_research.fast_path import answer_instead_of_promise, web_research_answer
from app.ai.execution.conversation_digest import conversation_history_answer
from app.ai.execution.tool_loop import run_tool_loop
from app.plugins.email.email_tools import email_read_fast_path
from app.ai.gateway.service import generate_reply
from app.ai.memory.conversation_store import ConversationStore

logger = logging.getLogger("indoone.channel")

# Sent when the model could not answer at all. The channel user gets an honest
# reply instead of silence, and nothing about the provider is revealed.
# A reply that is a research block rather than a sentence. This is the shape the model is fed
# as document context, and it reached WhatsApp, Telegram and Instagram as the answer when the
# model call failed: "<research> title: HI url: https://en.wikipedia.org/wiki/HI snippet: ..."
# with raw <sub> tags inside. Nothing downstream should ever have to trust that it did not.
_RAW_BLOCK = re.compile(
    r"<\s*(research|document|context)\s*>"
    r"|\btitle:\s*\S+\s*\n?\s*url:"
    r"|\bsnippet:\s*\S"
    r"|<\s*(sub|sup|b|i|span)\s*[>/]",
    re.IGNORECASE,
)

# Three or more bare links and no sentence is a source dump, not an answer.
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def looks_like_raw_research(reply: str) -> bool:
    """True when the text is a research payload rather than something a person wrote."""
    text = (reply or "").strip()

    if not text:
        return False

    if _RAW_BLOCK.search(text):
        return True

    links = _URL.findall(text)

    if len(links) >= 3:
        without_links = _URL.sub(" ", text)
        words = [w for w in without_links.split() if any(c.isalpha() for c in w)]

        # A real answer carries its own sentences between the links.
        if len(words) < 12:
            return True

    return False


_RAW_BLOCK_REPLY = (
    "I found sources but could not turn them into an answer. Ask me again, or ask something "
    "narrower."
)

_FALLBACK_REPLY = (
    "Sankalp is handling a lot of requests right now. Please try again in a moment."
)

def _frontline(text: str, subject: str):
    """The app's front line, for a channel message. None when it cannot run."""

    try:
        from app.agents import pipeline

        return pipeline.process(text, subject=subject)
    except Exception:  # a front line that fails must not cost the user an answer
        logger.warning("channel front line failed", exc_info=True)
        return None


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _owner_user_id(name: str) -> str:
    return os.getenv(name, "").strip()


def _conversation_id(channel: str, sender_id: str) -> str:
    digest = hashlib.sha256(f"{channel}:{sender_id}".encode("utf-8")).hexdigest()
    return f"channel:{channel}:{digest}"


def _tool_loop_available() -> bool:
    """Whether a model backend with function calling is configured."""

    return os.getenv("INDOONE_MODEL_BACKEND", "").strip().casefold() in {"gemini", "azure"}


def _instruction(owner_user_id: str) -> str:
    """The same two or three lines the app's chat endpoint adds, for a channel message.

    Built here rather than in the channel handlers so WhatsApp, Telegram and Instagram
    cannot drift from each other, and from the app.
    """

    return conversation_instruction(owner_user_id)


async def _resolve_pending_write(
    owner_user_id: str, conversation_id: str, text: str, history: list[tuple[str, str]]
) -> str | None:
    """Answer a confirmation (or a cancellation) for a write this conversation holds.

    Returns the reply to send, or None when there is nothing pending. The pending
    action is keyed by conversation, and the channel conversation id is the one the
    proposal was stored under, so this is the same lookup the app does - just reached
    from a surface that has no other way to confirm anything.
    """

    from app.capabilities.connectors.store import get_pending_github_action
    from app.plugins.github.chat_tools import handle_github_chat_with_tools

    try:
        pending = get_pending_github_action(owner_user_id, conversation_id)
    except Exception:
        # A store that cannot answer must not swallow the user's message.
        return None
    if pending is None:
        return None

    try:
        return await handle_github_chat_with_tools(
            owner_user_id,
            text,
            history=history,
            conversation_id=conversation_id,
        )
    except Exception:
        logger.exception("channel confirmation failed", extra={"conversation_id": conversation_id})
        return None


async def _generate_with_history(owner_user_id: str, channel: str, sender_id: str, text: str) -> str:
    """Answer a channel message through the same loop the app uses.

    These handlers used to call the model directly, which meant a message on WhatsApp
    was answered with no tools at all, no date, and no statement about the user's
    connected accounts - so it could not look anything up, and it answered a date
    question from training memory. The app and the channels were two different
    assistants that happened to share a model.

    Now: the same history cleaning, the same instruction lines, and the same unified
    tool loop. The loop returns None when the model chooses no tool, which is the
    ordinary conversational case, and that falls back to a plain reply.
    """

    conversation_id = _conversation_id(channel, sender_id)

    # The same five-agent front line the app runs, before anything else reads the message.
    # Without it a channel message reached the research and tool paths raw: no rate check, no
    # attack check, no masking of Aadhaar or card numbers, no language read, and no intent
    # read. The subject is the conversation, which is per sender, so one WhatsApp user's
    # flood cannot rate-limit another's.
    frontline = _frontline(text, conversation_id)

    if frontline is not None:
        if frontline.refused and frontline.refusal is not None:
            return frontline.refusal.detail

        text = frontline.prompt or text

    # The same calendar answer the app gets, before any gateway: a festival question on
    # WhatsApp costs nothing and cannot be invented either.
    calendar_reply = festival_answer(text)

    # A question about the thread itself is answered from the digest here too. Without
    # this the same question got a different answer on WhatsApp than in the app: the
    # channel path had the calendar and the tool loop, but not the digest.
    if calendar_reply is None:
        calendar_reply = await conversation_history_answer(owner_user_id, conversation_id, text)

    # A read-only mailbox question, deterministically, on every channel.
    if calendar_reply is None:
        calendar_reply = await email_read_fast_path(owner_user_id, conversation_id, text)

    # A research request is answered here too: the search runs first, so the
    # model is never left to promise a lookup it does not perform.
    if calendar_reply is None:
        calendar_reply = await web_research_answer(text, user_id=owner_user_id)

    store = ConversationStore()
    if calendar_reply is not None:
        try:
            store.append(
                conversation_id,
                [("user", text), ("assistant", calendar_reply)],
                user_id=owner_user_id,
            )
        except (ValueError, PermissionError):
            pass

        return calendar_reply

    try:
        history = store.recent(conversation_id, owner_user_id)
    except (ValueError, PermissionError):
        history = []

    # An old "I don't have access" must not be replayed now that an account is
    # connected - the model reads its own last answer as evidence.
    history = without_stale_refusals(history, owner_user_id) or []
    instruction = _instruction(owner_user_id)

    # A write held earlier in this conversation is resolved before anything else. A bare
    # "yes" names no repository, so nothing else could find it - and on a channel there
    # is no approval screen to fall back to, which makes this the only way a proposal
    # made on WhatsApp can ever be confirmed.
    pending_reply = await _resolve_pending_write(owner_user_id, conversation_id, text, history)
    if pending_reply is not None:
        store.append(
            conversation_id,
            [("user", text), ("assistant", pending_reply)],
            user_id=owner_user_id,
        )
        return pending_reply

    reply: str | None = None
    if _tool_loop_available():
        try:
            reply = await run_tool_loop(
                owner_user_id,
                conversation_id,
                text,
                history=history,
                extra_system_instruction=instruction,
            )
        except Exception:
            # The plain reply below is the fallback: one provider hiccup must not cost
            # the user an answer on a channel they cannot retry from.
            logger.exception("channel tool loop failed", extra={"channel": channel})
            reply = None

    # An empty string is not an answer either - a channel has no second screen to explain
    # an empty message on.
    if reply is None or not str(reply).strip():
        try:
            # A tool that failed recorded why; the model says it in the user's language.
            tool_failure = take_tool_failure()
            if tool_failure:
                instruction = f"{instruction} {failure_line(tool_failure)}".strip()

            reply = await generate_reply(text, history=history, extra_system_instruction=instruction)
        except Exception:
            # A channel message must never be left unanswered.
            logger.exception("channel reply failed", extra={"channel": channel})
            reply = _FALLBACK_REPLY

    # A promise is not an answer, on a channel least of all: there is no second screen to
    # send "kodu" from. Outside the try above, so a failure here still leaves the reply.
    replacement = await answer_instead_of_promise(reply, text, history=history)
    if replacement:
        reply = replacement

    # Last gate before it leaves for a person: a research payload is not a message. This runs
    # on every channel and does not depend on why the block was produced, which is the point -
    # the cause can change, and the guard still holds.
    if looks_like_raw_research(reply):
        logger.warning(
            "channel reply was a raw research block and was replaced",
            extra={"channel": channel, "length": len(reply)},
        )
        reply = _RAW_BLOCK_REPLY

    # The same reply, said in this channel's own conventions. Sending the app's Markdown to
    # Instagram is how a channel shows raw asterisks to a customer, and a table does not
    # render in a WhatsApp message at all.
    try:
        synthesized = load_agent("10_model_arbiter").synthesize(reply, channel)
        if synthesized.formatted_text:
            reply = synthesized.formatted_text
    except Exception:  # a formatting problem must never swallow the answer
        logger.warning("channel formatting failed", exc_info=True)

    store.append(
        conversation_id,
        [("user", text), ("assistant", reply)],
        user_id=owner_user_id,
    )
    return reply
