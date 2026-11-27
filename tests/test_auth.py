import time

import pytest

import main
from helpers import mock_client, nc_ok

DNS_XML = nc_ok(
    "namecheap.domains.dns.getHosts",
    '<DomainDNSGetHostsResult Domain="example.com">'
    '<host Name="@" Type="A" Address="1.1.1.1" MXPref="10" TTL="1800" IsActive="true" />'
    "</DomainDNSGetHostsResult>",
)


@pytest.fixture
def authed_client(client):
    client.app.state.http_client = mock_client(lambda req: __import__("httpx").Response(200, text=DNS_XML))
    return client


def test_missing_token(client):
    r = client.get("/dns-records/example.com")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is missing"


@pytest.mark.parametrize("header", ["Token abc", "Bearer", "Bearer a b"])
def test_malformed_header(client, header):
    r = client.get("/dns-records/example.com", headers={"Authorization": header})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid Authorization header"


def test_garbage_token(client):
    r = client.get("/dns-records/example.com", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


def test_token_without_kid(client, make_token):
    r = client.get("/dns-records/example.com", headers={"Authorization": f"Bearer {make_token(kid=None)}"})
    assert r.status_code == 401
    assert "kid" in r.json()["detail"]


def test_unknown_kid(client, make_token):
    r = client.get("/dns-records/example.com", headers={"Authorization": f"Bearer {make_token(kid='other')}"})
    assert r.status_code == 401
    assert "not found" in r.json()["detail"]


def test_expired_token(client, make_token):
    r = client.get("/dns-records/example.com", headers={"Authorization": f"Bearer {make_token(exp_in=-10)}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Token expired"


def test_token_signed_with_wrong_key(client, make_token):
    from conftest import _new_key
    token = make_token(key=_new_key())
    r = client.get("/dns-records/example.com", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["detail"].startswith("Invalid token")


def test_valid_token_passes(authed_client, auth):
    r = authed_client.get("/dns-records/example.com", headers=auth)
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_options_preflight_skips_auth(client):
    r = client.options(
        "/dns-records/example.com",
        headers={"Origin": "http://x", "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code != 401


def test_jwks_fetched_lazily_and_cached(client, jwks, auth, monkeypatch):
    """Empty cache -> key is fetched from CERTS_API_URL once, then reused."""
    import httpx
    calls = []

    def handler(req):
        if str(req.url) == main.CERTS_API_URL:
            calls.append(1)
            return httpx.Response(200, json=jwks)
        return httpx.Response(200, text=DNS_XML)

    monkeypatch.setattr(main, "_jwks_cache", {"keys": []})
    monkeypatch.setattr(main, "_jwks_fetched_at", 0)
    client.app.state.http_client = mock_client(handler)

    assert client.get("/dns-records/example.com", headers=auth).status_code == 200
    assert client.get("/dns-records/example.com", headers=auth).status_code == 200
    assert len(calls) == 1
