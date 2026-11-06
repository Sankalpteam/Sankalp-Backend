"""Kannada voice, in and out.

Text chat is table stakes. A person who can *talk* to their assistant in their own language is
a different product, and for the languages this system already speaks - Kannada, Hindi, Tamil,
Telugu and the rest - it is the difference between a feature and a habit.

The design keeps one promise: **the voice layer never invents**. Speech in becomes text, that
text goes through exactly the same 27-agent pipeline a typed message goes through, and the
reply comes back as audio. Nothing about the answer changes because it was spoken.

Two facts about this file:

* It is a **seam, not a provider**. ``SpeechProvider`` is the contract; a concrete provider is
  supplied at call time or built from the environment. The base URL and the request shape
  belong to whichever service is chosen, and hard-coding a guess about them here would be the
  one thing this file is not allowed to do.
* It is **off by default**. ``INDOONE_VOICE_ENABLED=true`` turns it on, because a voice
  endpoint that is half-configured is worse than one that is absent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

SUPPORTED_LANGUAGES = frozenset(
    {
        "kn-IN",
        "hi-IN",
        "ta-IN",
        "te-IN",
        "ml-IN",
        "mr-IN",
        "bn-IN",
        "gu-IN",
        "pa-IN",
        "or-IN",
    }
)

DEFAULT_LANGUAGE = "kn-IN"
MAX_AUDIO_BYTES = 5 * 1024 * 1024


class SpeechUnavailable(RuntimeError):
    """Voice is off, unconfigured, or the provider could not be reached."""


@dataclass(slots=True)
class Transcription:
    text: str = ""
    language: str = DEFAULT_LANGUAGE
    duration_seconds: float = 0.0
    provider: str = ""


@dataclass(slots=True)
class Synthesis:
    audio: bytes = b""
    mime_type: str = "audio/wav"
    language: str = DEFAULT_LANGUAGE
    provider: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SpeechProvider(Protocol):
    """What a speech service has to do to be usable here."""

    name: str

    async def transcribe(self, audio: bytes, *, language: str, mime_type: str) -> Transcription:
        ...

    async def synthesize(self, text: str, *, language: str) -> Synthesis:
        ...


def voice_enabled() -> bool:
    return os.getenv("INDOONE_VOICE_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _normalise_language(language: str) -> str:
    wanted = (language or "").strip()

    if wanted in SUPPORTED_LANGUAGES:
        return wanted

    # "kn", "kn_in", "KN-in" all mean the same thing to a person and should to us.
    lowered = wanted.replace("_", "-").lower()

    if len(lowered) == 2:
        candidate = f"{lowered}-IN"
        if candidate in SUPPORTED_LANGUAGES:
            return candidate

    for supported in SUPPORTED_LANGUAGES:
        if supported.lower() == lowered:
            return supported

    return DEFAULT_LANGUAGE


async def transcribe(
    audio: bytes,
    *,
    language: str = DEFAULT_LANGUAGE,
    mime_type: str = "audio/wav",
    provider: SpeechProvider | None = None,
) -> Transcription:
    """Audio to text, in the language the speaker chose."""
    if not voice_enabled() and provider is None:
        raise SpeechUnavailable("Voice is switched off (INDOONE_VOICE_ENABLED).")

    if not audio:
        raise SpeechUnavailable("No audio was sent.")

    if len(audio) > MAX_AUDIO_BYTES:
        raise SpeechUnavailable("That recording is too long. Keep it under five minutes.")

    chosen = provider or build_provider()
    result = await chosen.transcribe(
        audio, language=_normalise_language(language), mime_type=mime_type
    )

    result.language = _normalise_language(result.language or language)
    result.provider = result.provider or getattr(chosen, "name", "")

    return result


async def speak(
    text: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    provider: SpeechProvider | None = None,
) -> Synthesis:
    """Text to audio, in the same language."""
    if not voice_enabled() and provider is None:
        raise SpeechUnavailable("Voice is switched off (INDOONE_VOICE_ENABLED).")

    body = (text or "").strip()

    if not body:
        raise SpeechUnavailable("There is nothing to say.")

    chosen = provider or build_provider()
    result = await chosen.synthesize(body, language=_normalise_language(language))

    result.language = _normalise_language(result.language or language)
    result.provider = result.provider or getattr(chosen, "name", "")

    return result


def build_provider() -> SpeechProvider:
    """The configured provider, or a clear refusal.

    Deliberately explicit: an operator who turns voice on without configuring a provider gets
    a sentence telling them what is missing, not a 500 from a half-built client.
    """
    from app.voice.provider import HttpSpeechProvider

    base_url = os.getenv("INDOONE_SPEECH_BASE_URL", "").strip()
    api_key = os.getenv("INDOONE_SPEECH_API_KEY", "").strip()

    if not base_url or not api_key:
        raise SpeechUnavailable(
            "No speech provider is configured. Set INDOONE_SPEECH_BASE_URL and "
            "INDOONE_SPEECH_API_KEY."
        )

    return HttpSpeechProvider(base_url=base_url, api_key=api_key)
