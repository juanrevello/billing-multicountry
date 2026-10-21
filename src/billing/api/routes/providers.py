from fastapi import APIRouter
from pydantic import BaseModel

from billing.domain.registry import provider_classes

router = APIRouter(tags=["providers"])


class ProviderOut(BaseModel):
    country_code: str
    provider: str
    currencies: list[str]


@router.get("/providers")
async def list_providers() -> list[ProviderOut]:
    return [
        ProviderOut(
            country_code=cls.country_code,
            provider=cls.name,
            currencies=sorted(cls.supported_currencies),
        )
        for cls in provider_classes()
    ]
