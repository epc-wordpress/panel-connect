import httpx
import pytest

from service import namecheap as nc
from helpers import mock_client, nc_ok, nc_error


def _domain_list(domains, total, page_size=100):
    items = "".join(f'<Domain Name="{d}" AutoRenew="true" IsExpired="false" />' for d in domains)
    return nc_ok(
        "namecheap.domains.getList",
        f"<DomainGetListResult>{items}</DomainGetListResult>"
        f"<Paging><TotalItems>{total}</TotalItems><CurrentPage>1</CurrentPage><PageSize>{page_size}</PageSize></Paging>",
    )


BALANCES = nc_ok(
    "namecheap.users.getBalances",
    '<UserGetBalancesResult Currency="USD" AvailableBalance="12.50" AccountBalance="20.00" '
    'EarnedAmount="1.5" WithdrawableAmount="3" FundsRequiredForAutoRenew="4.25" />',
)


def test_extract_error():
    assert nc._extract_namecheap_error(None) == "Empty response from NameCheap"
    assert nc._extract_namecheap_error({"ApiResponse": {"@Status": "OK"}}) is None
    import xmltodict
    assert nc._extract_namecheap_error(xmltodict.parse(nc_error("Bad key"))) == "Bad key"
    two = ('<ApiResponse Status="ERROR"><Errors><Error Number="1">One</Error>'
           '<Error Number="2">Two</Error></Errors></ApiResponse>')
    assert nc._extract_namecheap_error(xmltodict.parse(two)) == "One; Two"


async def test_fetch_balances():
    async with mock_client(lambda r: httpx.Response(200, text=BALANCES)) as c:
        assert await nc.fetch_balances(c) == {
            "currency": "USD", "availableBalance": 12.5, "accountBalance": 20.0,
            "earnedAmount": 1.5, "withdrawableAmount": 3.0, "fundsRequiredForAutoRenew": 4.25,
        }


async def test_fetch_namecheap_paginates_and_adds_balances():
    pages = []

    def handler(req):
        p = req.url.params
        if p["Command"] == "namecheap.users.getBalances":
            return httpx.Response(200, text=BALANCES)
        pages.append(p["Page"])
        names = {"1": ["a.com"], "2": ["b.com"]}[p["Page"]]
        return httpx.Response(200, text=_domain_list(names, total=2, page_size=1))

    async with mock_client(handler) as c:
        res = await nc.fetch_namecheap(c)

    assert pages == ["1", "2"]
    assert res["status"] == "success"
    assert [d["@Name"] for d in res["allDomains"]] == ["a.com", "b.com"]
    assert res["allDomains"][0]["@AutoRenew"] == "true"
    assert res["balances"]["availableBalance"] == 12.5


async def test_fetch_namecheap_single_domain_is_wrapped_in_list():
    def handler(req):
        if req.url.params["Command"] == "namecheap.users.getBalances":
            return httpx.Response(200, text=BALANCES)
        return httpx.Response(200, text=_domain_list(["only.com"], total=1))

    async with mock_client(handler) as c:
        res = await nc.fetch_namecheap(c)
    assert [d["@Name"] for d in res["allDomains"]] == ["only.com"]


async def test_fetch_namecheap_api_error_returns_error_status():
    async with mock_client(lambda r: httpx.Response(200, text=nc_error("Invalid IP"))) as c:
        res = await nc.fetch_namecheap(c)
    assert res["status"] == "error" and "Invalid IP" in res["message"]
    assert res["allDomains"] == []


async def test_retries_on_too_many_requests():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] < 3:
            return httpx.Response(200, text=nc_error("Too many requests"))
        return httpx.Response(200, text=BALANCES)

    async with mock_client(handler) as c:
        assert (await nc.fetch_balances(c))["currency"] == "USD"
    assert n["calls"] == 3


async def test_retries_on_http_5xx_then_gives_up():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(503)

    async with mock_client(handler) as c:
        with pytest.raises(RuntimeError, match="NameCheap request failed"):
            await nc.fetch_balances(c)
    assert n["calls"] == nc._MAX_RETRIES + 1


async def test_non_retryable_error_not_retried():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(200, text=nc_error("Invalid API key"))

    async with mock_client(handler) as c:
        with pytest.raises(RuntimeError, match="Invalid API key"):
            await nc.fetch_balances(c)
    assert n["calls"] == 1


# --- DNS records -----------------------------------------------------------

def _hosts(*hosts):
    return nc_ok("namecheap.domains.dns.getHosts",
                 f'<DomainDNSGetHostsResult Domain="example.com">{"".join(hosts)}</DomainDNSGetHostsResult>')


