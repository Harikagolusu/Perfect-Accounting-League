# PAL — Setup & User Guide

PAL (Perfect Accounting League) automates invoice and bank-statement processing,
ledger mapping, and posting to Tally. This guide takes you from a fresh clone to
a running app configured with **your own DeepSeek API key**.

Repository: <https://github.com/Harikagolusu/Perfect-Accounting-League.git>

---

## 1. Prerequisites

Install these first:

- **Node.js** 18 or newer (and npm)
- **Python** 3.12 or newer
- **PostgreSQL** 14 or newer (running locally)
- A **DeepSeek API key** — create one at <https://platform.deepseek.com/api_keys>

---

## 2. Clone the repository

```sh
git clone https://github.com/Harikagolusu/Perfect-Accounting-League.git
cd Perfect-Accounting-League
```

The frontend lives at the repository root; the backend is in `backend/`.

---

## 3. Backend (FastAPI)

All commands run from the `backend/` directory:

```sh
cd backend
```

### 3a. Create and activate a virtual environment

```sh
python -m venv .venv
```

- Windows: `.venv\Scripts\activate`
- macOS / Linux: `source .venv/bin/activate`

### 3b. Install dependencies

```sh
pip install -r requirements.txt
```

### 3c. Configure environment (including your DeepSeek key)

Copy the template:

- Windows: `copy .env.example .env`
- macOS / Linux: `cp .env.example .env`

Open `.env` and set **your own** DeepSeek key:

```
DEEPSEEK_API_KEY=sk-your-key-here
```

`.env` is git-ignored, so your key is never committed. The defaults already use
the right models (`DEEPSEEK_MODEL=deepseek-chat` for extraction,
`DEEPSEEK_REASONER_MODEL=deepseek-reasoner` for ledger matching). Change
`DATABASE_URL` if your PostgreSQL credentials differ from
`postgres:postgres@localhost:5432`.

### 3d. Create the database and run migrations

```sh
createdb pal_db
alembic upgrade head
```

### 3e. Start the backend

```sh
uvicorn app.main:app --reload
```

It serves on <http://localhost:8000>. Check <http://localhost:8000/health> (should
return `{"status":"ok",...}`) and the API docs at <http://localhost:8000/docs>.

Confirm your key works:

```sh
curl -X POST http://localhost:8000/api/v1/ai/test -H "Content-Type: application/json" -d "{\"prompt\":\"say hi\"}"
```

A `200` with a `text` field means DeepSeek is configured correctly.

---

## 4. Frontend (React / TanStack)

From the **repository root** (a second terminal):

```sh
npm install
npm run dev
```

The app serves on <http://localhost:8080>.

The frontend talks to the backend at `http://localhost:8000` by default. If your
backend runs elsewhere, create a `.env.local` file in the repository root:

```
VITE_API_BASE_URL=http://localhost:8000
```

---

## 5. Using the platform

Work through the sidebar in this order:

1. **Companies & Clients** — create a company. Everything else is scoped to it.
2. **Document Inbox** — upload invoices and bank statements (PDF/JPG/PNG/XLSX).
   Each upload gets a batch code (`Batch-N` for invoices, `Bank-N` for bank
   statements) so you can filter one upload later. Extraction runs automatically
   on upload using your DeepSeek key.
3. **Invoice Processing** — review each extracted invoice. The expense ledger is
   suggested from approved history or, as a last resort, by AI (flagged for your
   confirmation). Approve to build the accounting voucher.
4. **Bank** — review extracted statement transactions; AI suggests the
   counterparty ledger for each. Filter by bank batch.
5. **Vouchers** — approve a voucher, then post it to Tally. Approving a purchase
   also remembers the vendor's creditor ledger, so the next invoice for that
   vendor auto-fills the party line.
6. **Posted to Tally** — every voucher posted to Tally appears here, with its
   ledger entries, amount, posted date, and Tally reference.

### Tally integration (optional)

TallyPrime must be running as a server. Set `TALLY_BASE_URL` in `backend/.env`
(default `http://localhost:9000`). Without it, you can still export vouchers and
work through every step except the live Tally post.

---

## Notes

- Your DeepSeek key stays on your machine (in `backend/.env`); it is never sent
  anywhere except DeepSeek's API.
- No sample data ships with PAL. Modules show empty states until you add records.
- Scanned (image-only) or password-protected PDFs may fail extraction — use a
  digital PDF or add those transactions by hand.
