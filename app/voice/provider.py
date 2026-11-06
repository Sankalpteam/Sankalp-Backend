"""An HTTP speech provider, configured rather than guessed.

The request and response shapes below are this project's **own** contract with a speech
service, kept deliberately plain: multipart audio in, JSON with a ``text`` field out, and JSON
text in, audio bytes out. A provider that speaks a different shape gets an adapter beside this
file, not a rewrite of the gateway.

Nothing here is enabled by default, and nothing here invents an endpoint: the base URL comes
from the environment because it belongs to whoever is paying for the service.
"""

from __future__ import annotations

import json
from typing import Any

from app.voice.gateway import Synthesis, Transcription


class HttpSpeechProvider:
    """Talks to a speech service over HTTPS, with a short, bounded request."""

    name = "http"

    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {"api-subscription-key": self._api_key, "accept": "application/json"}

        if json_body:
            headers["content-type"] = "application/json"

        return headers

    async def transcribe(self, audio: bytes, *, language: str, mime_type: str) -> Transcription:
        import httpx

        files = {"file": ("audio", audio, mime_type or "audio/wav")}
        data = {"model": "saarika:v2", "language_code": language}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/speech-to-text",
                headers=self._headers(),
                files=files,
                data=data,
            )

        if response.status_code >= 400:
            raise RuntimeError(f"speech-to-text failed with HTTP {response.status_code}")

        body: dict[str, Any] = response.json()

        return Transcription(
            text=str(body.get("transcript") or body.get("text") or "").strip(),
            language=str(body.get("language_code") or language),
            provider=self.name,
        )

    async def synthesize(self, text: str, *, language: str) -> Synthesis:
        import httpx

        payload = {
            "text": text,
            "target_language_code": language,
            "speaker": "anushka",
            "model": "bulbul:v2",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/text-to-speech",
                headers=self._headers(json_body=True),
                content=json.dumps(payload),
            )

        if response.status_code >= 400:
            raise RuntimeError(f"text-to-speech failed with HTTP {response.status_code}")

        body: dict[str, Any] = response.json()
        audios = body.get("audios") or []

        if not audios:
            raise RuntimeError("text-to-speech returned no audio")

        import base64

        return Synthesis(
            audio=base64.b64decode(audios[0]),
            mime_type="audio/wav",
            language=language,
            provider=self.name,
        )
