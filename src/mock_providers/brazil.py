import random
import uuid
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from mock_providers.scenarios import IssueScenario, LookupScenario, Scenario, hang

router = APIRouter(prefix="/br", tags=["BR"])


class Recipient(BaseModel):
    document: str


class NfeIn(BaseModel):
    reference: str
    recipient: Recipient
    amount_cents: int = Field(gt=0)
    currency: Literal["BRL"]


class NfeOut(BaseModel):
    reference: str
    access_key: str
    protocol: str
    status: Literal["authorized"]
    amount_cents: int


_nfes: dict[str, NfeOut] = {}


def _server_error() -> JSONResponse:
    return JSONResponse({"code": "E500", "reason": "Service unavailable"}, status_code=500)


@router.post("/nfe", response_model=NfeOut)
async def issue_nfe(body: NfeIn, scenario: IssueScenario):
    if scenario is Scenario.ERROR:
        return _server_error()
    if scenario is Scenario.REJECT:
        return JSONResponse(
            {"code": "E301", "reason": "Recipient document is invalid"}, status_code=422
        )

    # Same reference returns the NF-e already authorized instead of a new one.
    nfe = _nfes.get(body.reference)
    if nfe is None:
        nfe = NfeOut(
            reference=body.reference,
            access_key="".join(random.choices("0123456789", k=44)),
            protocol=uuid.uuid4().hex[:15],
            status="authorized",
            amount_cents=body.amount_cents,
        )
        _nfes[body.reference] = nfe

    # Timeout: the NF-e is authorized but the response never arrives in time.
    if scenario is Scenario.TIMEOUT:
        await hang()
    return nfe


@router.get("/nfe", response_model=list[NfeOut])
async def list_nfes():
    return list(_nfes.values())


@router.get("/nfe/{reference}", response_model=NfeOut)
async def get_nfe(reference: str, scenario: LookupScenario):
    if scenario is Scenario.ERROR:
        return _server_error()
    if scenario is Scenario.TIMEOUT:
        await hang()
    nfe = _nfes.get(reference)
    if nfe is None:
        return JSONResponse({"code": "E404", "reason": "NF-e not found"}, status_code=404)
    return nfe
