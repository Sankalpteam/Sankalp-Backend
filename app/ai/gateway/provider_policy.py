"""One provider-selection policy shared by chat, tools, and app channels.

Gemini is the only selected primary backend. Groq is a secondary service called
by the gateway's controlled fallback; selecting 'groq', 'local', or 'azure' as
INDOONE_MODEL_BACKEND must *not* silently enable a different execution path.
Provider credentials and model versions are configured separately.
"""
from __future__ import annotations

import os

PRIMARY_BACKEND = "gemini"
FALLBACK_PROVIDER = "groq"


def configured_backend() -> str:
    """Return the normalized operator choice without inventing a fallback."""
    return os.getenv("INDOONE_MODEL_BACKEND", PRIMARY_BACKEND).strip().casefold()


def is_primary_backend_enabled() -> bool:
    """The same fail-closed check for API, agent tool loop, and app channels."""
    return configured_backend() == PRIMARY_BACKEND
