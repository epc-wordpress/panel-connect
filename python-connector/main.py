import os
import json
import asyncio
import base64
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from service.namecheap import fetch_namecheap
from service.whm import get_bandwidth

load_dotenv()

CERTS_API_URL = os.getenv("CERTS_API_URL")
IS_PRODUCTION = os.getenv("NODE_ENV") == "production"
CLIENT_URL = os.getenv("CLIENT_URL")
PORT = int(os.getenv("PORT", 3001))
NAME = os.getenv("NAME")
CLIENT_IP = os.getenv("CLIENT_IP")
SERVER_API_URL = os.getenv("SERVER_API_URL")
SERVER_API_TOKEN = os.getenv("SERVER_API_TOKEN")
TEAM = os.getenv("TEAM")
NO_NC = os.getenv("NO_NC", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
API_USER = os.getenv("API_USER")

app = FastAPI()

origins = [CLIENT_URL] if IS_PRODUCTION and CLIENT_URL else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

_jwks_cache = {"keys": []}

async def _fetch_jwks():
    global _jwks_cache
    if not CERTS_API_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(CERTS_API_URL)
            r.raise_for_status()
            _jwks_cache = r.json()
            return _jwks_cache
    except Exception:
        return None

def _rsa_key_from_jwks(jwks_key: dict) -> Optional[bytes]:
    """Convert JWKS RSA key to PEM format."""
    try:
        # Extract RSA components from JWKS
        e = int.from_bytes(base64.urlsafe_b64decode(jwks_key['e'] + '=='), 'big')
        n = int.from_bytes(base64.urlsafe_b64decode(jwks_key['n'] + '=='), 'big')
        
        # Create RSA public key
        public_numbers = rsa.RSAPublicNumbers(e, n)
        public_key = public_numbers.public_key(default_backend())
        
        # Serialize to PEM
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return pem
    except Exception:
        return None

async def _get_public_key_for_kid(kid: str) -> Optional[bytes]:
    if not _jwks_cache.get("keys"):
        await _fetch_jwks()
    for key in _jwks_cache.get("keys", []):
        if key.get("kid") == kid and key.get("kty") == "RSA":
            return _rsa_key_from_jwks(key)
    return None


@app.middleware("http")
async def verify_jwt_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    auth = request.headers.get("authorization")
    if not auth:
        return HTTPException(status_code=401, detail="Token is missing")
    try:
        token = auth.split(" ")[1]
    except Exception:
        return HTTPException(status_code=401, detail="Token is missing")

    try:
        header = pyjwt.get_unverified_header(token)
        kid = header.get("kid")
        pub_pem = await _get_public_key_for_kid(kid)
        if not pub_pem:
            raise Exception("Public key for kid not found")
        decoded = pyjwt.decode(token, pub_pem, algorithms=["RS256"], options={"verify_aud": False})
        request.state.user = decoded
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

    return await call_next(request)


async def send_domains_to_server(domains, balances, bandwidth):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            team_resp = await client.post(
                f"{SERVER_API_URL}/api/team/update-team",
                json={"name": TEAM},
                headers={"Authorization": f"Bearer {SERVER_API_TOKEN}", "Content-Type": "application/json"},
            )
            team_resp.raise_for_status()
            team_id = team_resp.json().get("teamId")

            account_data = {
                "server_name": NAME,
                "hosting_price": 0.00,
                "team_id": team_id,
                "availableBalance": balances.get("availableBalance") if balances else 0.00,
                "fundsRequiredForAutoRenew": balances.get("fundsRequiredForAutoRenew") if balances else 0.00,
                "client_ip": CLIENT_IP,
                "bandwidth": bandwidth,
            }

            acc_resp = await client.post(
                f"{SERVER_API_URL}/api/team/update-account",
                json=account_data,
                headers={"Authorization": f"Bearer {SERVER_API_TOKEN}", "Content-Type": "application/json"},
            )
            acc_resp.raise_for_status()
            account_id = acc_resp.json().get("accountId")

            if isinstance(domains, list) and domains:
                domain_data_array = []
                for domain in domains:
                    attrs = domain.get("@", {})
                    domain_data_array.append(
                        {
                            "AccountId": account_id,
                            "Name": attrs.get("Name"),
                            "AutoRenew": attrs.get("AutoRenew") == "true",
                            "Created": format_date(attrs.get("Created")),
                            "Expires": format_date(attrs.get("Expires")),
                            "IsExpired": attrs.get("IsExpired") == "true",
                            "IsLocked": attrs.get("IsLocked") == "true",
                            "IsOurDNS": attrs.get("IsOurDNS") == "true",
                            "User": API_USER,
                        }
                    )

                data_to_send = {"accountId": account_id, "domains": domain_data_array}
                try:
                    await client.post(
                        f"{SERVER_API_URL}/api/domains/array",
                        json=data_to_send,
                        headers={"Authorization": f"Bearer {SERVER_API_TOKEN}", "Content-Type": "application/json"},
                    )
                except Exception as e:
                    print("Error sending domains:", e)

    except Exception as e:
        print("Error sending domains to the server:", e)


def format_date(date_str: Optional[str]):
    if not date_str:
        return None
    try:
        month, day, year = date_str.split('/')
        formatted = f"{year}-{int(month):02d}-{int(day):02d}"
        return formatted
    except Exception:
        return date_str


async def fetch_and_send_info():
    async with httpx.AsyncClient(verify=True, timeout=60) as client:
        bandwidth = await get_bandwidth(client)

        if DRY_RUN:
            print("Dry run mode enabled. Skipping sending domains to server.")
            return "Dry run mode enabled"

        info = {"allDomains": [], "balances": {}}

        if NO_NC:
            print("No Namecheap mode enabled. Skipping fetching domains from Namecheap.")
        else:
            info = await fetch_namecheap(client)

        await send_domains_to_server(info.get("allDomains", []), info.get("balances", {}), bandwidth)

        if isinstance(info.get("allDomains"), list):
            print(f"Fetched {len(info.get('allDomains'))} domains from Namecheap.")
        else:
            print("No domains were fetched or allDomains is not an array.")


@app.get("/fetch-namecheap-domains")
async def fetch_endpoint(request: Request):
    try:
        result = await fetch_and_send_info()
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.on_event("startup")
async def startup_event():
    try:
        await fetch_and_send_info()
    except Exception as e:
        print("Error fetching domains at startup:", e)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: asyncio.create_task(fetch_and_send_info()), "cron", hour="*/6")
    scheduler.start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