async def test_get_dns_records_multiple_and_single():
    one = '<host Name="@" Type="A" Address="1.1.1.1" MXPref="10" TTL="1800" IsActive="true" />'
    two = '<host Name="www" Type="CNAME" Address="example.com." MXPref="10" TTL="300" IsActive="false" />'

    async with mock_client(lambda r: httpx.Response(200, text=_hosts(one, two))) as c:
        recs = await nc.fetch_domain_dns_records(c, "https://example.com/path")
    assert [r["name"] for r in recs] == ["@", "www"]
    assert recs[0] == {"name": "@", "type": "A", "address": "1.1.1.1", "mxPref": "10", "ttl": "1800", "isActive": True}
    assert recs[1]["isActive"] is False

    async with mock_client(lambda r: httpx.Response(200, text=_hosts(one))) as c:
        assert len(await nc.fetch_domain_dns_records(c, "example.com")) == 1


async def test_get_dns_records_splits_sld_tld():
    seen = {}

    def handler(req):
        seen.update(req.url.params)
        return httpx.Response(200, text=_hosts())

    async with mock_client(handler) as c:
        await nc.fetch_domain_dns_records(c, "example.co.uk.")
    assert (seen["SLD"], seen["TLD"]) == ("example", "co.uk")


async def test_get_dns_records_invalid_domain():
    async with mock_client(lambda r: httpx.Response(200)) as c:
        with pytest.raises(ValueError, match="Invalid domain"):
            await nc.fetch_domain_dns_records(c, "localhost")


async def test_dns_requires_env(monkeypatch):
    monkeypatch.setattr(nc, "API_KEY", "")
    async with mock_client(lambda r: httpx.Response(200)) as c:
        with pytest.raises(RuntimeError, match="API_KEY"):
            await nc.fetch_domain_dns_records(c, "example.com")


SET_OK = nc_ok("namecheap.domains.dns.setHosts", '<DomainDNSSetHostsResult Domain="example.com" IsSuccess="true" />')


async def test_set_dns_records_builds_query():
    seen = {}

    def handler(req):
        seen.update(req.url.params)
        return httpx.Response(200, text=SET_OK)

    records = [
        {"name": "@", "type": "a", "address": "1.1.1.1", "ttl": 300},
        {"name": "mail", "type": "MX", "address": "mx.example.com", "mxPref": 5},
        {"name": "x", "type": "TXT", "address": "v=spf1 include:_spf.example.com ~all"},
    ]
    async with mock_client(handler) as c:
        res = await nc.set_domain_dns_records(c, "example.com", records)

    assert res == {"domain": "example.com", "success": True}
    assert seen["Command"] == "namecheap.domains.dns.setHosts"
    assert seen["RecordType1"] == "A" and seen["TTL1"] == "300"
    assert seen["MXPref2"] == "5" and seen["EmailType"] == "MX"
    assert seen["Address3"] == "v=spf1 include:_spf.example.com ~all"


async def test_set_dns_records_no_mx_no_emailtype():
    seen = {}

    def handler(req):
        seen.update(req.url.params)
        return httpx.Response(200, text=SET_OK)

    async with mock_client(handler) as c:
        await nc.set_domain_dns_records(c, "example.com", [{"name": "@", "type": "A", "address": "1.1.1.1"}])
    assert "EmailType" not in seen


@pytest.mark.parametrize("records,match", [
    ("nope", "must be a list"),
    (["x"], "must be a dictionary"),
    ([{"name": "@", "type": "A"}], "missing required fields"),
    ([{"name": "@", "type": "A", "address": "1.1.1.1", "ttl": 59}], "TTL"),
    ([{"name": "@", "type": "A", "address": "1.1.1.1", "ttl": 60001}], "TTL"),
])
async def test_set_dns_records_validation(records, match):
    async with mock_client(lambda r: httpx.Response(200, text=SET_OK)) as c:
        with pytest.raises(ValueError, match=match):
            await nc.set_domain_dns_records(c, "example.com", records)


async def test_set_dns_records_unsuccessful():
    xml = nc_ok("namecheap.domains.dns.setHosts", '<DomainDNSSetHostsResult Domain="example.com" IsSuccess="false" />')
    async with mock_client(lambda r: httpx.Response(200, text=xml)) as c:
        with pytest.raises(RuntimeError, match="Failed to set DNS"):
            await nc.set_domain_dns_records(c, "example.com", [{"name": "@", "type": "A", "address": "1.1.1.1"}])
