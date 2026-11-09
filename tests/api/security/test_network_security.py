import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.security.network_security import (
    allowed_origins,
    configure_network_security,
    validate_network_security_config,
)


def test_development_uses_safe_default_hosts(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_ENV", "development")
    monkeypatch.delenv("INDOONE_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("INDOONE_TRUST_PROXY_HEADERS", "false")

    app = FastAPI()
    app.get("/")(lambda: {"status": "ok"})
    configure_network_security(app)
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400


def test_production_requires_explicit_host_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_ENV", "production")
    monkeypatch.delenv("INDOONE_ALLOWED_HOSTS", raising=False)

    with pytest.raises(RuntimeError, match="INDOONE_ALLOWED_HOSTS must be configured"):
        validate_network_security_config()


def test_production_rejects_wildcard_network_trust(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_ENV", "production")
    monkeypatch.setenv("INDOONE_ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("INDOONE_ALLOWED_ORIGINS", "*")
    monkeypatch.setenv("INDOONE_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("INDOONE_TRUSTED_PROXY_IPS", "*")

    with pytest.raises(RuntimeError, match="wildcard"):
        validate_network_security_config()


def test_production_accepts_explicit_network_trust(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_ENV", "production")
    monkeypatch.setenv("INDOONE_ALLOWED_HOSTS", "api.example.com,api.internal.example")
    monkeypatch.setenv("INDOONE_ALLOWED_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("INDOONE_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("INDOONE_TRUSTED_PROXY_IPS", "127.0.0.1")

    validate_network_security_config()


def test_cors_allows_only_configured_origin(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_ENV", "development")
    monkeypatch.setenv("INDOONE_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("INDOONE_ALLOWED_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("INDOONE_TRUST_PROXY_HEADERS", "false")

    app = FastAPI()
    app.get("/")(lambda: {"status": "ok"})
    configure_network_security(app)
    client = TestClient(app)

    allowed = client.options(
        "/",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    blocked = client.options(
        "/",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    assert blocked.status_code == 400


def test_cors_allows_the_projects_own_web_origin_by_default(monkeypatch) -> None:
    """With INDOONE_ALLOWED_ORIGINS unset, the web app can still reach us."""
    monkeypatch.setenv("INDOONE_ENV", "development")
    monkeypatch.setenv("INDOONE_ALLOWED_HOSTS", "testserver")
    monkeypatch.delenv("INDOONE_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("INDOONE_TRUST_PROXY_HEADERS", "false")

    assert allowed_origins() == [
        "https://indooneteam.github.io",
        "https://indoone.indoworkspace.in",
        "https://sankalp.indoworkspace.in",
    ]

    app = FastAPI()
    app.get("/")(lambda: {"status": "ok"})
    configure_network_security(app)
    client = TestClient(app)

    response = client.get(
        "/",
        headers={"Origin": "https://indooneteam.github.io"},
    )

    assert response.headers.get("access-control-allow-origin") == (
        "https://indooneteam.github.io"
    )


def test_configured_origins_are_added_to_the_web_origin(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_ALLOWED_ORIGINS", "https://app.example.com")

    assert allowed_origins() == [
        "https://indooneteam.github.io",
        "https://indoone.indoworkspace.in",
        "https://sankalp.indoworkspace.in",
        "https://app.example.com",
    ]


def test_the_web_origin_is_allowed_even_when_another_is_configured(monkeypatch) -> None:
    """The variable adds origins; it does not remove the web app's own."""
    monkeypatch.setenv("INDOONE_ENV", "production")
    monkeypatch.setenv("INDOONE_ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("INDOONE_ALLOWED_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("INDOONE_TRUST_PROXY_HEADERS", "false")

    app = FastAPI()
    app.get("/")(lambda: {"status": "ok"})
    configure_network_security(app)
    client = TestClient(app)

    response = client.get(
        "/",
        headers={"Origin": "https://indooneteam.github.io"},
    )

    assert response.headers.get("access-control-allow-origin") == (
        "https://indooneteam.github.io"
    )


def test_the_indoone_subdomain_is_allowed() -> None:
    """The web app used to live at indoone.indoworkspace.in and still does while it moves.

    A browser sends the origin it was loaded from. If that origin is not in this list the
    request is refused before it is sent, and the site looks like the backend is down - so the
    subdomain has to be here before the DNS record is used, not after.
    """
    assert "https://indoone.indoworkspace.in" in allowed_origins()


def test_the_subdomain_gets_a_cors_header(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_ENV", "development")
    monkeypatch.setenv("INDOONE_ALLOWED_HOSTS", "testserver")

    app = FastAPI()
    app.get("/")(lambda: {"status": "ok"})
    configure_network_security(app)

    response = TestClient(app).get("/", headers={"Origin": "https://indoone.indoworkspace.in"})

    assert response.headers.get("access-control-allow-origin") == "https://indoone.indoworkspace.in"


def test_the_new_subdomain_is_allowed_before_the_dns_record_exists() -> None:
    """The site moves to sankalp.indoworkspace.in.

    A browser sends the origin it was loaded from, so the new address has to be allowed
    before the record points at it - otherwise the first person to open the new address gets
    every request refused, which looks exactly like the backend being down.
    """
    assert "https://sankalp.indoworkspace.in" in allowed_origins()
