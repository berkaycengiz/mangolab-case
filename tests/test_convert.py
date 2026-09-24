from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
import pytest

from app.main import create_app


pytestmark = pytest.mark.anyio


DEFAULT_PARAMS = {
    "amount": "250",
    "from": "EUR",
    "to": "TRY",
    "date": "2026-08-28",
}


def upstream_payload(
    *,
    rate: int | float = 47.1234,
    rate_date: str = "2026-08-28",
    base: str = "EUR",
    target: str = "TRY",
) -> dict[str, object]:
    return {
        "amount": 1.0,
        "base": base,
        "date": rate_date,
        "rates": {target: rate},
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def client_for(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    upstream_base: str | None = "https://fake-upstream.test",
) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = create_app(
        upstream_base=upstream_base,
        transport=httpx.MockTransport(handler),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client


async def test_converts_currency_and_normalizes_codes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/2026-08-28"
        assert request.url.params["base"] == "EUR"
        assert request.url.params["symbols"] == "TRY"
        return httpx.Response(200, json=upstream_payload())

    params = {**DEFAULT_PARAMS, "from": "eur", "to": "try"}
    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=params)

    assert response.status_code == 200
    assert response.json() == {
        "amount": 250,
        "from": "EUR",
        "to": "TRY",
        "rate": 47.1234,
        "result": 11780.85,
        "rate_date": "2026-08-28",
        "asked_date": "2026-08-28",
        "source": "ECB via frankfurter.dev",
    }


async def test_weekend_uses_upstreams_earlier_rate_date() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=upstream_payload(rate_date="2026-08-28"),
        )

    params = {**DEFAULT_PARAMS, "date": "2026-08-30"}
    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=params)

    assert response.status_code == 200
    assert response.json()["asked_date"] == "2026-08-30"
    assert response.json()["rate_date"] == "2026-08-28"


async def test_repeated_rate_question_uses_cache() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=upstream_payload())

    async with client_for(handler) as client:
        first = await client.get("/tools/convert", params=DEFAULT_PARAMS)
        second = await client.get(
            "/tools/convert",
            params={**DEFAULT_PARAMS, "amount": "2"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 1
    assert second.json()["result"] == 94.25


async def test_cache_does_not_mix_dates() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        rate_date = request.url.path.rsplit("/", 1)[-1]
        rate = 1.0 if rate_date == "2026-08-27" else 2.0
        return httpx.Response(
            200,
            json=upstream_payload(rate=rate, rate_date=rate_date),
        )

    async with client_for(handler) as client:
        first = await client.get(
            "/tools/convert",
            params={**DEFAULT_PARAMS, "amount": "10", "date": "2026-08-27"},
        )
        second = await client.get(
            "/tools/convert",
            params={**DEFAULT_PARAMS, "amount": "10", "date": "2026-08-28"},
        )

    assert calls == 2
    assert first.json()["result"] == 10.0
    assert second.json()["result"] == 20.0


@pytest.mark.parametrize(
    ("params", "expected_code"),
    [
        ({"from": "EUR", "to": "TRY", "date": "2026-08-28"}, "missing_amount"),
        ({**DEFAULT_PARAMS, "amount": "0"}, "invalid_amount"),
        ({**DEFAULT_PARAMS, "amount": "-1"}, "invalid_amount"),
        ({**DEFAULT_PARAMS, "amount": "1.12345678901"}, "invalid_amount"),
        ({**DEFAULT_PARAMS, "date": "28-08-2026"}, "invalid_date"),
        ({**DEFAULT_PARAMS, "from": "EU"}, "invalid_currency"),
    ],
)
async def test_rejects_invalid_query_values(
    params: dict[str, str],
    expected_code: str,
) -> None:
    def should_not_run(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("validation errors must not call the upstream")

    async with client_for(should_not_run) as client:
        response = await client.get("/tools/convert", params=params)

    assert response.status_code == 400
    assert response.json()["error"] == expected_code


async def test_accepts_ten_decimal_places_and_rounds_only_the_result() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream_payload(rate=2))

    params = {**DEFAULT_PARAMS, "amount": "1.1234567890"}
    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=params)

    assert response.status_code == 200
    assert response.json()["amount"] == 1.123456789
    assert response.json()["rate"] == 2
    assert response.json()["result"] == 2.25


async def test_large_amount_keeps_exact_json_numbers() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream_payload())

    params = {**DEFAULT_PARAMS, "amount": "100000000000000.01"}
    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=params)

    assert response.status_code == 200
    payload = response.json(parse_float=Decimal)
    assert payload["amount"] == Decimal("100000000000000.01")
    assert payload["rate"] == Decimal("47.1234")
    assert payload["result"] == Decimal("4712340000000000.47")


