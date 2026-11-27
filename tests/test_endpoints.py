import json

import httpx
import pytest

import main
from helpers import mock_client, nc_ok, nc_error

SET_OK = nc_ok("namecheap.domains.dns.setHosts",
               '<DomainDNSSetHostsResult Domain="example.com" IsSuccess="true" />')


def _record(**kw):
    return {"name": "@", "type": "A", "address": "1.1.1.1", **kw}


def test_get_dns_records_error_is_500(client, auth):
    client.app.state.http_client = mock_client(lambda r: httpx.Response(200, text=nc_error("Invalid request IP")))
    r = client.get("/dns-records/example.com", headers=auth)
    assert r.status_code == 500
    assert "Invalid request IP" in r.json()["detail"]


def test_put_dns_records_ok(client, auth):
    seen = {}

    def handler(req):
        seen["q"] = dict(req.url.params)
        return httpx.Response(200, text=SET_OK)

    client.app.state.http_client = mock_client(handler)
    r = client.put("/dns-records/example.com", headers=auth, json={"records": [_record(), _record(name="mail", type="MX", address="mx.example.com")]})
    assert r.status_code == 200
    assert r.json() == {"domain": "example.com", "success": True, "records_count": 2}
    assert seen["q"]["Command"] == "namecheap.domains.dns.setHosts"
    assert seen["q"]["EmailType"] == "MX"
    assert seen["q"]["MXPref2"] == "10"


def test_put_dns_records_validation_400(client, auth):
    client.app.state.http_client = mock_client(lambda r: httpx.Response(200, text=SET_OK))
    r = client.put("/dns-records/example.com", headers=auth, json={"records": [_record(ttl=5)]})
    assert r.status_code == 400
    assert "TTL" in r.json()["detail"]


def test_put_dns_records_bad_body_422(client, auth):
    r = client.put("/dns-records/example.com", headers=auth, json={"records": [{"name": "@"}]})
    assert r.status_code == 422


def test_fetch_endpoint_rate_limited(client, auth):
    codes = [client.get("/fetch-namecheap-domains", headers=auth).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


@pytest.mark.parametrize("raw,expected", [
    ("03/25/2026", "2026-03-25"),
    ("12/01/2025", "2025-12-01"),
    (None, None),
    ("", None),
    ("2026-03-25", "2026-03-25"),
])
def test_format_date(raw, expected):
    assert main.format_date(raw) == expected


async def test_send_domains_to_server(monkeypatch):
    sent = {}

    def handler(req):
        body = json.loads(req.content)
        path = req.url.path
        sent[path] = (body, req.headers["authorization"])
        if path.endswith("update-team"):
            return httpx.Response(200, json={"teamId": 7})
        if path.endswith("update-account"):
            return httpx.Response(200, json={"accountId": 42})
        return httpx.Response(200, json={})

    main.app.state.http_client = mock_client(handler)
    domains = [
        {"@Name": "a.com", "@AutoRenew": "true", "@Created": "01/02/2024", "@Expires": "01/02/2027",
         "@IsExpired": "false", "@IsLocked": "true", "@IsOurDNS": "true"},
        {"@Name": ""},  # skipped: no name
    ]
    await main.send_domains_to_server(domains, {"availableBalance": 5.5, "fundsRequiredForAutoRenew": 1.0}, {"x": 1})

    assert sent["/panel/team/update-team"][0] == {"name": "team-a"}
    assert sent["/panel/team/update-team"][1] == "Bearer srv-token"
    account = sent["/panel/team/update-account"][0]
    assert account["team_id"] == 7 and account["availableBalance"] == 5.5 and account["panel"] == "whm"
    payload = sent["/panel/domains/array"][0]
    assert payload["accountId"] == 42
    assert payload["domains"] == [{
        "AccountId": 42, "Name": "a.com", "AutoRenew": True, "Created": "2024-01-02",
        "Expires": "2027-01-02", "IsExpired": False, "IsLocked": True, "IsOurDNS": True, "User": "ncuser",
    }]


async def test_send_domains_server_error_raises():
    main.app.state.http_client = mock_client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        await main.send_domains_to_server([], {}, {})


async def test_fetch_and_send_info_dry_run(monkeypatch):
    async def bw(_):
        return {"ok": 1}

    monkeypatch.setattr(main, "DRY_RUN", True)
    monkeypatch.setattr(main, "whm_get_bandwidth", bw)
    main.app.state.http_client = main.app.state.insecure_http_client = object()
    assert await main.fetch_and_send_info() == "Dry run mode enabled"


async def test_fetch_and_send_info_full_cycle(monkeypatch):
    calls = {}

    async def bw(_):
        return {"bw": 1}

    async def nc(_):
        return {"allDomains": [{"@Name": "a.com"}], "balances": {"availableBalance": 1}}

    async def send(domains, balances, bandwidth):
        calls["args"] = (domains, balances, bandwidth)

    monkeypatch.setattr(main, "whm_get_bandwidth", bw)
    monkeypatch.setattr(main, "fetch_namecheap", nc)
    monkeypatch.setattr(main, "send_domains_to_server", send)
    main.app.state.http_client = main.app.state.insecure_http_client = object()
    assert await main.fetch_and_send_info() == "Fetched 1 domains"
    assert calls["args"] == ([{"@Name": "a.com"}], {"availableBalance": 1}, {"bw": 1})


async def test_fetch_and_send_info_whm_failure_still_sends(monkeypatch):
    calls = {}

    async def bw(_):
        raise RuntimeError("whm down")

    async def send(domains, balances, bandwidth):
        calls["bandwidth"] = bandwidth

    monkeypatch.setattr(main, "NO_NC", True)
    monkeypatch.setattr(main, "whm_get_bandwidth", bw)
    monkeypatch.setattr(main, "send_domains_to_server", send)
    main.app.state.http_client = main.app.state.insecure_http_client = object()
    await main.fetch_and_send_info()
    assert calls["bandwidth"] == {"error": "whm down"}
