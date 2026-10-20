# Billing Multi-Country

REST service that issues invoices in different countries. It picks the provider for the
request's `country_code`, adapts the payload to that provider's API, calls it with timeouts and
retries, and stores an audit trail of every call. It is designed so that a client retry never
bills twice and a new country can be added without touching the core.

- **Stack:** Python 3.14, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + asyncpg, Alembic, httpx,
  tenacity, PostgreSQL 16, uv.
- **Providers:** two simulated providers (Argentina and Brazil) with different payload and
  response formats, served by the `mock-providers` service.

## Quick start

Requires Docker. No `.env` or manual setup is needed.

```bash
docker compose up --build
```

| Service | URL | What it is |
|---|---|---|
| `app` | http://localhost:8000/docs | The billing API (Swagger UI) |
| `mock-providers` | http://localhost:8001/docs | Simulated AR and BR providers |
| `db` | `localhost:5432` (db `invoicing`, user `test_user` / `test_user_password`) | PostgreSQL, persisted in the `pgdata` volume |

Migrations run automatically when the app starts.

### Try it

Issue an invoice. `Idempotency-Key` is required and must be a UUID:

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
  "external_reference": "71234567890123",
  "entity_id": "20-12345678-9",
  "amount": "1500.00",
  "currency": "ARS",
  "country_code": "AR",
  "error_code": null,
  "error_detail": null,
  "created_at": "2026-10-20T16:10:00.000000Z",
  "updated_at": "2026-10-20T16:10:00.050000Z"
}
```

Get the invoice with its audit trail (every provider call, with payload, response and timing):

```bash
curl -s localhost:8000/invoices/<invoice_id>
```

List the supported countries:

```bash
curl -s localhost:8000/providers
```

The mock picks a random outcome per call (80% success, 10% `500`, 5% timeout, 5% rejection, set
with `MOCK_*_WEIGHT` in `docker-compose.yml`), so repeated requests show the different paths. To
force one outcome, change those weights, or call the mock directly with an `X-Mock-Scenario`
header (`success`, `500`, `timeout`, `reject`):

```bash
curl -s -H 'Content-Type: application/json' -H 'X-Mock-Scenario: reject' \
  -d '{"external_id":"x","customer_id":"y","total":"1.00","currency_code":"PES"}' \
  localhost:8001/ar/vouchers
```

## API

| Endpoint | Case | Status |
|---|---|---|
| `POST /invoices` | Issued | `201` |
| | Replay of an already issued invoice (same key and body) | `200` |
| | Provider timed out: outcome unknown, invoice stays `pending` | `202` |
| | Invalid input, missing or non-UUID key, unsupported country or currency | `422` |
| | Rejected by the provider (`failed`, with the provider's reason) | `422` |
| | Same key with a different body, or two requests creating the same key at once | `409` |
| | Provider unavailable after all retries (`failed`) | `502` |
| `GET /invoices/{invoice_id}` | Invoice with its full audit trail | `200` / `404` |
| `GET /providers` | Countries, provider and currencies | `200` |

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) Problem Details document
(`application/problem+json`). When the invoice was created, the problem includes its
`invoice_id` and `invoice_status`:

```json
{
  "type": "/problems/provider-rejected",
  "title": "Invoice rejected by provider",
  "status": 422,
  "detail": "10016: Customer is not a valid taxpayer",
  "invoice_id": "01a15a10-6c1e-7a3b-9f0e-2b6c1d4e5f70",
  "invoice_status": "failed"
}
```

The full spec is in [`openapi.json`](openapi.json), and live at `/docs` and `/openapi.json`.

## Architecture

```mermaid
flowchart LR
    client([Client]) -->|HTTP| api

    subgraph app [app]
        api["api/<br/>routes, schemas,<br/>Problem Details"]
        service["application/<br/>InvoiceService<br/>(issue flow)"]
        domain["domain/<br/>InvoiceProvider contract,<br/>registry, errors, statuses"]
        repo["infra/db/<br/>repository, models,<br/>migrations"]
        retry["infra/retry<br/>tenacity"]
        adapters["infra/providers/<br/>ArgentinaProvider,<br/>BrazilProvider"]
        http["infra/http<br/>httpx client,<br/>error mapping"]

        api --> service
        service --> domain
        service --> repo
        service --> retry
        adapters -. implement .-> domain
        adapters --> http
        service -->|"get_provider(country)"| adapters
    end

    repo --> db[(PostgreSQL)]
    http -->|"/ar/vouchers, /br/nfe"| mock["mock-providers<br/>(AR and BR routers)"]