async def test_large_product_rounds_only_at_the_final_cent() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream_payload(rate=100000000.5))

    params = {**DEFAULT_PARAMS, "amount": "800000000000000000.01"}
    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=params)

    assert response.status_code == 200
    assert response.json(parse_float=Decimal)["result"] == Decimal(
        "80000000400000000001000000.01"
    )


async def test_rate_outside_calculation_range_uses_error_envelope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream_payload(rate=1000000000000))

    params = {**DEFAULT_PARAMS, "amount": "999999999999999999"}
    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=params)

    assert response.status_code == 502
    assert response.json()["error"] == "invalid_upstream_response"
    assert set(response.json()) == {"error", "message"}


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"from": "ZZZ"}, "invalid_currency"),
        ({"to": "ZZZ"}, "invalid_currency"),
        ({"to": "EUR"}, "same_currency"),
        ({"date": "2999-01-01"}, "future_date"),
        ({"date": "1998-12-31"}, "date_before_series"),
    ],
)
async def test_rejects_unsafe_requests_before_upstream_call(
    overrides: dict[str, str],
    expected_code: str,
) -> None:
    def should_not_run(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsafe requests must not call the upstream")

    async with client_for(should_not_run) as client:
        response = await client.get(
            "/tools/convert",
            params={**DEFAULT_PARAMS, **overrides},
        )

    assert response.status_code == 400
    assert response.json()["error"] == expected_code


def timeout_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("too slow", request=request)


def connection_error_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.parametrize(
    ("handler", "expected_status", "expected_code"),
    [
        (timeout_handler, 504, "upstream_timeout"),
        (connection_error_handler, 502, "upstream_unavailable"),
        (
            lambda _request: httpx.Response(500, json={"message": "failed"}),
            502,
            "upstream_error",
        ),
        (
            lambda _request: httpx.Response(200, text="not-json"),
            502,
            "invalid_upstream_response",
        ),
        (
            lambda _request: httpx.Response(404, json={"message": "not found"}),
            404,
            "rate_not_available",
        ),
    ],
)
async def test_maps_upstream_failures_to_explicit_errors(
    handler: Callable[[httpx.Request], httpx.Response],
    expected_status: int,
    expected_code: str,
) -> None:
    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=DEFAULT_PARAMS)

    assert response.status_code == expected_status
    assert response.json()["error"] == expected_code
    assert set(response.json()) == {"error", "message"}


async def test_rejects_rate_from_after_the_asked_date() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=upstream_payload(rate_date="2026-08-31"),
        )

    params = {**DEFAULT_PARAMS, "date": "2026-08-30"}
    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=params)

    assert response.status_code == 502
    assert response.json()["error"] == "invalid_upstream_response"


async def test_rejects_non_positive_upstream_rate() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream_payload(rate=0))

    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=DEFAULT_PARAMS)

    assert response.status_code == 502
    assert response.json()["error"] == "invalid_upstream_response"


async def test_missing_target_rate_is_not_invented() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        payload = upstream_payload()
        payload["rates"] = {"USD": 1.17}
        return httpx.Response(200, json=payload)

    async with client_for(handler) as client:
        response = await client.get("/tools/convert", params=DEFAULT_PARAMS)

    assert response.status_code == 404
    assert response.json()["error"] == "rate_not_available"


async def test_uses_configured_upstream_base_and_preserves_path_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FX_UPSTREAM_BASE", "https://configured.test/fake-upstream/")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "configured.test"
        assert request.url.path == "/fake-upstream/v1/2026-08-28"
        return httpx.Response(200, json=upstream_payload())

    async with client_for(handler, upstream_base=None) as client:
        response = await client.get("/tools/convert", params=DEFAULT_PARAMS)

    assert response.status_code == 200
