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

**Step 2.1 – Companies & Clients finalized and wired to the frontend.**

What exists:

| Piece | Status |
| --- | --- |
| FastAPI app, `/health`, Swagger at `/docs` | Working |
| Environment-based config (pydantic-settings) | Working |
| SQLAlchemy 2.x engine, session factory, `Base`, `get_db` | Working |
| Alembic wired to `Base.metadata` (head `0003_company_status`) | Working |
| **Company entity + full CRUD API** | **Working** |
| CORS for the frontend dev server | Working |
| pytest suite | Passing |
| Vendors, invoices, bank statements, GST, TDS, reconciliation | **Not started** |
| Tally / Zoho, AI, OCR, authentication | **Not started** |

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
│   ├── models/
│   │   └── company.py           # Company ORM model + CompanyStatus
│   ├── schemas/
│   │   └── company.py           # CompanyCreate / Update / Response
│   ├── api/
│   │   └── v1/
│   │       ├── router.py        # aggregate router
│   │       └── companies/
│   │           └── router.py    # companies endpoints
│   └── services/
│       └── company.py           # all Company DB access lives here
├── alembic/                     # migrations
├── tests/                       # pytest suite
├── .env.example
├── alembic.ini
└── requirements.txt
```

The layering rule: **routers → services → models**. Routers stay thin and hold
no queries; every database call lives in `app/services/`.

## Data model

### `companies`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` | Primary key, generated in Python |
| `name` | `varchar(255)` | Required, display name |
| `legal_name` | `varchar(255)` | Nullable |
| `gstin` | `varchar(15)` | Nullable, uppercased on write |
| `pan` | `varchar(10)` | Nullable, uppercased on write |
| `email` | `varchar(255)` | Nullable |
| `phone` | `varchar(32)` | Nullable |
| `address` | `text` | Nullable |
| `financial_year` | `varchar(32)` | Nullable |
| `status` | `company_status` (enum) | `ACTIVE` / `INACTIVE` / `ONBOARDING`, defaults to `ACTIVE` |
| `created_at` | `timestamptz` | Defaults to `now()` |
| `updated_at` | `timestamptz` | Defaults to `now()`, bumped on every UPDATE |

Company is the root scope for every company-owned record to come (vendors,
invoices, bank statements, GST, TDS, reconciliation). Those relationships are
deliberately not declared yet.

#### Why `status` replaced `is_active`

The original boolean could not represent **Onboarding**, which the Companies &
Clients UI uses as a real lifecycle state. Keeping both a boolean and a status
would also have left two columns able to disagree about the same fact, so
`status` is now the single source of truth and **soft delete is
`status = 'INACTIVE'`** rather than a separate flag.

`ACTIVATE` and `ONBOARDING` are both live states. Only `INACTIVE` is filtered
out of default listings.

Validation is format-only: `gstin`, `pan` and `email` are checked against the
same patterns the frontend uses in `src/lib/format.ts`, so both sides accept
and reject identical values. GSTIN checksum verification is not implemented.

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
createdb pal_test_db   # used by the test suite
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

Tests read `TEST_DATABASE_URL` (default `.../pal_test_db`) and never touch the
dev database.

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

Confirm the model and the database agree:

```sh
alembic check
```

Always review the generated file before committing it.

Migration chain:

| Revision | Purpose |
| --- | --- |
| `0001_initial_schema` | Empty baseline |
| `0002_add_companies` | Creates `companies` |
| `0003_company_status` | Replaces `is_active` with the `status` enum |

`0003` backfills existing rows from the old flag: `is_active = false` becomes
`INACTIVE`, everything else becomes `ACTIVE`, so previously soft-deleted
companies stay out of default listings.

## Starting the API

```sh
uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/health> or the docs at
<http://127.0.0.1:8000/docs>.

## Running tests

```sh
pytest -v
```

Tests run against a real PostgreSQL database and override `get_db`, so no
running server is required.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| GET | `/api/v1/health` | Same handler, under the versioned prefix |
| POST | `/api/v1/companies` | Create a company |
| GET | `/api/v1/companies` | List companies (`?include_inactive=`) |
| GET | `/api/v1/companies/{company_id}` | Fetch one company |
| PATCH | `/api/v1/companies/{company_id}` | Update supplied fields only |
| DELETE | `/api/v1/companies/{company_id}` | Soft delete (`status=INACTIVE`) |

`GET /health` does not touch the database, so it stays green while PostgreSQL
is down and can be used as a container/orchestrator readiness check.

Swagger UI: <http://127.0.0.1:8000/docs> · ReDoc: <http://127.0.0.1:8000/redoc>

### Company behaviour worth knowing

- **Create** — only `name` is required. Status defaults to `ACTIVE` when not
  supplied. GSTIN and PAN are uppercased and whitespace-trimmed on write;
  blank strings become `null`. Unknown fields are rejected.
- **List** — returns `ACTIVE` and `ONBOARDING` newest-first. Pass
  `?include_inactive=true` to include `INACTIVE` rows.
- **Patch** — only the fields present in the body are applied, so
  `{"phone": null}` clears the phone without touching anything else.
  `updated_at` is bumped automatically. Status can be changed here too.
- **Delete** — never removes the row. Sets `status = INACTIVE`, bumps
  `updated_at`, and returns the updated company so the record stays available
  for audit.

## Frontend integration

The frontend reads this API through `src/lib/api/` and `src/lib/queries/`
(see the root `README.md`). Point it at the backend with:

```sh
# .env.local in the repository root
VITE_API_BASE_URL=http://localhost:8000
```

## Planned modules

`app/api/v1/router.py` reserves the prefix for: `/documents`, `/invoices`,
`/bank-statements`, `/vendors`, `/tally`, `/zoho`, `/gst`, `/tds`,
`/reconciliation`. Each will be its own package under `app/api/v1/` and
registered in the aggregate router when implemented.