```

| Layer | Responsibility | Depends on |
|---|---|---|
| `api/` | HTTP surface: request/response schemas, status codes, Problem Details | `application`, `domain` |
| `application/` | The issue use case: create, call the provider with retries, audit each attempt, settle the status | `domain`, `infra` |
| `domain/` | Business concepts with no framework code: provider contract, typed provider errors, which errors are retryable, statuses, country registry | Standard library only |
| `infra/` | Technical implementations: database, HTTP client, retry library, provider adapters | `domain` |

The core never imports a concrete adapter. At startup the registry imports every module in
`billing/infra/providers/`; each adapter registers itself with `@register`, and the service asks
for one with `get_provider(country_code)`.

### Issue flow

Each database step is its own short transaction. No connection is held while waiting for the
provider.

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

### Project layout

```text
src/
  billing/
    main.py            app wiring: migrations, provider discovery, routers
    config.py          settings and per-provider settings
    api/               invoices, providers, health, problems (RFC 9457)
    application/       invoicing.py (issue use case)
    domain/            invoice.py, provider.py (contract), registry.py
    infra/
      db/              models, repository, session, migrations/
      http.py          shared HTTP plumbing for adapters
      retry.py         retry policy (tenacity)
      providers/       argentina.py, brazil.py, uruguay.py (commented example)
  mock_providers/      simulated AR and BR provider APIs
tests/                 integration tests
```

## Adding a new provider or country

No change to the core is needed: one module and, optionally, its environment variables.

1. Create `src/billing/infra/providers/<country>.py` with a class that extends
   `HttpInvoiceProvider`, is decorated with `@register`, and declares `country_code`, `name` and
   `supported_currencies`.
2. Implement the three methods of the contract:
   - `build_payload(invoice)`: translate the invoice to the provider's format. Send our
     `invoice_id` as the provider's reference, so it can deduplicate.
   - `issue(invoice)`: call the provider and return a `ProviderResult` with the provider's
     reference. Raise `ProviderRejectedError` for a 4xx; `HttpInvoiceProvider.request()` already
     turns timeouts, connection errors and 5xx into retryable errors.
   - `lookup(invoice_id)`: return the provider's record for our reference, or `None` if it does
     not exist. It is used to reconcile before issuing again.
3. Point it to the provider with `PROVIDER_<COUNTRY>_BASE_URL` and, if needed, override its timeout
   and retries (see [Configuration](#configuration)).

[`uruguay.py`](src/billing/infra/providers/uruguay.py) is a complete, commented-out example:
uncomment it and `UY` appears in `GET /providers`.

## Configuration

Settings come from environment variables (and an optional `.env`, see
[`.env.example`](.env.example)). Every provider uses the global defaults unless it overrides them
with `PROVIDER_<COUNTRY>_<SETTING>`.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://test_user:test_user_password@localhost:5432/invoicing` | Database |
| `LOG_LEVEL` | `INFO` | JSON logs to stdout |
| `PROVIDER_BASE_URL` | `http://localhost:8001` | Base URL; `/<country>` is appended unless `PROVIDER_<COUNTRY>_BASE_URL` is set |
| `PROVIDER_TIMEOUT_SECONDS` | `5` | Timeout per HTTP call |
| `PROVIDER_MAX_RETRIES` | `3` | Retries after the first attempt (5xx, timeout, connection error) |
| `PROVIDER_BACKOFF_BASE_SECONDS` | `0.5` | Base of the exponential backoff (plus random jitter) |
| `PROVIDER_<COUNTRY>_*` | — | Per-country override of any of the four above, e.g. `PROVIDER_AR_TIMEOUT_SECONDS=3` |
| `MOCK_SUCCESS_WEIGHT`, `MOCK_ERROR_WEIGHT`, `MOCK_TIMEOUT_WEIGHT`, `MOCK_REJECT_WEIGHT` | `80`, `10`, `5`, `5` | Mock only: relative weights of each random outcome |
| `MOCK_TIMEOUT_SECONDS` | `30` | Mock only: how long the timeout scenario hangs |

