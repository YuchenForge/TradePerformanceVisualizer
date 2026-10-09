# SnapTrade Data Platform (internal)

A **standalone** data explorer for the raw SnapTrade export JSON and synced
database tables. This is a separate app from the user-facing analytical
dashboard (`frontend/`) — it's meant for the creators only, lives on its own
URL, and is **not** linked from the dashboard.

It renders `SnapTradeExplorer` (accounts / positions / orders / transactions)
and talks to the shared Flask backend's `/api/explorer/*` and `/api/db/*`
endpoints.

## Running locally

The backend must be running first (from `../backend`, on port 5001):

```bash
cd ../backend
source venv/bin/activate
python app.py
```

Then start this app (defaults to port **3001** via `.env`):

```bash
cd data-platform
npm ci             # first time only
cp .env.example .env
npm start
```

Open http://localhost:3001

Port layout while everything runs:

| App                        | Port | Path        |
|----------------------------|------|-------------|
| Flask backend              | 5001 | `backend/`  |
| User-facing dashboard      | 3000 | `frontend/` |
| **Data platform (this)**   | 3001 | `data-platform/` |

API calls to `/api/*` are proxied to `http://localhost:5001` (see `proxy` in
`package.json`).

## Authentication

The data this app reads is gated by **HTTP Basic Auth** on the backend
(`/api/explorer/*` and `/api/db/*`). Enter the shared username/password in the explorer sign-in form. Credentials
stay in memory until the page is reloaded. There is no per-user login.

Set the credentials via environment variables when starting the backend:

```bash
EXPLORER_USER=admin EXPLORER_PASS='your-strong-password' python app.py
```

The explorer is disabled until `EXPLORER_PASS` is set. These endpoints serve
brokerage data for all users. Use HTTPS outside localhost.

Refresh waits for the export and database import to finish and displays failures.

## Notes

- `SnapTradeExplorer.jsx` here is a copy of the component that previously lived
  in `frontend/src/`. The dashboard no longer renders it — the "Trade Explorer"
  tab was removed from `frontend/src/App.js`.
- Tailwind is loaded via CDN in `public/index.html`, matching the dashboard.
