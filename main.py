import os
import json
import asyncio
import base64
import time
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv
from service.namecheap import fetch_namecheap, fetch_domain_dns_records, set_domain_dns_records
from service.whm import get_bandwidth
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_jwks_lock = asyncio.Lock()
_jwks_fetched_at = 0
JWKS_TTL = 3600

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

async def _fetch_jwks(request: Request):
    global _jwks_cache, _jwks_fetched_at

    if not CERTS_API_URL:
        return None

    now = time.time()
    if _jwks_cache.get("keys") and now - _jwks_fetched_at < JWKS_TTL:
        return _jwks_cache

    async with _jwks_lock:
        if _jwks_cache.get("keys") and now - _jwks_fetched_at < JWKS_TTL:
            return _jwks_cache

        client = request.app.state.http_client
        r = await client.get(CERTS_API_URL)
        r.raise_for_status()

        _jwks_cache = r.json()
        _jwks_fetched_at = time.time()
        return _jwks_cache


def _rsa_key_from_jwks(jwks_key: dict) -> Optional[bytes]:
    """Convert JWKS RSA key to PEM format."""
    try:
        e = int.from_bytes(base64.urlsafe_b64decode(jwks_key['e'] + '=='), 'big')
        n = int.from_bytes(base64.urlsafe_b64decode(jwks_key['n'] + '=='), 'big')
        public_numbers = rsa.RSAPublicNumbers(e, n)
        public_key = public_numbers.public_key(default_backend())
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return pem
    except Exception:
        return None

async def _get_public_key_for_kid(kid: str, request: Request) -> Optional[bytes]:
    if not _jwks_cache.get("keys"):
        await _fetch_jwks(request)

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
        return JSONResponse(
            status_code=401,
            content={"detail": "Token is missing"},
        )

    parts = auth.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid Authorization header"},
        )

    token = parts[1]

    try:
        header = pyjwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            return JSONResponse(
                status_code=401,
                content={"detail": "Token header missing kid"},
            )

        pub_pem = await _get_public_key_for_kid(kid, request)
        if not pub_pem:
            return JSONResponse(
                status_code=401,
                content={"detail": "Public key for kid not found"},
            )

        decoded = pyjwt.decode(
            token,
            pub_pem,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )

        request.state.user = decoded

    except pyjwt.ExpiredSignatureError:
        return JSONResponse(
            status_code=401,
            content={"detail": "Token expired"},
        )
    except Exception as e:
        return JSONResponse(
            status_code=401,
            content={"detail": f"Invalid token: {str(e)}"},
        )

    return await call_next(request)


async def send_domains_to_server(domains, balances, bandwidth):
    client = app.state.http_client
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
    if not (isinstance(domains, list) and domains):
        return
    domain_data_array = []
    for domain in domains:
        attrs = {}
        if isinstance(domain, dict):
            if "$" in domain and isinstance(domain["$"], dict):
                attrs = domain["$"]
            elif "@" in domain and isinstance(domain["@"], dict):
                attrs = domain["@"]
            else:
                has_at_keys = any(k.startswith("@") for k in domain.keys())
                if has_at_keys:
                    for k, v in domain.items():
                        attrs[k[1:] if isinstance(k, str) and k.startswith("@") else k] = v
                else:
                    attrs = domain.copy()
        def get_attr(*names, default=None):
            for n in names:
                if n is None:
                    continue
                v = attrs.get(n)
                if v is not None:
                    return v
            return default
        name = get_attr("Name", "name")
        if not name:
            continue
        auto_renew_raw = get_attr("AutoRenew", "autoRenew", "auto_renew", default="false")
        created_raw = get_attr("Created", "created")
        expires_raw = get_attr("Expires", "expires")
        isexpired_raw = get_attr("IsExpired", "isExpired", "is_expired", default="false")
        islocked_raw = get_attr("IsLocked", "isLocked", "is_locked", default="false")
        isourdns_raw = get_attr("IsOurDNS", "isOurDNS", "is_our_dns", default="false")
        user_raw = get_attr("User", "user") or API_USER
        auto_renew = str(auto_renew_raw).lower() == "true"
        is_expired = str(isexpired_raw).lower() == "true"
        is_locked = str(islocked_raw).lower() == "true"
        is_our_dns = str(isourdns_raw).lower() == "true"
        domain_data_array.append({
            "AccountId": account_id,
            "Name": name,
            "AutoRenew": auto_renew,
            "Created": format_date(created_raw),
            "Expires": format_date(expires_raw),
            "IsExpired": is_expired,
            "IsLocked": is_locked,
            "IsOurDNS": is_our_dns,
            "User": user_raw,
        })
    data_to_send = {"accountId": account_id, "domains": domain_data_array}
    resp = await client.post(
        f"{SERVER_API_URL}/api/domains/array",
        json=data_to_send,
        headers={"Authorization": f"Bearer {SERVER_API_TOKEN}", "Content-Type": "application/json"},
    )
    resp.raise_for_status()

