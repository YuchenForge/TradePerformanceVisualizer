# TradePerformanceVisualizer

A full-stack application for visualizing trade performance.

## Components

| Component       | Path             | Port | Deployed | Description                                                      |
| --------------- | ---------------- | ---- | -------- | ---------------------------------------------------------------- |
| Backend (Flask) | `backend/`       | 5001 | yes      | REST API for the dashboard + the internal explorer endpoints.   |
| Dashboard       | `frontend/`      | 3000 | yes      | User-facing React app (session auth).                           |
| Data explorer   | `data-platform/` | 3001 | no (local-only) | Internal, creators-only React explorer behind HTTP Basic Auth. |

The dashboard and explorer both talk to the single Flask backend on `:5001`.
The explorer's API (`/api/explorer/*`, `/api/db/*`) lives inside the backend,
gated by HTTP Basic Auth.

## Prerequisites

- Python 3.12+
- Node.js and npm

## Setup

### Backend

```bash
cd backend
python3 -m venv venv            # create the virtual environment
source venv/bin/activate        # macOS/Linux  (venv\Scripts\activate on Windows)
pip install -r requirements.txt
```

Secrets and runtime data are **not** committed. On first run the backend
auto-creates the files it needs; templates are provided:

- `backend/users.example.json` → copy to `backend/users.json`
- Account cache files are created automatically after brokerage setup.

Set the backend environment before starting any services. Copy
`backend/.env.example` to `backend/.env`, fill in the values, then load them
in the shell used to start the app:

```bash
set -a
source backend/.env
set +a
```

The app does not load `.env` itself. Set `SECRET_KEY` to a stable random value,
configure `SNAPTRADE_CLIENT_ID` and `SNAPTRADE_CONSUMER_KEY`, and set
`EXPLORER_PASS` to enable the internal explorer. No shared password is enabled
by default. Previously embedded brokerage credentials must be rotated; removing
them from source does not remove them from Git history.

## Running the Application

### Dashboard + backend (quick start)

```bash
cd frontend
npm install        # first time only
npm run dev        # starts the Flask backend (:5001) then the dashboard (:3000)
```

### Running services separately

**Backend (Flask API):**

```bash
cd backend
source venv/bin/activate
python app.py      # http://localhost:5001
```

**Dashboard (React):**

```bash
cd frontend
npm install        # first time only
npm start          # http://localhost:3000
```

**Data explorer (React, local-only):**

```bash
cd data-platform
npm install        # first time only
cp .env.example .env   # first time only (sets PORT=3001)
npm start          # http://localhost:3001 — requires the backend on :5001
```

## Database and verification

The API and importer share `DATABASE_URL`. The default is
`backend/database/portfolio.db` (SQLite); PostgreSQL uses
`postgresql+psycopg://user:password@localhost/database`. Tables are created on
startup; existing schema migrations are not performed automatically.

From `backend/`, export current data and import it:

```bash
python -m database.snaptrade_export_all
python -m database.db_sync
```

Imports accept both `users[].accounts[]` and legacy `accounts[]` exports. Each
import is a transaction: a failed write rolls back, repeated imports replace
snapshots for the supplied accounts, and omitted accounts are preserved.
Back up the database before importing real data. The repository already tracks
`backend/database/portfolio.db`; new ignore rules do not untrack that existing file.

Run isolated backend tests from the repository root:

```bash
PYTHONPATH=backend backend/venv/bin/python -m unittest discover -s backend/tests -v
```

Build each frontend with `npm ci` followed by `CI=true npm run build` in its
respective directory. Backend tests use temporary SQLite data and mocked external
services. They do not verify live brokerage access or a PostgreSQL server.
The optional legacy Dash viewer requires `dash` and `pandas` installed separately.

## API Endpoints

- `GET /api/health` checks HTTP and database connectivity; returns 503 on database failure.
- `/api/auth/register`, `/api/auth/complete-setup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/status`, `/api/auth/profile`
- `GET /api/portfolio/summary`, `/api/portfolio/stocks`, `/api/portfolio/history`
- `GET /api/transactions`, `/api/benchmark/history`
- `GET /api/watchlist`, `/api/watchlist/quotes` (demo prices, labeled in the dashboard)

Internal endpoints require the explorer's shared HTTP Basic credentials:
`GET /api/explorer/data`, `/api/explorer/meta`, `/api/explorer/refresh`,
`POST /api/explorer/refresh`, `GET /api/db/accounts`, `/api/db/positions`,
`/api/db/orders`, `/api/db/activities`, and `POST /api/db/sync`.
The refresh status endpoint reports running, complete, or failed. Refresh locking
and status are process-local; use a single backend process for this local workflow.
