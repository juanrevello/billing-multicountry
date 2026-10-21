# Billing Multi-Country

REST service that issues invoices in different countries. It picks the provider for the
request's `country_code`, adapts the payload to that provider's API, calls it with timeouts and
retries, and stores an audit trail of every call. A client retry never bills twice, and a new
country is added without touching the core.

**Stack:** Python 3.14, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + asyncpg, httpx, tenacity,
PostgreSQL 16, uv.

## Quick start

Requires Docker. No `.env` or manual setup is needed; the tables are created on startup.

```bash
docker compose up --build
```

| Service | URL | What it is |
|---|---|---|
| `app` | http://localhost:8000/docs | The billing API (Swagger UI) |
| `mock-providers` | http://localhost:8001/docs | Simulated AR and BR providers |
| `db` | `localhost:5432` (db `invoicing`, user `test_user` / `test_user_password`) | PostgreSQL |

Issue an invoice (`Idempotency-Key` is required and must be a UUID):

```bash
curl -s -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d '{"entity_id":"20-12345678-9","amount":1500.00,"currency":"ARS","country_code":"AR"}' \
  localhost:8000/invoices
```

```json
{
  "invoice_id": "01a15a10-6c1e-7a3b-9f0e-2b6c1d4e5f70",
  "status": "issued",
  "provider_used": "arca",
  "external_reference": "71234567890123"
}
```

`GET /invoices/<invoice_id>` returns the invoice with its audit trail, and `GET /providers` the
supported countries.

The mock picks a random outcome per issue call (80% success, 10% `500`, 5% timeout, 5% rejection;
`MOCK_*_WEIGHT`), and an `X-Mock-Scenario` header (`success`, `500`, `timeout`, `reject`) forces
one. `GET localhost:8001/ar/vouchers` and `GET localhost:8001/br/nfe` list what each provider
issued.

## API

The spec, with every response code, is in [`openapi.json`](openapi.json) and at `/docs`. Errors
are [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) Problem Details
(`application/problem+json`); when the invoice exists they include `invoice_id` and
`invoice_status`.

## Architecture

```mermaid
flowchart LR
    client([Client]) -->|HTTP| api

    subgraph app [app]
        api["api/<br/>routes, schemas,<br/>Problem Details"]
        service["application/<br/>InvoiceService"]
        domain["domain/<br/>provider contract,<br/>registry, statuses"]
        repo["infra/db/<br/>models, repository"]
        retry["infra/retry<br/>tenacity"]
        adapters["infra/providers/<br/>ArgentinaProvider,<br/>BrazilProvider"]
        http["infra/http<br/>httpx, error mapping"]

        api --> service
        service --> domain
        service --> repo
        service --> retry
        adapters -. implement .-> domain
        adapters --> http
        service -->|"get_provider(country)"| adapters
    end

    repo --> db[(PostgreSQL)]
    http -->|"/ar/vouchers, /br/nfe"| mock["mock-providers"]
```

The core never imports a concrete adapter: on startup the registry imports every module in
`infra/providers/`, where each adapter registers itself with `@register`.

Each database step is its own short transaction, so no connection is held while waiting for the
provider:

```mermaid
sequenceDiagram
    participant C as Client
    participant S as InvoiceService
    participant DB as PostgreSQL
    participant P as Provider

    C->>S: POST /invoices (Idempotency-Key)
    S->>DB: find invoice by key
    alt new key
        S->>DB: INSERT invoice (pending)
    else same key, other body
        S-->>C: 409
    else already issued
        S-->>C: 200 replay
    else pending or failed
        S->>DB: INSERT attempt (lookup, in_flight)
        S->>P: lookup(invoice_id)
        S->>DB: UPDATE attempt (outcome, response, duration)
        Note over S,P: found → issued, without calling issue again
    end
    loop each attempt, with backoff on 5xx / timeout
        S->>DB: INSERT attempt (issue, in_flight, payload)
        S->>P: issue(payload with our invoice_id)
        S->>DB: UPDATE attempt (outcome, response, duration)
    end
    S->>DB: UPDATE invoice (issued / pending / failed)
    S-->>C: 201 / 202 / 422 / 502
```

## Adding a new provider or country

1. Create `src/billing/infra/providers/<country>.py` with a class that extends
   `HttpInvoiceProvider`, is decorated with `@register`, and declares `country_code`, `name` and
   `supported_currencies` ([`argentina.py`](src/billing/infra/providers/argentina.py) is a
   reference).
2. Implement the contract:
   - `build_payload(invoice)`: the provider's format, with our `invoice_id` as its reference so it
     can deduplicate.
   - `issue(payload)`: call the provider and return a `ProviderResult` with its reference. Raise
     `ProviderRejectedError` on a 4xx; `request()` already turns timeouts, connection errors and
     5xx into retryable errors.
   - `lookup(invoice_id)`: the provider's record for our reference, or `None`.
