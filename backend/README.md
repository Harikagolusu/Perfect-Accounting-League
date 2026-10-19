# PAL Backend

FastAPI foundation for **PAL – Perfect Accounting League**, the accounting
automation platform.

## What PAL is

PAL automates accounting operations that are currently manual and repetitive:
processing invoices and bank statements, maintaining the vendor master,
producing GST and TDS workings, and reconciling against GSTR-2B. It integrates
outward with **Tally** and **Zoho Books** so that whatever it produces can flow
back into the tools accountants already use.

The frontend (TanStack Start / React) lives at the **repository root** and is
untouched by this backend, which sits alongside it in `backend/`.

## Current status

**Step 1 – Backend foundation. No business features are implemented yet.**

What exists:

| Piece | Status |
| --- | --- |
| FastAPI app, `/health`, Swagger at `/docs` | Working |
| Environment-based config (pydantic-settings) | Working |
| SQLAlchemy 2.x engine, session factory, `Base`, `get_db` | Working |
| Alembic wired to `Base.metadata`, baseline revision `0001` | Working |
| CORS for the frontend dev server | Working |
| pytest smoke test | Passing |
| Business models / tables | **None yet, by design** |
| Invoice extraction, Tally, Zoho, GST, TDS, reconciliation | **Not started** |

Dependencies are added only when the feature that needs them is implemented.

## Tech stack

- Python 3.12+ (developed on 3.14)
- FastAPI + Uvicorn
- PostgreSQL + SQLAlchemy 2.x + Alembic
- Pydantic v2 + pydantic-settings
- psycopg (v3) driver
- pytest

## Project layout

```
backend/
├── app/
│   ├── main.py                  # FastAPI app factory + CORS
│   ├── core/
│   │   ├── config.py            # Settings (single source of truth)
│   │   └── database.py          # engine, SessionLocal, Base, get_db
│   ├── models/                  # ORM models (empty for now)
│   ├── schemas/                 # Pydantic request/response models
│   ├── api/
│   │   └── v1/
│   │       └── router.py        # aggregate router + /health
│   └── services/                # business logic (empty for now)
├── alembic/                     # migrations
├── tests/
├── .env.example
├── alembic.ini
└── requirements.txt
```

The layering rule: **routers → services → models**. Routers stay thin and hold
no business logic.

## Local setup

From the `backend/` directory:

```sh
python -m venv .venv
```

Activate it — Windows:

```sh
.venv\Scripts\activate
```

macOS / Linux:

```sh
source .venv/bin/activate
```

Install dependencies:

```sh
pip install -r requirements.txt
```

## PostgreSQL setup

Create the database, then point `DATABASE_URL` at it:

```sh
createdb pal_db
```

The default in `.env.example` assumes PostgreSQL on `localhost:5432` with the
standard `postgres` role. Change the credentials in your `.env` to match your
local install.

Nothing is hardcoded in the source — `app/core/config.py` reads
`DATABASE_URL` from the environment, and Alembic picks the same value up
automatically, so there is only one place to change it.

## Environment variables

Copy the template and edit it:

```sh
copy .env.example .env      # Windows
cp .env.example .env        # macOS / Linux
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `PAL Accounting Automation API` | Title shown in Swagger |
| `APP_ENV` | `development` | `development` / `production` |
| `DEBUG` | `true` | Debug mode and SQLAlchemy statement echo |
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/pal_db` | SQLAlchemy 2.x URL (psycopg driver) |
| `API_V1_PREFIX` | `/api/v1` | Prefix for versioned routes |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |

`.env` is git-ignored. Never commit real credentials.

## Running migrations

Apply everything:

```sh
alembic upgrade head
```

Roll back one step:

```sh
alembic downgrade -1
```

After adding models under `app/models/`, autogenerate a revision:

```sh
alembic revision --autogenerate -m "describe the change"
```

Always review the generated file before committing it.

## Starting the API

```sh
uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/health> or the docs at
<http://127.0.0.1:8000/docs>.

## Running tests

```sh
pytest
```

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| GET | `/api/v1/health` | Same handler, under the versioned prefix |

`/health` does not touch the database, so it stays green while PostgreSQL is
down and can be used as a container/orchestrator readiness check.

Swagger UI: <http://127.0.0.1:8000/docs> · ReDoc: <http://127.0.0.1:8000/redoc>

## Planned modules

`app/api/v1/router.py` reserves the prefix for: `/companies`, `/documents`,
`/invoices`, `/bank-statements`, `/vendors`, `/tally`, `/zoho`, `/gst`,
`/tds`, `/reconciliation`. Each will be its own package under
`app/api/v1/` and registered in the aggregate router when implemented.