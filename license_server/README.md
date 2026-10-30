# Ludo Titan License Server

Flask service for the Ludo Titan bot. It stores machine authorization records, disclaimer acceptances, and pause state in MongoDB, and provides JSON endpoints plus a browser admin page. See the [repository README](../README.md) for the API, environment-variable reference, and bot setup.

## Run locally (Windows PowerShell)

From the repository root:

```powershell
Copy-Item license_server\.env.example license_server\.env
# Edit license_server\.env with a MongoDB URI and unique secret values.
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r license_server\requirements.txt
$env:PORT = "5000"
python license_server\app.py
```

The service requires `PORT`, `MONGO_URI`, `MONGO_DB_NAME`, `ADMIN_SECRET`, `ADMIN_SESSION_SECRET`, `PAUSED_MESSAGE`, `DISCLAIMER_VERSION`, `DISCLAIMER_TEXT`, `PAYMENT_INSTRUCTIONS`, `PAYMENT_URL`, and `HEADER_TEXT`. Values that may be empty are still required to be present in the environment. MongoDB must be reachable. Do not commit `.env` or real credentials.

## Deploy with Render

The root `render.yaml` defines a Python web service using `license_server/` as its root directory, installs `license_server/requirements.txt`, and starts Gunicorn. Configure all keys in the blueprint's `envVars` in the Render service settings; values marked `sync: false` need to be supplied explicitly.

The service depends on an external MongoDB deployment. Render and MongoDB accounts and service availability are outside this repository.
