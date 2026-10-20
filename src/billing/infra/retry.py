import logging

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from billing.config import provider_settings
from billing.domain.provider import RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


def retrying(country_code: str) -> AsyncRetrying:
    """Exponential backoff with jitter; `max_retries` counts retries after the first attempt.

    Usage:
        async for attempt in retrying(invoice.country_code):
            with attempt:
                await provider.issue(invoice)
    """
    settings = provider_settings(country_code)
    return AsyncRetrying(
        stop=stop_after_attempt(settings.max_retries + 1),
        wait=wait_exponential_jitter(
            multiplier=settings.backoff_base_seconds, jitter=settings.backoff_base_seconds
        ),
        retry=retry_if_exception_type(RETRYABLE_ERRORS),
        before_sleep=_log_retry,
        reraise=True,
    )


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning(
        "provider call failed, retrying",
        extra={
            "attempt": state.attempt_number,
            "error": repr(exc),
            "sleep_seconds": round(state.upcoming_sleep, 3),
        },
    )
