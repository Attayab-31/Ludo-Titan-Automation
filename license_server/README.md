# License Server Deployment (Render)

## Quick start (local)
1. Copy `.env.example` to `.env` and fill values.
2. Install deps: `pip install -r requirements.txt`
3. Run: `python app.py`

## Render deployment (from GitHub)
1. Push this repo to GitHub.
2. On Render, create a new **Web Service** from the repo.
3. Render will detect `render.yaml` and set everything automatically.
4. In Render **Environment** settings, fill the secrets: `ADMIN_SECRET`, `ADMIN_SESSION_SECRET`, `MASTER_LICENSE_KEY` (can be empty), `PAUSED_MESSAGE`, `DISCLAIMER_TEXT`, `PAYMENT_INSTRUCTIONS`, `PAYMENT_URL` (can be empty).

Notes:
- The database is stored on a Render disk at `/var/data/licenses.db`.
- `DISCLAIMER_VERSION` can be bumped to force re-acceptance.
