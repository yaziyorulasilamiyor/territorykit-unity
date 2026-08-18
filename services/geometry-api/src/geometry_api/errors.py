"""One JSON error shape for every failure — validation and 500 included (FAZ-3-PLAN.md §5, §7).

    {"error": {"code": "revision_not_found", "message": "...", "details": {}}}

The HTTP status varies (400/404/410/422/500); the body shape never does. Three FastAPI exception
handlers funnel into :func:`_error_response`, so no route ever builds this shape by hand:

* :class:`ApiError` — raised by route code and by :mod:`geometry_api.deps` (which translates
  :mod:`geometry_api.registry`'s exceptions into one of these).
* ``RequestValidationError`` — FastAPI/pydantic request parsing failures become
  ``422 validation_error`` with the offending fields listed under ``details.fields``.
* Anything else — ``500 internal_error``, with a fixed message. The real exception is not
  serialised into the response; it still propagates to the server logs via FastAPI's normal
  logging, just not to the client.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

CODE_VALIDATION_ERROR = "validation_error"
CODE_INTERNAL_ERROR = "internal_error"


class ApiError(Exception):
    """A request failure with a stable machine-readable code and an HTTP status.

    ``details`` defaults to ``{}`` rather than being omitted, so every error response has the
    same three top-level keys whether or not this particular failure has extra context to give.
    """

    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _error_response(
    status_code: int, code: str, message: str, details: dict[str, Any]
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
        headers={"Cache-Control": "no-store"},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {"loc": [str(part) for part in error["loc"]], "msg": error["msg"]}
            for error in exc.errors()
        ]
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            CODE_VALIDATION_ERROR,
            "the request did not match the expected shape",
            {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            CODE_INTERNAL_ERROR,
            "an unexpected error occurred",
            {},
        )
