import os
import httpx

CLIENT_IP = os.getenv("CLIENT_IP")
WHM_API_KEY = os.getenv("WHM_API_KEY")


async def get_bandwidth(client: httpx.AsyncClient):
    """Fetch bandwidth from WHM.

    httpx.AsyncClient.get() does not accept a `verify` kwarg per-request in
    some versions; verify should be set on the client. The caller may pass an
    AsyncClient already configured with the desired `verify` and `timeout`.
    """
    WHM_API_URL = f"https://{CLIENT_IP}:2087/json-api/showbw?api.version=1"
    try:
        r = await client.get(
            WHM_API_URL,
            headers={"Authorization": f"WHM root:{WHM_API_KEY}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}
