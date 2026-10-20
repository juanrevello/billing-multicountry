from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from billing.api import health, invoices, providers
from billing.api.problems import install_problem_handlers, install_problem_schema
from billing.domain.registry import close_providers, discover
from billing.infra.db.models import Base
from billing.infra.db.session import engine

PROVIDER_ADAPTERS = "billing.infra.providers"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    discover(PROVIDER_ADAPTERS)
    yield
    await close_providers()
    await engine.dispose()


app = FastAPI(
    title="Billing Multi-Country",
    version="0.1.0",
    description=(
        "Issues invoices through a per-country provider, with idempotency (Idempotency-Key), "
        "retries and a full audit trail of every provider call."
    ),
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(providers.router)
app.include_router(invoices.router)
install_problem_handlers(app)
install_problem_schema(app)
