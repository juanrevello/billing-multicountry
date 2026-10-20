from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from billing.config import provider_settings
from billing.domain.provider import RETRYABLE_ERRORS


def retrying(country_code: str) -> AsyncRetrying:
    """Exponential backoff with jitter; `max_retries` counts retries after the first attempt.

    Usage:
        async for attempt in retrying(invoice.country_code):
            with attempt:
                await provider.issue(payload)
    """
    settings = provider_settings(country_code)
    return AsyncRetrying(
        stop=stop_after_attempt(settings.max_retries + 1),
        wait=wait_exponential_jitter(
            multiplier=settings.backoff_base_seconds, jitter=settings.backoff_base_seconds
        ),
        retry=retry_if_exception_type(RETRYABLE_ERRORS),
        reraise=True,
    )
