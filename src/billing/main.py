import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from billing.api import health
from billing.config import get_settings
from billing.logging import setup_logging

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("app started", extra={"environment": settings.environment})
    yield
    logger.info("app stopped")


app = FastAPI(title="Billing Multi-Country", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
