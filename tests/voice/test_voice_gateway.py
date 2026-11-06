"""The voice layer: speech in, the same assistant, speech out.

Everything here runs against a fake provider. The point of these tests is not that a speech
service works - that is the provider's job - but that this layer never invents, never crashes,
and never quietly answers in the wrong language.
"""

from __future__ import annotations

import pytest

from app.voice import gateway


class FakeProvider:
    name = "fake"

    def __init__(self, text: str = "ನಮಸ್ಕಾರ", audio: bytes = b"RIFFfake") -> None:
        self.text = text
        self.audio = audio
        self.calls: list[tuple] = []

    async def transcribe(self, audio: bytes, *, language: str, mime_type: str) -> gateway.Transcription:
        self.calls.append(("transcribe", language, mime_type, len(audio)))
        return gateway.Transcription(text=self.text, language=language)

    async def synthesize(self, text: str, *, language: str) -> gateway.Synthesis:
        self.calls.append(("synthesize", language, text))
        return gateway.Synthesis(audio=self.audio, language=language)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("INDOONE_VOICE_ENABLED", "true")
    yield


# ---------------------------------------------------------------- 1. off by default


def test_voice_is_off_unless_an_operator_turns_it_on(monkeypatch) -> None:
    monkeypatch.delenv("INDOONE_VOICE_ENABLED", raising=False)

    assert gateway.voice_enabled() is False


@pytest.mark.asyncio
async def test_a_switched_off_deployment_refuses_clearly(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_VOICE_ENABLED", "false")

    with pytest.raises(gateway.SpeechUnavailable, match="switched off"):
        await gateway.transcribe(b"audio")


# ---------------------------------------------------------------- 2. the round trip


@pytest.mark.asyncio
async def test_speech_becomes_text_through_the_provider() -> None:
    provider = FakeProvider(text="ನಾಳೆ ರಜೆ ಇದೆಯಾ?")

    result = await gateway.transcribe(b"RIFFxxxx", language="kn-IN", provider=provider)

    assert result.text == "ನಾಳೆ ರಜೆ ಇದೆಯಾ?"
    assert result.language == "kn-IN"
    assert result.provider == "fake"


@pytest.mark.asyncio
async def test_text_becomes_audio() -> None:
    provider = FakeProvider(audio=b"RIFFanswer")

    result = await gateway.speak("ನಾಳೆ ರಜೆ ಇದೆ.", language="kn-IN", provider=provider)

    assert result.audio == b"RIFFanswer"
    assert result.mime_type == "audio/wav"


@pytest.mark.asyncio
async def test_the_reply_is_said_in_the_language_it_was_asked_in() -> None:
    provider = FakeProvider()

    await gateway.speak("hello", language="hi-IN", provider=provider)

    assert provider.calls == [("synthesize", "hi-IN", "hello")]


# ---------------------------------------------------------------- 3. the guards


@pytest.mark.asyncio
async def test_an_empty_recording_is_refused() -> None:
    with pytest.raises(gateway.SpeechUnavailable, match="No audio"):
        await gateway.transcribe(b"", provider=FakeProvider())


@pytest.mark.asyncio
async def test_an_oversized_recording_is_refused_before_it_is_sent() -> None:
    huge = b"x" * (gateway.MAX_AUDIO_BYTES + 1)

    with pytest.raises(gateway.SpeechUnavailable, match="too long"):
        await gateway.transcribe(huge, provider=FakeProvider())


@pytest.mark.asyncio
async def test_nothing_to_say_is_refused() -> None:
    with pytest.raises(gateway.SpeechUnavailable, match="nothing to say"):
        await gateway.speak("   ", provider=FakeProvider())


# ---------------------------------------------------------------- 4. the languages


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("kn", "kn-IN"),
        ("kn_in", "kn-IN"),
        ("KN-IN", "kn-IN"),
        ("hi", "hi-IN"),
        ("ta-IN", "ta-IN"),
        ("", "kn-IN"),
        ("fr-FR", "kn-IN"),
    ],
)
def test_a_language_is_normalised_not_guessed(given: str, expected: str) -> None:
    """An unsupported language falls back to Kannada, the default - it is never invented."""
    assert gateway._normalise_language(given) == expected


# ---------------------------------------------------------------- 5. configuration


def test_an_unconfigured_provider_says_what_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("INDOONE_SPEECH_BASE_URL", raising=False)
    monkeypatch.delenv("INDOONE_SPEECH_API_KEY", raising=False)

    with pytest.raises(gateway.SpeechUnavailable, match="INDOONE_SPEECH_BASE_URL"):
        gateway.build_provider()


def test_a_configured_provider_is_built_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_SPEECH_BASE_URL", "https://speech.example.com")
    monkeypatch.setenv("INDOONE_SPEECH_API_KEY", "k" * 10)

    provider = gateway.build_provider()

    assert provider.base_url == "https://speech.example.com"
    # The key is never exposed as an attribute a log could print.
    assert not hasattr(provider, "api_key")


# ---------------------------------------------------------------- 6. the endpoints


def _client(monkeypatch, enabled: str = "true"):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_VOICE_ENABLED", enabled)
    return TestClient(app)


def test_a_switched_off_deployment_answers_503(monkeypatch) -> None:
    client = _client(monkeypatch, enabled="false")

    response = client.post("/voice/speak", json={"text": "hello"})

    assert response.status_code == 503


def test_speaking_returns_audio(monkeypatch) -> None:
    import base64
    import hashlib
    import hmac
    import time as _time

    from app.voice import gateway as gateway_module

    provider = FakeProvider(audio=b"RIFFanswer")
    monkeypatch.setattr(gateway_module, "build_provider", lambda: provider)

    client = _client(monkeypatch)

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    user = encode(b"user-one")
    stamp = encode(str(int(_time.time())).encode("ascii"))
    signature = encode(hmac.new(b"x" * 32, f"{user}.{stamp}".encode(), hashlib.sha256).digest())

    response = client.post(
        "/voice/speak",
        json={"text": "ನಾಳೆ ರಜೆ ಇದೆ.", "language": "kn"},
        headers={"Authorization": f"Bearer {user}.{stamp}.{signature}"},
    )

    assert response.status_code == 200
    assert response.content == b"RIFFanswer"
    assert response.headers["content-type"].startswith("audio/")
