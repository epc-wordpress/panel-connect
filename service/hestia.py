import os
import httpx

HESTIA_HOST = os.getenv("HESTIA_HOST")
HESTIA_ADMIN = os.getenv("HESTIA_ADMIN", "admin")
HESTIA_API_KEY = os.getenv("HESTIA_API_KEY")


def _hestia_url():
    return f"https://{HESTIA_HOST}:8083/api/"


async def _call(client: httpx.AsyncClient, cmd: str, *args) -> dict | list:
    data = {
        "user": HESTIA_ADMIN,
        "password": HESTIA_API_KEY,
        "returncode": "no",
        "cmd": cmd,
    }
    for i, arg in enumerate(args, 1):
        data[f"arg{i}"] = arg

    r = await client.post(_hestia_url(), data=data, timeout=30)
    r.raise_for_status()
    return r.json()


async def get_bandwidth(client: httpx.AsyncClient) -> dict:
    try:
        return await _call(client, "v-get-sys-info")
    except Exception as e:
        return {"error": str(e)}


async def get_domains(client: httpx.AsyncClient) -> list:
    try:
        users_data = await _call(client, "v-list-users")
        if not isinstance(users_data, dict):
            return []

        all_domains = []
        for username in users_data.keys():
            try:
                domains_data = await _call(client, "v-list-web-domains", username)
                if not isinstance(domains_data, dict):
                    continue
                for domain_name, info in domains_data.items():
                    suspended = str(info.get("SUSPENDED", "no")).lower() == "yes"
                    all_domains.append({
                        "Name": domain_name,
                        "AutoRenew": "false",
                        "Created": info.get("DATE"),
                        "Expires": None,
                        "IsExpired": str(suspended).lower(),
                        "IsLocked": "false",
                        "IsOurDNS": "true",
                        "User": username,
                    })
            except Exception:
                continue

        return all_domains
    except Exception as e:
        return []