def format_date(date_str: Optional[str]):
    if not date_str:
        return None
    try:
        month, day, year = date_str.split('/')
        return f"{year}-{int(month):02d}-{int(day):02d}"
    except Exception:
        return date_str

async def fetch_and_send_info():
    client = app.state.http_client
    bandwidth = {}
    try:
        insecure_client = app.state.insecure_http_client
        bandwidth = await get_bandwidth(insecure_client)
    except Exception as e:
        bandwidth = {"error": str(e)}
    if DRY_RUN:
        return "Dry run mode enabled"
    info = {"allDomains": [], "balances": {}}
    if not NO_NC:
        info = await fetch_namecheap(client)
    await send_domains_to_server(info.get("allDomains", []), info.get("balances", {}), bandwidth)
    return f"Fetched {len(info.get('allDomains', []))} domains"

@app.get("/fetch-namecheap-domains")
async def fetch_endpoint(request: Request):
    result = await fetch_and_send_info()
    return {"result": result}

class DNSRecord(BaseModel):
    name: str
    type: str
    address: str
    mxPref: Optional[int] = None
    ttl: Optional[int] = None

class DNSRecordsUpdate(BaseModel):
    records: List[DNSRecord]

@app.get("/dns-records/{domain}")
async def get_dns_records(domain: str, request: Request):
    try:
        client = request.app.state.http_client
        records = await fetch_domain_dns_records(client, domain)
        return {
            "domain": domain,
            "records": records,
            "count": len(records)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch DNS records: {str(e)}")

@app.put("/dns-records/{domain}")
async def update_dns_records(domain: str, update_data: DNSRecordsUpdate, request: Request):
    try:
        client = request.app.state.http_client
        records_data = [record.dict() for record in update_data.records]
        result = await set_domain_dns_records(client, domain, records_data)
        return {
            "domain": result.get("domain"),
            "success": result.get("success"),
            "records_count": len(records_data)
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid request: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update DNS records: {str(e)}")


@app.on_event("startup")
async def startup_event():
    app.state.http_client = httpx.AsyncClient(
        timeout=10,
        verify=True,
        limits=httpx.Limits(
            max_connections=50,
            max_keepalive_connections=10,
        ),
    )

    app.state.insecure_http_client = httpx.AsyncClient(
        timeout=10,
        verify=False,
        limits=httpx.Limits(
            max_connections=20,
            max_keepalive_connections=5,
        ),
    )

    await fetch_and_send_info()

    async def loop():
        while True:
            try:
                await fetch_and_send_info()
            except Exception as e:
                logger.exception(e)
            await asyncio.sleep(6 * 60 * 60)

    asyncio.create_task(loop())


@app.on_event("shutdown")
async def shutdown_event():
    await app.state.http_client.aclose()
    await app.state.insecure_http_client.aclose()