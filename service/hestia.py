import os
import httpx
from datetime import datetime, timezone

HESTIA_API_KEY = os.getenv("HESTIA_API_KEY")


def _hestia_url():
    return f"https://127.0.0.1:8083/api/"

async def _call(client: httpx.AsyncClient, cmd: str, *args) -> dict | list:
    data = {
        "hash": HESTIA_API_KEY,
        "cmd": cmd,
    }
    for i, arg in enumerate(args, 1):
        data[f"arg{i}"] = arg
    r = await client.post(_hestia_url(), data=data, timeout=30)
    r.raise_for_status()
    return r.json()


async def _fetch_users_with_domains(client: httpx.AsyncClient) -> dict:
    """Raises on any per-user failure — a partial user list must never be
    reported as a valid snapshot, since the caller deletes hosted domains
    that are missing from the snapshot it receives."""
    users_data = await _call(client, "v-list-users", "json")
    if not isinstance(users_data, dict):
        raise ValueError(f"v-list-users returned non-dict: type={type(users_data).__name__} value={users_data!r}")
    result = {}
    for username, uinfo in users_data.items():
        try:
            d = await _call(client, "v-list-web-domains", username, "json")
        except Exception as e:
            raise RuntimeError(f"v-list-web-domains({username}) failed: {type(e).__name__}: {e}") from e
        if not isinstance(d, dict):
            raise ValueError(f"v-list-web-domains({username}) returned non-dict: type={type(d).__name__} value={d!r}")
        result[username] = {"info": uinfo, "domains": d}
    return result


def _main_domain(domains: list[str]) -> str:
    """Pick the most likely primary domain — fewest dots, then shortest."""
    if not domains:
        return ""
    return min(domains, key=lambda d: (d.count("."), len(d)))


async def fetch_all(client: httpx.AsyncClient) -> tuple[dict, list]:
    """Returns (bandwidth_dict, domains_list) in one pass."""
    try:
        users = await _fetch_users_with_domains(client)
    except Exception as e:
        print(f"[hestia] fetch_all FAILED: {type(e).__name__}: {e}")
        return {"error": str(e)}, []

    now = datetime.now(timezone.utc)
    acct = []
    total_bytes = 0
    all_domains = []
    skipped_users = []

    for username, udata in users.items():
        if username == "admin":
            continue

        domains = udata["domains"]
        if not domains:
            skipped_users.append(username)
            continue

        bwusage = []
        user_bytes = 0

        for domain_name, dinfo in domains.items():
            bw_mb = float(dinfo.get("U_BANDWIDTH", 0) or 0)
            bw_bytes = int(bw_mb * 1024 * 1024)
            user_bytes += bw_bytes
            bwusage.append({
                "domain": domain_name,
                "usage": str(bw_bytes) if bw_bytes else 0,
                "deleted": 0,
            })
            suspended = str(dinfo.get("SUSPENDED", "no")).lower() == "yes"
            all_domains.append({
                "Name": domain_name,
                "AutoRenew": "false",
                "Created": dinfo.get("DATE"),
                "Expires": None,
                "IsExpired": str(suspended).lower(),
                "IsLocked": "false",
                "IsOurDNS": "true",
                "User": username,
            })

        total_bytes += user_bytes
        acct.append({
            "limit": 0,
            "maindomain": _main_domain(list(domains.keys())),
            "user": username,
            "reseller": 0,
            "deleted": 0,
            "bwusage": bwusage,
            "bwlimited": 0,
            "owner": "root",
            "totalbytes": user_bytes,
        })

    if skipped_users:
        print(f"[hestia] skipped users with genuinely zero domains: {skipped_users}")
    print(f"[hestia] fetch_all summary: accounts_sent={[a['user'] for a in acct]} total_domains={len(all_domains)} total_bytes={total_bytes}")

    bandwidth = {
        "metadata": {"result": 1, "version": 1, "command": "showbw", "reason": "OK"},
        "data": {
            "reseller": "root",
            "acct": acct,
            "totalused": str(total_bytes),
            "month": now.month,
            "year": now.year,
        },
    }
    return bandwidth, all_domains


if __name__ == "__main__":
    import asyncio, json

    async def _test():
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            bw, domains = await fetch_all(client)
            print("\n=== bandwidth ===")
            print(json.dumps(bw, indent=2))
            print(f"\n=== domains ({len(domains)}) ===")
            print(json.dumps(domains[:3], indent=2))

    asyncio.run(_test())
