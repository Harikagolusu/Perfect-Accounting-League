# TallyPrime Read-Only Integration POC

This POC proves that the PAL backend can reach TallyPrime's XML-over-HTTP
server, read the loaded company, and read its ledgers. It does **not** create,
edit, map, import or post vouchers.

## 1. Configuration

Add these values to `backend/.env` (or set them in the process environment):

```env
TALLY_BASE_URL=http://localhost:9000
TALLY_TIMEOUT_SECONDS=10
```

`TALLY_BASE_URL` is read only from server-side configuration. No API caller
can supply a Tally URL. `TALLY_TIMEOUT_SECONDS` must be greater than `0` and no
more than `60`.

## 2. Start TallyPrime

1. Open the company to be read.
2. Configure TallyPrime as a server:
   - **TallyPrime acts as:** Server
   - **Port:** 9000
   - **Enable ODBC:** Yes
3. Keep TallyPrime running while PAL is tested.

## 3. Verify TallyPrime directly

Open:

```text
http://localhost:9000
```

Expected response:

```xml
<RESPONSE>TallyPrime Server is Running</RESPONSE>
```

## 4. Start the PAL backend

From the `backend` directory:

```sh
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8082
```

Swagger UI: `http://127.0.0.1:8082/docs`

## 5. API calls

### Connection status

```http
GET /api/v1/tally/status
```

The endpoint makes a real company-list request to TallyPrime. A disconnected
TallyPrime is reported as HTTP 200 with `connected: false`.

```json
{
  "connected": true,
  "url": "http://localhost:9000",
  "message": "TallyPrime is reachable"
}
```

### Companies

```http
GET /api/v1/tally/companies
```

```json
{
  "success": true,
  "companies": [
    { "name": "PAL Test Company" }
  ],
  "message": null
}
```

If no company is loaded:

```json
{
  "success": true,
  "companies": [],
  "message": "No company is loaded in TallyPrime."
}
```

### Sync companies into PAL

```http
POST /api/v1/tally/companies/sync
```

Reads the TallyPrime company collection and upserts each company into PAL's
company master. Matching is case-insensitive, and existing companies are never
hard-deleted. A company archived locally (`INACTIVE`) is restored to `ACTIVE`
on the next sync if it still exists in Tally, because Tally is the source of
truth. The **Sync from Tally** button on the Companies page calls this endpoint.

```json
{
  "success": true,
  "companies_fetched": 1,
  "companies_created": 0,
  "companies_updated": 1,
  "companies_unchanged": 0,
  "error_message": null
}
```

### Ledgers

```http
GET /api/v1/tally/ledgers
```

When no company is supplied, PAL uses the first company returned by Tally.
Use the optional query parameter to select a company explicitly:

```http
GET /api/v1/tally/ledgers?company=PAL%20Test%20Company
```

```json
{
  "success": true,
  "company": "PAL Test Company",
  "ledgers": [
    { "name": "Cash", "parent": "Cash-in-Hand" },
    { "name": "HDFC Bank", "parent": "Capital Account" }
  ],
  "message": null
}
```

## 6. XML requests used

The exact requests are isolated in
`backend/app/integrations/tally/xml.py`:

- `build_company_collection_request()` — `Export` / `Collection` /
  `List of Companies`, type `Company`.
- `build_ledger_collection_request(company)` — `Export` / `Collection` /
  `Ledgers`, type `Ledger`, fetching `Name` and `Parent`, optionally scoped
  with `SVCURRENTCOMPANY`.

Responses are parsed defensively in
`backend/app/integrations/tally/parser.py`. The parser handles Tally's
non-XML-1.0 character references such as `&#4; Primary` and UTF-16 bodies.

## 7. Tests

Normal tests mock HTTP and never need TallyPrime:

```sh
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_tally.py -q
```

Optional live smoke test, with TallyPrime running:

```powershell
$env:TALLY_LIVE_TEST="1"
.\.venv\Scripts\python.exe -m pytest tests/test_tally_live.py -q
```

## 8. Troubleshooting

| Symptom | Check |
| --- | --- |
| `Unable to connect to TallyPrime` | Is TallyPrime open and running as a server? |
| Port 9000 unavailable | Close the other process, or change `TALLY_BASE_URL` and Tally's port together. |
| Wrong URL | `TALLY_BASE_URL` must be an absolute `http://` or `https://` URL with no credentials, query or fragment. |
| No company loaded | Open the company in TallyPrime, then call `/companies` again. |
| XML parsing failure | Check that TallyPrime is returning an `ENVELOPE` response. The API returns a safe message; Tally's error text is written to the backend log at warning level, never to the caller. |
| Timeout | Increase `TALLY_TIMEOUT_SECONDS` up to 60, or check TallyPrime responsiveness. |

## 9. Scope and next step

Implemented:

- read-only connectivity check;
- company read;
- ledger read;
- safe error mapping and timeouts;
- mocked unit/endpoint tests plus an opt-in live smoke test.

Not implemented:

- voucher creation, import or posting;
- ledger creation or PAL-to-Tally ledger mapping;
- Zoho;
- MCP;
- persistence of Tally configuration or read results;
- authentication/RBAC.

The next step for the first voucher-posting POC is a separate write workflow:
build a Tally `Import` / `Data` / `Vouchers` XML request for one approved
voucher, add an explicit idempotency key and a dry-run mode, capture Tally's
per-voucher import result, and only then allow PAL's existing
`AWAITING_IMPORT_CONFIRMATION` -> `POSTED` verification transition with an
external reference.
