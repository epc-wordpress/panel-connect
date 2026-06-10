import os
import httpx

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
    print(f"[hestia] {cmd} key_set={bool(HESTIA_API_KEY)}")
    r = await client.post(_hestia_url(), data=data, timeout=30)
    print(f"[hestia] {cmd} status={r.status_code}")
    r.raise_for_status()
    return r.json()


async def _fetch_users_with_domains(client: httpx.AsyncClient) -> dict:
    users_data = await _call(client, "v-list-users")
    if not isinstance(users_data, dict):
        return {}
    result = {}
    for username, uinfo in users_data.items():
        domains = {}
        try:
            d = await _call(client, "v-list-web-domains", username)
            if isinstance(d, dict):
                domains = d
        except Exception:
            pass
        result[username] = {"info": uinfo, "domains": domains}
    return result


async def fetch_all(client: httpx.AsyncClient) -> tuple[dict, list]:
    """Returns (bandwidth_dict, domains_list) in one pass."""
    try:
        users = await _fetch_users_with_domains(client)
    except Exception as e:
        return {"error": str(e)}, []

    acct = []
    total_bytes = 0
    all_domains = []

    for username, data in users.items():
        uinfo = data["info"]
        domains = data["domains"]

        bw_mb = float(uinfo.get("U_BANDWIDTH", 0) or 0)
        bw_bytes = int(bw_mb * 1024 * 1024)
        total_bytes += bw_bytes

        bwusage = []
        for domain_name, dinfo in domains.items():
            d_bw_mb = float(dinfo.get("U_BANDWIDTH", 0) or 0)
            bwusage.append({
                "domain": domain_name,
                "usage": int(d_bw_mb * 1024 * 1024),
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

        acct.append({
            "user": username,
            "maindomain": next(iter(domains), ""),
            "totalbytes": bw_bytes,
            "bwlimited": 0,
            "bwusage": bwusage,
            "deleted": 0,
            "reseller": 0,
            "owner": "root",
        })

    bandwidth = {
        "metadata": {"reason": "OK", "command": "showbw", "result": 1, "version": 1},
        "data": {
            "totalused": str(total_bytes),
            "acct": acct,
            "reseller": "root",
        },
    }
    return bandwidth, all_domains
