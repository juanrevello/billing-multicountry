"""RFC 9457 Problem Details for every error response."""

import logging
import uuid
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemDetails(BaseModel):
    """RFC 9457 body; the optional fields are extensions present only when they apply."""

    type: str
    title: str
    status: int
    detail: str | None = None
    invoice_id: uuid.UUID | None = None
    invoice_status: str | None = None
    supported_currencies: list[str] | None = None
    errors: list[dict[str, Any]] | None = None


def problem_doc(description: str) -> dict[str, Any]:
    """OpenAPI entry for an error response served as application/problem+json."""
    schema = {"$ref": "#/components/schemas/ProblemDetails"}
    return {"description": description, "content": {PROBLEM_MEDIA_TYPE: {"schema": schema}}}


class Problem(Exception):
    def __init__(
        self,
        status: int,
        type_: str,
        title: str,
        detail: str | None = None,
        /,
        **extra: Any,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.type = type_
        self.title = title
        self.detail = detail
        self.extra = extra


def problem_response(
    status: int, type_: str, title: str, detail: str | None = None, /, **extra: Any
) -> JSONResponse:
    body = {"type": type_, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body.update({k: v for k, v in extra.items() if v is not None})
    return JSONResponse(jsonable_encoder(body), status_code=status, media_type=PROBLEM_MEDIA_TYPE)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(_: Request, exc: Problem) -> JSONResponse:
        return problem_response(exc.status, exc.type, exc.title, exc.detail, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return problem_response(
            422, "/problems/validation-error", "Request validation failed", errors=errors
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem_response(
            exc.status_code, "about:blank", HTTPStatus(exc.status_code).phrase, str(exc.detail)
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return problem_response(500, "about:blank", "Internal Server Error")


def install_problem_schema(app: FastAPI) -> None:
    """Register ProblemDetails in the spec, where every problem_doc() response refers to it."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            spec = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
            )
            schemas = spec.setdefault("components", {}).setdefault("schemas", {})
            schemas["ProblemDetails"] = ProblemDetails.model_json_schema()
            app.openapi_schema = spec
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
