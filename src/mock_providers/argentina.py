import random
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from mock_providers.scenarios import IssueScenario, LookupScenario, Scenario, hang

router = APIRouter(prefix="/ar", tags=["AR"])


class VoucherIn(BaseModel):
    external_id: str
    customer_id: str
    total: Decimal = Field(gt=0, decimal_places=2)
    currency_code: Literal["PES", "DOL"]


class VoucherOut(BaseModel):
    external_id: str
    voucher_number: int
    cae: str
    total: Decimal
    currency_code: str


_vouchers: dict[str, VoucherOut] = {}


def _server_error() -> JSONResponse:
    return JSONResponse({"errors": [{"code": 500, "message": "Internal error"}]}, status_code=500)


@router.post("/vouchers", status_code=201, response_model=VoucherOut)
async def issue_voucher(body: VoucherIn, scenario: IssueScenario):
    if scenario is Scenario.ERROR:
        return _server_error()
    if scenario is Scenario.REJECT:
        return JSONResponse(
            {"errors": [{"code": 10016, "message": "Customer is not a valid taxpayer"}]},
            status_code=400,
        )

    # Same external_id returns the voucher already issued instead of a new one.
    existing = _vouchers.get(body.external_id)
    if existing is None:
        voucher = VoucherOut(
            external_id=body.external_id,
            voucher_number=len(_vouchers) + 1,
            cae="".join(random.choices("0123456789", k=14)),
            total=body.total,
            currency_code=body.currency_code,
        )
        _vouchers[body.external_id] = voucher

    # Timeout: the voucher is issued but the response never arrives in time.
    if scenario is Scenario.TIMEOUT:
        await hang()

    if existing is not None:
        return JSONResponse(existing.model_dump(mode="json"), status_code=200)
    return voucher


@router.get("/vouchers", response_model=list[VoucherOut])
async def list_vouchers():
    return list(_vouchers.values())


@router.get("/vouchers/{external_id}", response_model=VoucherOut)
async def get_voucher(external_id: str, scenario: LookupScenario):
    if scenario is Scenario.ERROR:
        return _server_error()
    if scenario is Scenario.TIMEOUT:
        await hang()
    voucher = _vouchers.get(external_id)
    if voucher is None:
        return JSONResponse(
            {"errors": [{"code": 404, "message": "Voucher not found"}]}, status_code=404
        )
    return voucher
