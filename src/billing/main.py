import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from billing.api import health, invoices, providers
from billing.api.problems import install_problem_handlers, install_problem_schema
from billing.config import get_settings
from billing.domain.registry import close_providers, discover, provider_classes
from billing.infra.db.migrate import upgrade_to_head
from billing.infra.db.session import engine
from billing.logging import setup_logging

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)

PROVIDER_ADAPTERS = "billing.infra.providers"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Alembic's env.py runs its own event loop, so it needs a separate thread.
    await asyncio.to_thread(upgrade_to_head)
    logger.info("migrations applied")
    discover(PROVIDER_ADAPTERS)
    logger.info(
        "providers registered",
        extra={"countries": [cls.country_code for cls in provider_classes()]},
    )
    logger.info("app started", extra={"environment": settings.environment})
    yield
    await close_providers()
    await engine.dispose()
    logger.info("app stopped")


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
