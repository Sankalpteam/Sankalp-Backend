"""Sankalp runtime gateway: Gemini primary, Groq secondary.

Plugins use the authenticated backend executor. No local inference, model weights,
training pipeline, or Azure deployment is loaded by the production runtime.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _gemini_backend_enabled() -> bool:
    return os.getenv("INDOONE_MODEL_BACKEND", "gemini").strip().casefold() == "gemini"


def ai_provider_status() -> dict[str, object]:
    """Report Gemini readiness without exposing API keys."""
    from app.ai.gateway import gemini_service
    selected = os.getenv("INDOONE_MODEL_BACKEND", "gemini").strip().casefold()
    configured = bool(os.getenv("GEMINI_API_KEY", "").strip())
    model = os.getenv("GEMINI_MODEL", gemini_service._DEFAULT_MODEL).strip()
    return {
        "provider": "gemini" if selected == "gemini" else (selected or "invalid"),
        "model": model if selected == "gemini" else None,
        "configuration_ready": bool(selected == "gemini" and configured and model),
        "api_key_configured": configured,
    }


class AIService:
    """Gemini answers first; Groq takes over on documented busy errors."""

    async def generate(
        self,
        message: str,
        history: list[tuple[str, str]] | None = None,
        document_context: str = "",
        media_attachment: dict[str, object] | None = None,
        media_attachments: list[dict[str, object]] | None = None,
        extra_system_instruction: str = "",
    ) -> str:
        if not message.strip():
            raise ValueError("message cannot be empty")
        if not _gemini_backend_enabled():
            raise RuntimeError("Unsupported provider: set INDOONE_MODEL_BACKEND=gemini")

        from app.ai.gateway.gemini_service import generate_gemini_reply, run_with_provider_fallback
        from app.ai.gateway.groq_service import generate_groq_reply, groq_available

        async def invoke(provider):
            kwargs = dict(
                history=history,
                document_context=document_context,
                extra_system_instruction=extra_system_instruction,
            )
            if media_attachments:
                kwargs["media_attachments"] = media_attachments
            elif media_attachment is not None:
                kwargs["media_attachment"] = media_attachment
            return await provider(message, **kwargs)

        # The configured Groq text model cannot process images/PDFs. Never discard
        # attachments to fabricate an answer on a provider failure.
        if media_attachment is not None or media_attachments:
            return await invoke(generate_gemini_reply)

        if groq_available():
            return await run_with_provider_fallback(
                lambda: invoke(generate_gemini_reply),
                lambda: invoke(generate_groq_reply),
            )
        return await invoke(generate_gemini_reply)


async def _close_stream(stream) -> None:
    """Close a provider generator, ignoring whatever it throws on the way out.

    The original failure is the one worth reporting; an error raised while unwinding the
    stream would replace it with a less useful one.
    """
    try:
        await stream.aclose()
    except Exception:  # noqa: BLE001 - closing is best effort by design
        return


async def stream_generate(
    message: str,
    history: list[tuple[str, str]] | None = None,
    document_context: str = "",
    extra_system_instruction: str = "",
):
    """Yield the answer as it is produced, instead of returning it whole.

    The contract is a plain async generator of text chunks. Two things matter about it:

    * A provider that cannot stream still works. It yields the finished reply as ONE chunk,
      which is exactly what the old 48-character replay pretended to be - but this says so
      instead of chopping a complete string to look busy.
    * A streaming failure mid-answer is not silent. Anything the provider raises after the
      first chunk propagates, so the caller can tell the difference between "the answer is
      long" and "the connection died".

    Provider streaming is behind INDOONE_STREAM_PROVIDERS because it cannot be verified
    from a test suite: it is a live HTTP contract with Gemini and Azure. Until that is
    verified on a deployment, this returns the blocking reply as a single chunk - correct,
    just not incremental.
    """
    # On by default: the generators exist for both providers now. A deployment that needs
    # the old behaviour sets INDOONE_STREAM_PROVIDERS=false.
    streaming_on = os.getenv("INDOONE_STREAM_PROVIDERS", "true").strip().casefold() not in {
        "0", "false", "no", "off",
    }
    providers = _provider_streams() if streaming_on else []
    arguments = {
        "history": history,
        "document_context": document_context,
        "extra_system_instruction": extra_system_instruction,
    }

    for position, (label, provider_stream) in enumerate(providers):
        stream = provider_stream(message, **arguments)
        first = ""

        try:
            first = await asyncio.wait_for(
                stream.__anext__(), timeout=_STREAM_FIRST_CHUNK_SECONDS
            )
        except StopAsyncIteration:
            logger.warning("%s stream ended without a chunk; failing over", label)
            await _close_stream(stream)
            continue
        except Exception as exc:
            # Timeout, 429, 5xx, a socket error - all the same decision: the next provider
            # answers instead. Nothing has reached the client yet, so this costs only time.
            next_label = (
                providers[position + 1][0]
                if position + 1 < len(providers)
                else "the blocking path"
            )
            logger.warning(
                "%s stream failed before the first chunk; failing over to %s: %s",
                label,
                next_label,
                exc,
            )
            await _close_stream(stream)
            continue

        # The first chunk arrived: this provider is answering.

        if first:
            yield first

        # The first chunk is out, so the answer is live: a failure from here is real and
        # propagates. Swallowing it would leave half an answer standing as if it were whole.
        try:
            async for chunk in stream:
                if chunk:
                    yield chunk
        finally:
            await _close_stream(stream)

        return

    reply = await generate_reply(
        message,
        history=history,
        document_context=document_context,
        extra_system_instruction=extra_system_instruction,
    )

    if reply:
        yield reply


# How long the primary gets to produce its FIRST chunk before the secondary takes over.
# A provider that accepts the connection and then stalls is the case this exists for: the
# client would otherwise wait until its own timeout and show "taking too long".
_STREAM_FIRST_CHUNK_SECONDS = 8.0


def _gemini_stream_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def _provider_streams() -> list[tuple[str, object]]:
    """Gemini streams; Groq supplies verified blocking fallback if needed."""
    if not _gemini_backend_enabled() or not _gemini_stream_configured():
        return []
    from app.ai.gateway.gemini_service import stream_gemini_reply
    return [("Gemini", stream_gemini_reply)]


def _provider_stream():
    """The streaming generator for the configured backend, or None.

    Chosen the same way the blocking call is chosen, so the two can never disagree about
    which provider is active.
    """
    # A provider that has no streaming generator yet must not break the request: the
    # caller falls back to one chunk of the finished reply, which is correct, just not
    # incremental.
    try:
        if _gemini_backend_enabled():
            from app.ai.gateway.gemini_service import stream_gemini_reply

            return stream_gemini_reply

        # No Azure: see the note in _stream_providers.
    except ImportError:
        return None

    return None


async def generate_reply(
    message: str,
    history: list[tuple[str, str]] | None = None,
    document_context: str = "",
    media_attachment: dict[str, object] | None = None,
    media_attachments: list[dict[str, object]] | None = None,
    extra_system_instruction: str = "",
) -> str:
    if media_attachments:
        return await AIService().generate(
            message,
            history=history,
            document_context=document_context,
            media_attachments=media_attachments,
            extra_system_instruction=extra_system_instruction,
        )
    if media_attachment is None:
        return await AIService().generate(
            message,
            history=history,
            document_context=document_context,
            extra_system_instruction=extra_system_instruction,
        )
    return await AIService().generate(
        message,
        history=history,
        document_context=document_context,
        media_attachment=media_attachment,
        extra_system_instruction=extra_system_instruction,
    )
