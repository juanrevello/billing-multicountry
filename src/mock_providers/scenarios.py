import asyncio
import random
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from mock_providers.config import get_settings


class Scenario(StrEnum):
    SUCCESS = "success"
    ERROR = "500"
    TIMEOUT = "timeout"
    REJECT = "reject"


def _random_scenario() -> Scenario:
    s = get_settings()
    weights = {
        Scenario.SUCCESS: s.success_weight,
        Scenario.ERROR: s.error_weight,
        Scenario.TIMEOUT: s.timeout_weight,
        Scenario.REJECT: s.reject_weight,
    }
    return random.choices(list(weights), weights=list(weights.values()))[0]


def _parse(value: str) -> Scenario:
    try:
        return Scenario(value.lower())
    except ValueError:
        allowed = ", ".join(s.value for s in Scenario)
        raise HTTPException(400, f"Invalid X-Mock-Scenario, expected one of: {allowed}") from None


def issue_scenario(x_mock_scenario: Annotated[str | None, Header()] = None) -> Scenario:
    """Forced by header, otherwise random by the configured weights."""
    return _parse(x_mock_scenario) if x_mock_scenario else _random_scenario()


def lookup_scenario(x_mock_scenario: Annotated[str | None, Header()] = None) -> Scenario:
    """Lookups only fail when forced by header."""
    return _parse(x_mock_scenario) if x_mock_scenario else Scenario.SUCCESS


IssueScenario = Annotated[Scenario, Depends(issue_scenario)]
LookupScenario = Annotated[Scenario, Depends(lookup_scenario)]


async def hang() -> None:
    await asyncio.sleep(get_settings().timeout_seconds)