3. Set `PROVIDER_<COUNTRY>_BASE_URL` and, if needed, its timeout and retries.

## Configuration

Environment variables, or an optional `.env` (see [`.env.example`](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://test_user:test_user_password@localhost:5432/invoicing` | Database |
| `PROVIDER_BASE_URL` | `http://localhost:8001` | `/<country>` is appended unless `PROVIDER_<COUNTRY>_BASE_URL` is set |
| `PROVIDER_TIMEOUT_SECONDS` | `5` | Timeout per HTTP call |
| `PROVIDER_MAX_RETRIES` | `3` | Retries after the first attempt |
| `PROVIDER_BACKOFF_BASE_SECONDS` | `0.5` | Base of the exponential backoff (plus jitter) |
| `PROVIDER_<COUNTRY>_*` | — | Per-country override of the four above, e.g. `PROVIDER_AR_TIMEOUT_SECONDS=3` |
| `MOCK_*_WEIGHT`, `MOCK_TIMEOUT_SECONDS` | `80/10/5/5`, `30` | Mock only: outcome weights and how long a timeout hangs |

## Double billing and audit

A timeout does not mean the invoice was not issued, so it stays `pending`. Our `invoice_id` is the
provider's reference, and the provider deduplicates by it. Retrying a `pending` or `failed` invoice
with the same `Idempotency-Key` first calls `lookup`: if the provider has it, it is marked `issued`
without issuing again. This covers **confirmation without persistence**: the provider issues the
invoice but our service fails before saving it.

Each provider call is an `invoice_attempts` row, written before the call (`in_flight`, with the
payload) and completed after it, so it is recorded even if the process dies mid-call.

## Tests

Integration tests run the app against PostgreSQL, with the providers mocked at the HTTP level
([respx](https://lundberg.github.io/respx/)). They use an in-memory database (`db-test`, port
5433), never the development one.

```bash
docker compose up -d --wait db-test
```

```bash
uv run pytest
```

They cover the successful flow for AR and BR, provider failures (5xx then success, 5xx exhausted,
timeout, rejection), idempotency (replay, conflicting body, confirmation without persistence,
resuming a failed invoice, concurrent creation), input validation and `GET /providers`.

## Local development

With [uv](https://docs.astral.sh/uv/), run the database in Docker and the API and mock locally:

```bash
uv sync
```

```bash
docker compose up -d --wait db
```

```bash
uv run uvicorn billing.main:app --reload
```

```bash
uv run uvicorn mock_providers.main:app --port 8001 --reload
```

Lint with `uv run ruff check .` and `uv run ruff format .`. After changing the API, regenerate the
spec:

```bash
uv run python -c "import json; from billing.main import app; print(json.dumps(app.openapi(), indent=2))" > openapi.json
```

## Technical decisions

| Topic | Chosen | Discarded, and why |
|---|---|---|
| Stack | FastAPI + Pydantic v2, async SQLAlchemy + asyncpg, PostgreSQL, chosen for simplicity: I had no prior experience building APIs in Python | — |
| Schema | `create_all` on startup | Schema versioning: not needed for this challenge |
| Layers | By layer in one package (`api` / `application` / `domain` / `infra`); ports and adapters only for providers | By feature: there is a single context. A repository port: it would need domain models duplicating the ORM ones |
| Idempotency | Required `Idempotency-Key` (UUID). A different body with the same key is a `409`, checked against the stored invoice | Deduplicating by body: two legitimate identical invoices would collide |
| Provider 4xx | `failed` + `422` with the provider's reason, not retried | `502`: the data is wrong, not the provider. Retrying `429` (rate limit): not required, so it is reported as a rejection |
| Resilience | Timeout per provider; tenacity retries with exponential backoff and jitter on 5xx, timeout and connection errors, in the use case so each attempt is audited | Retries inside the HTTP client: one audited call for many attempts |
| Provider call | Synchronous within the request | Queue + worker + webhook: worth it for slow providers, not here |
| Transactions | One short transaction per repository method, so the `pending` invoice and the `in_flight` attempt are committed before each provider call | A session per request (`Depends(get_session)`) committed at the end: a crash after the provider issues loses both. Committing mid-request works too, but relies on never missing a commit |
| Audit | One `invoice_attempts` row per call, written before and after it | One row at the end: no trace if the process dies mid-call. Error columns on the invoice: they repeat the last attempt |
| Errors | RFC 9457 Problem Details | Ad hoc formats per endpoint |
| Tests | pytest + respx against PostgreSQL in memory (`db-test`) | SQLite: behaves differently from PostgreSQL |

**Out of scope:** authentication; background reconciliation of `pending` invoices (it happens when
the client retries); serializing concurrent retries of the same invoice (they are safe anyway: the
provider deduplicates and an `issued` invoice is never downgraded).
