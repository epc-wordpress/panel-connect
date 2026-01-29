Python connector (duplicate of Node project)

Quick start

1. Copy `.env.example` to `.env` and fill in environment variables.

2. Build & run (local):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 3001
```

3. Build Docker image:

```bash
docker build -t python-connector:latest .
```

Endpoints

- `GET /fetch-namecheap-domains` — protected by JWT (requires Authorization header with Bearer token). On startup the app runs once and a scheduled job runs every 6 hours.

Notes

- This is a working port but should be tested with your env vars and Namecheap/WHM credentials.
- The JWT verification fetches JWKS from `CERTS_API_URL` and looks up the key by `kid`.
