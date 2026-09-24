# FX conversion tool

A Python 3.11+ FastAPI tool that converts currencies using ECB rates from
Frankfurter v1.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
./run.sh
```

Run tests separately; the service does not need to be running:

```bash
./test.sh
```

Tests fake every upstream call and pass even with a closed port:
`FX_UPSTREAM_BASE=http://127.0.0.1:1 ./test.sh`.

`PORT` defaults to `8080`. `FX_UPSTREAM_BASE` defaults to
`https://api.frankfurter.dev`; both can be overridden.

## Request

```bash
curl "http://localhost:8080/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
```

Success returns `amount`, `from`, `to`, `rate`, `result`, `rate_date`,
`asked_date`, and `source`. Failures return a non-2xx status with
`{"error":"<code>","message":"<readable sentence>"}`.

## Behaviour and error codes

| Case | Behaviour |
|---|---|
| Weekend or ECB holiday | `200` with the earlier published rate. `rate_date` shows its real date; `asked_date` shows the request date. No rate is invented. |
| Future date | `400 future_date`. |
| Before 1999-01-04, when the series starts | `400 date_before_series`. |
| Unknown currency | `400 invalid_currency`; codes are case-insensitive. |
| Same `from` and `to` | `400 same_currency`. |
| Upstream is slow | `504 upstream_timeout`. |
| Upstream returns 500 | `502 upstream_error`. |
| Upstream returns non-JSON or invalid rate data | `502 invalid_upstream_response`. This also rejects a rate dated after `asked_date` or one too large to calculate safely. |
| Missing `amount` | `400 missing_amount`. |
| Zero, negative, or oversized `amount` | `400 invalid_amount`; the limit is 18 digits before and 10 after the decimal point. |
| Positive `amount` has exactly 10 decimal places | Accepted; `result` is rounded to two decimal places. |
| Other failures | `400 missing_parameter`, `400 invalid_date`, `400 invalid_request`; `502 upstream_unavailable` for a connection failure; `404 rate_not_available` for upstream 404 or a missing target rate. |

Rates are cached by currency pair and requested date, so repeated requests do
not call Frankfurter again.