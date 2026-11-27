import os
import sys
import time
import base64
from pathlib import Path

# Env must be set before `main` / service modules are imported (they read it
# at import time). Set explicitly so a developer's local .env can't leak in.
os.environ.update({
    "NODE_ENV": "development",
    "CERTS_API_URL": "https://certs.test/jwks",
    "API_USER": "ncuser",
    "API_KEY": "nckey",
    "CLIENT_IP": "1.2.3.4",
    "SERVER_API_URL": "https://server.test",
    "SERVER_API_TOKEN": "srv-token",
    "TEAM": "team-a",
    "NAME": "panel-1",
    "PANEL_TYPE": "whm",
    "NO_NC": "false",
    "DRY_RUN": "false",
    "WHM_API_KEY": "whmkey",
    "HESTIA_API_KEY": "hestiakey",
})

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import main
from service import namecheap

KID = "test-kid"


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


@pytest.fixture(scope="session")
def rsa_key():
    return _new_key()


@pytest.fixture(scope="session")
def jwks(rsa_key):
    nums = rsa_key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256",
                      "n": _b64u(nums.n), "e": _b64u(nums.e)}]}


@pytest.fixture
def make_token(rsa_key):
    def _make(*, kid=KID, key=None, exp_in=300, claims=None):
        payload = {"sub": "user-1", "exp": int(time.time()) + exp_in, **(claims or {})}
        headers = {"kid": kid} if kid else {}
        return pyjwt.encode(payload, _pem(key or rsa_key), algorithm="RS256", headers=headers)
    return _make


@pytest.fixture
def auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
def client(jwks, monkeypatch):
    """TestClient with JWKS pre-cached and startup fetch/cron neutralised."""
    async def _noop():
        return "noop"

    monkeypatch.setattr(main, "fetch_and_send_info", _noop)
    monkeypatch.setattr(main, "_jwks_cache", jwks)
    monkeypatch.setattr(main, "_jwks_fetched_at", time.time())
    main.limiter.reset()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def fast_namecheap(monkeypatch):
    """No real pacing/backoff sleeps in tests."""
    import asyncio

    class _FastAsyncio:
        """Proxy for the `asyncio` name inside service.namecheap only — patching
        asyncio.sleep globally would turn main's 6h cron loop into a busy loop."""
        def __getattr__(self, name):
            return getattr(asyncio, name)

        @staticmethod
        async def sleep(_):
            return None

    monkeypatch.setattr(namecheap, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(namecheap, "asyncio", _FastAsyncio())
