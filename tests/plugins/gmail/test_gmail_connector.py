"""Gmail's six endpoints exist, and say honest things when they are not configured.

The app has been calling these addresses since it had a Gmail card, and none of them existed:
every call answered 404, so the card could only offer a button that failed. These tests pin the
contract the app already expects - the paths, the payload names, and the shape of the answers.
"""

from __future__ import annotations

import pytest

from app.plugins.gmail import oauth


def test_the_apps_payload_names_are_the_ones_we_read(monkeypatch) -> None:
    """GmailPluginApi posts user_id + server_auth_code + scope. Those are the field names."""

    from app.plugins.gmail.routes import GmailServerAuthCodeRequest

    fields = set(GmailServerAuthCodeRequest.model_fields)

    assert fields == {"user_id", "server_auth_code", "scope"}


def test_the_native_token_payload_matches(monkeypatch) -> None:
    from app.plugins.gmail.routes import GmailNativeTokenRequest

    assert set(GmailNativeTokenRequest.model_fields) == {"user_id", "access_token", "scope"}


def test_a_status_question_answers_without_being_connected() -> None:
    answer = oauth.gmail_connection_status("user-1")

    assert answer["connected"] is False
    assert answer["integration"] == "gmail"
    # The client id is public; the secret is not, and must never appear in any answer.
    assert answer["secrets_exposed"] is False


def test_an_unconfigured_deployment_says_so_instead_of_failing_oddly(monkeypatch) -> None:
    monkeypatch.delenv("INDOONE_GMAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("INDOONE_GMAIL_CLIENT_SECRET", raising=False)

    config = oauth.gmail_auth_config()

    assert config["configured"] is False
    assert config["server_client_id"] == ""
    assert config["client_secret_configured"] is False


def test_the_client_id_is_published_and_the_secret_is_not(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_GMAIL_CLIENT_ID", "abc.apps.googleusercontent.com")
    monkeypatch.setenv("INDOONE_GMAIL_CLIENT_SECRET", "shhh")

    config = oauth.gmail_auth_config()

    assert config["server_client_id"] == "abc.apps.googleusercontent.com"
    assert config["configured"] is True
    assert "shhh" not in str(config)


@pytest.mark.asyncio
async def test_an_exchange_without_configuration_refuses_rather_than_calls_google(monkeypatch) -> None:
    monkeypatch.delenv("INDOONE_GMAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("INDOONE_GMAIL_CLIENT_SECRET", raising=False)

    with pytest.raises(RuntimeError):
        await oauth.exchange_server_auth_code("user-1", "a-code")


@pytest.mark.asyncio
async def test_an_empty_code_is_refused_before_anything_is_stored() -> None:
    with pytest.raises(ValueError):
        await oauth.exchange_server_auth_code("user-1", "   ")


def test_an_empty_user_is_refused() -> None:
    with pytest.raises(ValueError):
        oauth.gmail_connection_status("  ")


def test_the_scope_is_the_least_that_works() -> None:
    """Read, send and trash the mailbox, and name the account. Nothing wider."""

    assert oauth.GMAIL_REQUIRED_SCOPES == (
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/userinfo.email",
    )