To see the settings each provider actually resolves (defaults plus overrides):

```bash
uv run python -c "from billing.config import provider_settings as c; print(c('AR')); print(c('BR'))"
```

## Failure handling and double billing

| Provider outcome | Retried | Invoice | HTTP |
|---|---|---|---|
| Success | — | `issued` | `201` |
| 5xx or connection error, retries exhausted | Yes | `failed` (`provider_unavailable`) | `502` |
| Timeout, retries exhausted | Yes | `pending` (`provider_timeout`) | `202` |
| 4xx rejection | No | `failed` (`provider_rejected`, with the reason) | `422` |

A timeout does not mean the invoice was not issued: the provider may have processed it and the
response got lost. That is why it stays `pending` instead of `failed`.

Double billing is prevented in two layers:

1. **Our API:** the `Idempotency-Key` identifies the invoice, and a hash of the body makes sure the
   key is not reused for a different invoice. Repeating a request returns the same invoice.
2. **Towards the provider:** our `invoice_id` goes in every payload as the provider's reference,
   and the provider deduplicates by it. When a `pending` or `failed` invoice is retried, the
   service first asks the provider (`lookup`). If the invoice exists, it is marked `issued`
   without issuing again; only if it does not exist is it issued.

This covers **confirmation without persistence**: the provider issues the invoice but our service
fails before saving the result. The invoice stays `pending`, the attempt stays recorded, and the
client's retry reconciles it via lookup with a single issuance.

## Audit trail

Every provider call is a row in `invoice_attempts`, written in two steps: an `in_flight` row with
the payload **before** the call, and an update with the response, outcome and duration **after**
it. If the process dies mid-call, the row still shows that the call was made.

`GET /invoices/{invoice_id}` returns, for each attempt: number, kind (`issue` or `lookup`),
outcome (`success`, `retryable_error`, `timeout`, `rejected`, `not_found`, `in_flight`), payload
sent, provider status and body, error, `started_at`, `finished_at` and `duration_ms`. The invoice
itself records the provider used, final status, provider reference and failure reason. Each step
is also logged as a JSON line with `invoice_id`, `provider` and `attempt`.

## Tests

