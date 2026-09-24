from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, DecimalException, ROUND_HALF_UP
from typing import Annotated

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app.fx import FxService, ToolError


DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"
DEFAULT_TIMEOUT_SECONDS = 5.0
MONEY_QUANTUM = Decimal("0.01")

AmountQuery = Annotated[
    Decimal,
    Query(gt=0, max_digits=28, decimal_places=10),
]


def create_app(
    *,
    upstream_base: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> FastAPI:
    configured_base = (
        upstream_base or os.getenv("FX_UPSTREAM_BASE") or DEFAULT_UPSTREAM_BASE
    ).rstrip("/") + "/"

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        async with httpx.AsyncClient(
            base_url=configured_base,
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        ) as client:
            application.state.fx_service = FxService(client)
            yield

    application = FastAPI(
        title="MangoLab FX Tool",
        version="1.0.0",
        lifespan=lifespan,
    )

    @application.exception_handler(ToolError)
    async def handle_tool_error(_request: Request, exc: ToolError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        code, message = _describe_validation_error(exc)
        return JSONResponse(
            status_code=400,
            content={"error": code, "message": message},
        )

    @application.get("/tools/convert")
    async def convert(
        request: Request,
        amount: AmountQuery,
        from_currency: Annotated[
            str,
            Query(
                alias="from",
                min_length=3,
                max_length=3,
                pattern=r"^[A-Za-z]{3}$",
            ),
        ],
        to_currency: Annotated[
            str,
            Query(
                alias="to",
                min_length=3,
                max_length=3,
                pattern=r"^[A-Za-z]{3}$",
            ),
        ],
        asked_date: Annotated[date, Query(alias="date")],
    ) -> Response:
        base_currency = from_currency.upper()
        target_currency = to_currency.upper()
        service: FxService = request.app.state.fx_service
        quote = await service.get_rate(base_currency, target_currency, asked_date)
        try:
            result = (amount * quote.rate).quantize(
                MONEY_QUANTUM, rounding=ROUND_HALF_UP
            )
        except DecimalException as exc:
            raise ToolError(
                502,
                "invalid_upstream_response",
                "The exchange-rate provider returned a rate outside the supported range.",
            ) from exc

        return _exact_decimal_response(
            {
                "amount": amount,
                "from": base_currency,
                "to": target_currency,
                "rate": quote.rate,
                "result": result,
                "rate_date": quote.rate_date.isoformat(),
                "asked_date": asked_date.isoformat(),
                "source": "ECB via frankfurter.dev",
            }
        )

    return application


def _describe_validation_error(exc: RequestValidationError) -> tuple[str, str]:
    errors = exc.errors()
    if not errors:
        return "invalid_request", "The request is invalid."

    first_error = errors[0]
    parameter = str(first_error.get("loc", ("query", "unknown"))[-1])
    error_type = str(first_error.get("type", ""))

    if error_type == "missing":
        if parameter == "amount":
            return "missing_amount", "Amount is required."
        return "missing_parameter", f"Query parameter '{parameter}' is required."
    if parameter == "amount":
        return (
            "invalid_amount",
            "Amount must be positive, with at most 18 digits before and 10 digits after the decimal point.",
        )
    if parameter in {"from", "to"}:
        return "invalid_currency", "Currency codes must contain exactly three letters."
    if parameter == "date":
        return "invalid_date", "Date must use the YYYY-MM-DD format."
    return "invalid_request", "The request is invalid."


def _exact_decimal_response(fields: dict[str, Decimal | str]) -> Response:
    # The standard JSON encoder rejects Decimal; converting to float loses precision.
    # These are the only numeric fields, and each has already been checked finite.
    body = "{" + ",".join(
        f"{json.dumps(key)}:"
        + (str(value) if isinstance(value, Decimal) else json.dumps(value))
        for key, value in fields.items()
    ) + "}"
    return Response(content=body, media_type="application/json")


app = create_app()
