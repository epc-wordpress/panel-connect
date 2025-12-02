import os
import httpx
import xmltodict
from urllib.parse import quote

API_USER = os.getenv("API_USER")
API_KEY = os.getenv("API_KEY")
CLIENT_IP = os.getenv("CLIENT_IP")


async def fetch_balances(client: httpx.AsyncClient):
    try:
        api_url = (
            f"https://api.namecheap.com/xml.response?ApiUser={quote(API_USER)}&ApiKey={quote(API_KEY)}"
            f"&UserName={quote(API_USER)}&Command=namecheap.users.getBalances&ClientIp={quote(CLIENT_IP)}"
        )
        r = await client.get(api_url)
        r.raise_for_status()
        data = xmltodict.parse(r.text)
        command_response = data.get("ApiResponse", {}).get("CommandResponse")
        if not command_response:
            raise RuntimeError("Invalid response structure: CommandResponse not found")
        balance_result = command_response[0].get("UserGetBalancesResult")[0].get("@")
        return {
            "currency": balance_result.get("Currency"),
            "availableBalance": float(balance_result.get("AvailableBalance") or 0),
            "accountBalance": float(balance_result.get("AccountBalance") or 0),
            "earnedAmount": float(balance_result.get("EarnedAmount") or 0),
            "withdrawableAmount": float(balance_result.get("WithdrawableAmount") or 0),
            "fundsRequiredForAutoRenew": float(balance_result.get("FundsRequiredForAutoRenew") or 0),
        }
    except Exception as e:
        raise


async def fetch_namecheap(client: httpx.AsyncClient):
    try:
        base_api_url = (
            f"https://api.namecheap.com/xml.response?ApiUser={quote(API_USER)}&ApiKey={quote(API_KEY)}"
            f"&UserName={quote(API_USER)}&Command=namecheap.domains.getList&ClientIp={quote(CLIENT_IP)}&Pagesize=100"
        )

        # first page
        r = await client.get(f"{base_api_url}&Page=1")
        r.raise_for_status()
        data = xmltodict.parse(r.text)
        command_response = data.get("ApiResponse", {}).get("CommandResponse")
        if not command_response:
            raise RuntimeError("Invalid response structure: CommandResponse not found")

        paging = command_response[0].get("Paging", [{}])[0]
        total_items = int(paging.get("TotalItems", 0) or 0)
        page_size = 100
        total_pages = (total_items + page_size - 1) // page_size

        all_domains = command_response[0].get("DomainGetListResult", [{}])[0].get("Domain") or []

        # fetch other pages if any
        for page in range(2, total_pages + 1):
            r = await client.get(f"{base_api_url}&Page={page}")
            r.raise_for_status()
            page_data = xmltodict.parse(r.text)
            paginated_command = page_data.get("ApiResponse", {}).get("CommandResponse")
            if not paginated_command:
                raise RuntimeError(f"Invalid response structure on page {page}")
            domains_on_page = paginated_command[0].get("DomainGetListResult", [{}])[0].get("Domain") or []
            if isinstance(domains_on_page, list):
                all_domains.extend(domains_on_page)
            else:
                all_domains.append(domains_on_page)

        balances = await fetch_balances(client)

        return {"allDomains": all_domains, "balances": balances}

    except Exception as e:
        return {"status": "error", "message": str(e), "allDomains": [], "balances": {}}
