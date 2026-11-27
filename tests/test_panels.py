import json

import httpx
import pytest

from service import hestia, whm
from helpers import mock_client


async def test_whm_get_bandwidth_ok():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": {"acct": []}})

    async with mock_client(handler) as c:
        assert await whm.get_bandwidth(c) == {"data": {"acct": []}}
    assert seen["auth"].startswith("WHM root:")
    assert ":2087/json-api/showbw" in seen["url"]


async def test_whm_get_bandwidth_error_is_returned_not_raised():
    async with mock_client(lambda r: httpx.Response(500)) as c:
        assert "error" in await whm.get_bandwidth(c)


def _hestia_handler(users, domains_by_user, fail_user=None):
    def handler(req):
        form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
        cmd = form["cmd"]
        if cmd == "v-list-users":
            return httpx.Response(200, json=users)
        if cmd == "v-list-web-domains":
            user = form["arg1"]
            if user == fail_user:
                return httpx.Response(500)
            return httpx.Response(200, json=domains_by_user[user])
        return httpx.Response(404)
    return handler


async def test_hestia_fetch_all():
    users = {"admin": {}, "bob": {}, "empty": {}}
    domains = {
        "admin": {"admin.com": {}},
        "bob": {
            "bob.com": {"U_BANDWIDTH": "2", "SUSPENDED": "no", "DATE": "2024-01-01"},
            "sub.bob.com": {"U_BANDWIDTH": "1", "SUSPENDED": "yes", "DATE": "2024-02-01"},
        },
        "empty": {},
    }
    async with mock_client(_hestia_handler(users, domains)) as c:
        bw, all_domains = await hestia.fetch_all(c)

    assert {d["Name"] for d in all_domains} == {"bob.com", "sub.bob.com"}  # admin and empty skipped
    by_name = {d["Name"]: d for d in all_domains}
    assert by_name["sub.bob.com"]["IsExpired"] == "true"
    assert by_name["bob.com"]["IsExpired"] == "false"
    assert by_name["bob.com"]["User"] == "bob"

    acct = bw["data"]["acct"]
    assert len(acct) == 1 and acct[0]["user"] == "bob"
    assert acct[0]["maindomain"] == "bob.com"
    assert acct[0]["totalbytes"] == 3 * 1024 * 1024
    assert bw["data"]["totalused"] == str(3 * 1024 * 1024)


async def test_hestia_partial_failure_reports_error_and_no_domains():
    """A partial user list must never look like a valid snapshot."""
    users = {"bob": {}, "alice": {}}
    domains = {"bob": {"bob.com": {}}, "alice": {"alice.com": {}}}
    async with mock_client(_hestia_handler(users, domains, fail_user="alice")) as c:
        bw, all_domains = await hestia.fetch_all(c)
    assert "error" in bw and all_domains == []


def test_hestia_main_domain():
    assert hestia._main_domain(["a.b.com", "b.com", "zz.b.com"]) == "b.com"
    assert hestia._main_domain([]) == ""