Integration tests run the real app against a real PostgreSQL, with the providers mocked at the
HTTP level ([respx](https://lundberg.github.io/respx/)) so every scenario is deterministic.
They use an ephemeral database (`db-test`, in memory, port 5433), never the development one.

```bash
docker compose up -d --wait db-test
```

```bash
uv run pytest
```

They cover: the full successful flow for AR and BR (payload sent, provider reference, audit
trail); provider failures (5xx then success, 5xx exhausted, timeout, rejection without retry);
idempotency (replay, same key with another body, confirmation without persistence, resuming a
failed invoice, concurrent creation); input validation; and `GET /providers`.

Stop the test database when done (its data is discarded):

```bash
docker compose rm --stop --force db-test
```

## Local development

Requires [uv](https://docs.astral.sh/uv/). Run the database in Docker and the API and mock on
your machine, with auto-reload:

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

Optionally copy `.env.example` to `.env` to change settings. Lint and format with
`uv run ruff check .` and `uv run ruff format .`. Regenerate the exported spec after changing the
API:

```bash
uv run python -c "import json; from billing.main import app; print(json.dumps(app.openapi(), indent=2))" > openapi.json
```

## Technical decisions

| Topic | Chosen | Discarded, and why |
|---|---|---|
| Framework | FastAPI + Pydantic v2 | Django REST: heavier than this scope needs. Flask: no built-in validation or OpenAPI |
| Database access | SQLAlchemy 2 async + asyncpg | Sync driver in a threadpool: inconsistent with the async HTTP calls |
| Migrations | Alembic, applied at startup | `create_all`: no schema history |
| Idempotency | Required `Idempotency-Key` header (any UUID) plus a hash of the body | Body hash only: two legitimate identical invoices would collide. Requiring UUIDv7 for the key: most clients (`uuidgen`) generate v4 |
| IDs | UUIDv7 (`uuid.uuid7()`, standard library) for invoices and attempts | UUIDv4 primary keys: random inserts fragment the B-tree index |
| Same key, same body | Same invoice: `issued` → replay `200`; `pending`/`failed` → resumed, starting with a lookup | Stripe-style final `failed`: forces the client to create a new invoice and splits the history |
| Same key, other body | `409 Conflict` | Returning the previous invoice: hides a client bug |
| Concurrency on the same key | Out of scope (not required). Minimal guard: the `UNIQUE` key makes a concurrent creation return `409`; simultaneous retries are safe because the provider deduplicates and an `issued` invoice is never downgraded | A lease with `locked_until`: extra state and edge cases for an unrequested feature. `SELECT ... FOR UPDATE` during the HTTP call: holds connections and exhausts the pool |
| Idempotency towards the provider | Our `invoice_id` as the provider's reference, plus a lookup before issuing again | Trusting the provider's dedup alone: no way to reconcile a `pending` invoice |
| Reconciliation | When the client retries | Background job: extra infrastructure that this scope does not need |
| Timeout exhausted | `pending` + `202` (unknown outcome) | `failed` / `504`: would be false if the provider did issue it |
| 4xx from the provider | `failed` + `422` with the reason, no retry | `502`: the problem is the data, not availability |
| Provider call | Synchronous within the request, with async I/O | Queue + worker + webhook: worth it for long provider processes; not here |
| Resilience | Timeout per provider; retries with exponential backoff and jitter (tenacity) on 5xx, timeout and connection errors; never on 4xx | Circuit breaker: with a single instance and bounded retries it adds in-memory state and edge cases. httpx's own retries: only cover connection errors |
| Retry placement | In the use case, wrapping one adapter call per attempt | Inside the HTTP client: the service would see one call and could not audit each attempt |
| Configuration | Static environment variables (pydantic-settings), global defaults with per-country overrides | Configuration in the database: needs a table and an admin endpoint |
| Audit | `invoice_attempts`, one row per call written in two steps, plus JSON logs | One row at the end: no trace if the process dies mid-call. Separate request/response rows: twice the rows, and they must be joined |
| Errors | RFC 9457 Problem Details | Ad hoc formats per endpoint |
| Layers | `api` / `application` / `domain` / `infra`; `domain` has no framework code | Full hexagonal (ports for the repository and retry too): more indirection than this size justifies, so `application` uses `infra` directly |
| DB engine | Created once per process in `infra/db/session.py`, as in FastAPI's full-stack template; the repository opens a short transaction per operation | Session per request (`Depends` with `yield`): the transaction could stay open while waiting for the provider |
| Mock providers | One FastAPI service with a router per country, random outcomes by weight and `X-Mock-Scenario` to force one | json-server: no random outcomes without custom middleware. State is in memory, so restarting the mock forgets issued invoices |
| Tests | pytest + respx against PostgreSQL in an in-memory `db-test` | SQLite: behaves differently from PostgreSQL. Tests in a container: needs an extra image stage and a Makefile for little gain |
| OpenAPI | Generated by FastAPI, with a `ProblemDetails` schema for every error, exported to `openapi.json` | Hand-written spec: drifts from the code |

### Out of scope

- Concurrent requests with the same key beyond the `409` guard described above.
- Background reconciliation of `pending` invoices: it happens when the client retries.
- Authentication, multi-instance deployment and real provider integrations.
