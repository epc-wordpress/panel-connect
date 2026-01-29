import os
import httpx
import xmltodict
from urllib.parse import quote

API_USER = os.getenv("API_USER")
API_KEY = os.getenv("API_KEY")
CLIENT_IP = os.getenv("CLIENT_IP")

def _extract_namecheap_error(data: dict) -> str | None:
    api = (data or {}).get("ApiResponse") or {}
    if str(api.get("@Status", "")).upper() != "ERROR":
        return None
    errors = api.get("Errors", {}).get("Error")
    if not errors:
        return "NameCheap API error"
    if isinstance(errors, list):
        msg = "; ".join([str(e.get("#text") or e) for e in errors])
        return msg or "NameCheap API error"
    if isinstance(errors, dict):
        return str(errors.get("#text") or "NameCheap API error")
    return str(errors)


async def fetch_balances(client: httpx.AsyncClient):
    try:
        api_url = (
            f"https://api.namecheap.com/xml.response?"
            f"ApiUser={quote(API_USER)}&ApiKey={quote(API_KEY)}"
            f"&UserName={quote(API_USER)}&Command=namecheap.users.getBalances"
            f"&ClientIp={quote(CLIENT_IP)}"
        )

        r = await client.get(api_url)

        r.raise_for_status()

        data = xmltodict.parse(r.text)

        command_response = data.get("ApiResponse", {}).get("CommandResponse")
        if not command_response:
            return None

        balance_result = command_response.get("UserGetBalancesResult")
        if not balance_result:
            return None

        return {
            "currency": balance_result.get("@Currency"),
            "availableBalance": float(balance_result.get("@AvailableBalance") or 0),
            "accountBalance": float(balance_result.get("@AccountBalance") or 0),
            "earnedAmount": float(balance_result.get("@EarnedAmount") or 0),
            "withdrawableAmount": float(balance_result.get("@WithdrawableAmount") or 0),
            "fundsRequiredForAutoRenew": float(balance_result.get("@FundsRequiredForAutoRenew") or 0),
        }

    except Exception:
        raise



async def fetch_namecheap(client: httpx.AsyncClient):
    try:
        base_api_url = (
            f"https://api.namecheap.com/xml.response?"
            f"ApiUser={quote(API_USER)}&ApiKey={quote(API_KEY)}"
            f"&UserName={quote(API_USER)}&Command=namecheap.domains.getList"
            f"&ClientIp={quote(CLIENT_IP)}&Pagesize=100"
        )

        r = await client.get(f"{base_api_url}&Page=1")
        r.raise_for_status()

        data = xmltodict.parse(r.text)
        command_response = data.get("ApiResponse", {}).get("CommandResponse")
        if not command_response:
            raise RuntimeError("Invalid response structure: CommandResponse not found")

        paging = command_response.get("Paging", {})
        total_items = int(paging.get("TotalItems", 0) or 0)
        page_size = int(paging.get("PageSize", 100))
        total_pages = (total_items + page_size - 1) // page_size

        domains_result = command_response.get("DomainGetListResult", {})
        all_domains = domains_result.get("Domain") or []
        if not isinstance(all_domains, list):
            all_domains = [all_domains]

        for page in range(2, total_pages + 1):
            r = await client.get(f"{base_api_url}&Page={page}")
            r.raise_for_status()

            page_data = xmltodict.parse(r.text)
            paginated_command = page_data.get("ApiResponse", {}).get("CommandResponse")
            if not paginated_command:
                raise RuntimeError(f"Invalid response structure on page {page}")

            result = paginated_command.get("DomainGetListResult", {})
            domains_on_page = result.get("Domain") or []

            if isinstance(domains_on_page, list):
                all_domains.extend(domains_on_page)
            else:
                all_domains.append(domains_on_page)

        balances = await fetch_balances(client)

        return {
            "allDomains": all_domains,
            "balances": balances,
            "status": "success",
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "allDomains": [],
            "balances": {},
        }

async def fetch_domain_dns_records(client: httpx.AsyncClient, domain: str):
    try:
        if not (API_USER and API_KEY and CLIENT_IP):
            raise RuntimeError("NameCheap env vars missing: API_USER/API_KEY/CLIENT_IP")

        d = (domain or "").strip()
        d = d.replace("https://", "").replace("http://", "")
        d = d.split("/")[0].strip().rstrip(".")
        if d.count(".") < 1:
            raise ValueError("Invalid domain. Expected format: example.com")

        sld, tld = d.split(".", 1)

        api_url = (
            f"https://api.namecheap.com/xml.response?"
            f"ApiUser={quote(API_USER)}&ApiKey={quote(API_KEY)}"
            f"&UserName={quote(API_USER)}"
            f"&Command=namecheap.domains.dns.getHosts"
            f"&SLD={quote(sld)}&TLD={quote(tld)}"
            f"&ClientIp={quote(CLIENT_IP)}"
        )

        r = await client.get(api_url)
        r.raise_for_status()

        data = xmltodict.parse(r.text)
        err = _extract_namecheap_error(data)
        if err:
            raise RuntimeError(err)

        command_response = data.get("ApiResponse", {}).get("CommandResponse")
        if not command_response:
            return []

        result = command_response.get("DomainDNSGetHostsResult")
        if not result:
            return []

        hosts = result.get("host", [])
        if isinstance(hosts, dict):
            hosts = [hosts]

        records = []
        for h in hosts:
            records.append({
                "name": h.get("@Name"),
                "type": h.get("@Type"),
                "address": h.get("@Address"),
                "mxPref": h.get("@MXPref"),
                "ttl": h.get("@TTL"),
                "isActive": str(h.get("@IsActive")).lower() == "true",
            })

        return records

    except Exception:
        raise
