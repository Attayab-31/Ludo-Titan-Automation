# License Server Deployment (Render)

## Quick start (local)
1. Copy `.env.example` to `.env` and fill values.
2. Install deps: `pip install -r requirements.txt`
3. Run: `python app.py`

## Render deployment (from GitHub)
1. Push this repo to GitHub.
2. On Render, create a new **Web Service** from the repo.
3. Render will detect `render.yaml` and set everything automatically.
4. In Render **Environment** settings, fill: `MONGO_URI`, `MONGO_DB_NAME`, `ADMIN_SECRET`, `ADMIN_SESSION_SECRET`, `MASTER_LICENSE_KEY` (can be empty), `PAUSED_MESSAGE`, `DISCLAIMER_TEXT`, `PAYMENT_INSTRUCTIONS`, `PAYMENT_URL` (can be empty).

Notes:
- You can use MongoDB Atlas (free tier) for persistence.
- `DISCLAIMER_VERSION` can be bumped to force re-acceptance.
