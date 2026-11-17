"""Install version-specific handlers without changing legacy API error formats."""

import pymysql
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from backend.api.v1.core import db_error
from backend.core.errors import ApiError, from_http


def response_for(error: ApiError, request_id: str) -> JSONResponse:
    return JSONResponse(
        error.response(request_id).model_dump(), status_code=error.status_code, headers={"X-Request-Id": request_id}
    )


async def api_error(request: Request, exc: ApiError) -> Response:
    if not request.url.path.startswith("/api/v2/"):
        # Agent reports retain structured correction details inside the legacy detail envelope.
        detail = {"message": exc.message, **exc.details} if exc.details else exc.message
        return await http_exception_handler(request, HTTPException(exc.status_code, detail))
    return response_for(exc, getattr(request.state, "request_id", ""))


async def http_error(request: Request, exc: HTTPException) -> Response:
    if not request.url.path.startswith("/api/v2/"):
        return await http_exception_handler(request, exc)
    return response_for(
        from_http(exc.status_code, exc.detail if isinstance(exc.detail, str) else ""),
        getattr(request.state, "request_id", ""),
    )


async def validation_error(request: Request, exc: RequestValidationError) -> Response:
    if not request.url.path.startswith("/api/v2/"):
        return await request_validation_exception_handler(request, exc)
    return response_for(ApiError("VALIDATION_FAILED"), getattr(request.state, "request_id", ""))


async def database_error(request: Request, exc: pymysql.err.MySQLError) -> Response:
    if not request.url.path.startswith("/api/v2/"):
        return await db_error(request, exc)
    return response_for(ApiError("RUNTIME_UNAVAILABLE"), getattr(request.state, "request_id", ""))


def install_errors(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(pymysql.err.MySQLError, database_error)
