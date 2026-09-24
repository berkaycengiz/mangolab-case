from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx


SERIES_START = date(1999, 1, 4)

# Frankfurter v1 is a frozen, ECB-only API. Keeping its published currency set
# locally lets us reject bad tool arguments without spending an upstream call.
SUPPORTED_CURRENCIES = frozenset(
    {
        "AUD",
        "BGN",
        "BRL",
        "CAD",
        "CHF",
        "CNY",
        "CZK",
        "DKK",
        "EUR",
        "GBP",
        "HKD",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "ISK",
        "JPY",
        "KRW",
        "MXN",
        "MYR",
        "NOK",
        "NZD",
        "PHP",
        "PLN",
        "RON",
        "SEK",
        "SGD",
        "THB",
        "TRY",
        "USD",
        "ZAR",
    }
)


class ToolError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RateQuote:
    rate: Decimal
    rate_date: date


class FxService:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._cache: dict[tuple[str, str, date], RateQuote] = {}

    async def get_rate(
        self,
        base_currency: str,
        target_currency: str,
        asked_date: date,
    ) -> RateQuote:
        self._validate_request(base_currency, target_currency, asked_date)

        cache_key = (base_currency, target_currency, asked_date)
        cached_quote = self._cache.get(cache_key)
        if cached_quote is not None:
            return cached_quote

        try:
            response = await self._client.get(
                f"v1/{asked_date.isoformat()}",
                params={"base": base_currency, "symbols": target_currency},
            )
        except httpx.TimeoutException as exc:
            raise ToolError(
                504,
                "upstream_timeout",
                "The exchange-rate provider did not respond in time.",
            ) from exc
        except httpx.RequestError as exc:
            raise ToolError(
                502,
                "upstream_unavailable",
                "The exchange-rate provider could not be reached.",
            ) from exc

        if response.status_code == 404:
            raise ToolError(
                404,
                "rate_not_available",
                "No exchange rate is available for the requested date.",
            )
        if response.is_error:
            raise ToolError(
                502,
                "upstream_error",
                "The exchange-rate provider returned an error.",
            )

        quote = self._parse_quote(
            response=response,
            base_currency=base_currency,
            target_currency=target_currency,
            asked_date=asked_date,
        )
        self._cache[cache_key] = quote
        return quote

    @staticmethod
    def _validate_request(
        base_currency: str,
        target_currency: str,
        asked_date: date,
    ) -> None:
        unsupported = [
            currency
            for currency in (base_currency, target_currency)
            if currency not in SUPPORTED_CURRENCIES
        ]
        if unsupported:
            raise ToolError(
                400,
                "invalid_currency",
                f"Unsupported currency code: {unsupported[0]}.",
            )
        if base_currency == target_currency:
            raise ToolError(
                400,
                "same_currency",
                "Source and target currencies must be different.",
            )
        if asked_date > datetime.now(timezone.utc).date():
            raise ToolError(
                400,
                "future_date",
                "Exchange rates cannot be requested for a future date.",
            )
        if asked_date < SERIES_START:
            raise ToolError(
                400,
                "date_before_series",
                f"Exchange rates are only available from {SERIES_START.isoformat()}.",
            )

    @staticmethod
    def _parse_quote(
        response: httpx.Response,
        base_currency: str,
        target_currency: str,
        asked_date: date,
    ) -> RateQuote:
        try:
            payload = response.json(parse_float=Decimal)
        except (ValueError, TypeError) as exc:
            raise ToolError(
                502,
                "invalid_upstream_response",
                "The exchange-rate provider returned invalid JSON.",
            ) from exc

        if not isinstance(payload, dict):
            raise FxService._invalid_payload_error()

        payload_base = payload.get("base")
        if not isinstance(payload_base, str) or payload_base.upper() != base_currency:
            raise FxService._invalid_payload_error()

        raw_rate_date = payload.get("date")
        if not isinstance(raw_rate_date, str):
            raise FxService._invalid_payload_error()
        try:
            rate_date = date.fromisoformat(raw_rate_date)
        except ValueError as exc:
            raise FxService._invalid_payload_error() from exc

        # A weekend/holiday may resolve to an earlier working day. A later date
        # would answer a historical question with information from the future.
        if rate_date > asked_date or rate_date < SERIES_START:
            raise FxService._invalid_payload_error()

        rates = payload.get("rates")
        if not isinstance(rates, dict) or target_currency not in rates:
            raise ToolError(
                404,
                "rate_not_available",
                "No exchange rate is available for the requested date.",
            )

        raw_rate = rates[target_currency]
        if isinstance(raw_rate, bool) or not isinstance(raw_rate, (int, float, Decimal)):
            raise FxService._invalid_payload_error()
        try:
            rate = Decimal(str(raw_rate))
        except InvalidOperation as exc:
            raise FxService._invalid_payload_error() from exc
        if not rate.is_finite() or rate <= 0:
            raise FxService._invalid_payload_error()

        return RateQuote(rate=rate, rate_date=rate_date)

    @staticmethod
    def _invalid_payload_error() -> ToolError:
        return ToolError(
            502,
            "invalid_upstream_response",
            "The exchange-rate provider returned an invalid response.",
        )
