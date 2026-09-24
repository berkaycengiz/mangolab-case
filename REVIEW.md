# Review of `tool.py`

Findings are ranked by the risk of giving a customer a wrong answer.

## 1. The required query parameters are silently ignored

FastAPI exposes `from_` and `on`, while the contract requires `from` and
`date`. Both have defaults, so a valid-looking request can return a `200` using
EUR and the latest rate instead of the customer's currency and historical date.
**Verify:** With a fake upstream, call
`/tools/convert?amount=100&from=USD&to=TRY&date=2026-08-28`. The code requests
`/v1/latest?base=EUR&symbols=TRY`.

## 2. Upstream failures become successful zero conversions

The broad `except` turns timeouts, connection errors, and malformed responses
into a `200` response with `rate: 0.0` and `result: 0.0`. A `500` is not checked;
it can lead to a `/latest` fallback or the same zero response. An agent could
tell a paying customer that their money converts to zero instead of reporting
a failure.
**Verify:** Make the fake upstream raise a connection error; the endpoint returns
`200` with zero values.

## 3. Historical rates and their dates are unreliable

The cache key omits the requested date. A rate fetched for one day is reused for
another, and `rate_date` is constructed from the request or today's date instead
of the upstream's `date`. If a target rate is absent, the code also falls back
to `/latest`, which can put a current rate into a historical answer. The
customer cannot tell which day's rate was used.
**Verify:** Request two dates for the same pair through `fetch_rate`; only one
upstream call occurs, while the two returned dates differ. Also return an
earlier `date` from the fake upstream and compare it with the endpoint's
`rate_date`.

## 4. Rounding the rate first changes the conversion

The code rounds the rate to two decimals before multiplying. With a fake rate
of `1.2349` and amount `100`, it returns `123.00` instead of `123.49`.
**Verify:** Return that rate from the fake upstream and compare the result with
full-precision multiplication rounded only at the end.

## The one I would fix before shipping tonight

Fix **#1** first: require the documented `from` and `date` parameters and bind
them to the calculation. Otherwise, even an ordinary, successful request can
silently answer a different question.

## Suspicious but acceptable

Using an earlier published rate for a weekend is acceptable if the response
shows its actual publication date. The problem here is the false date label,
not the use of an earlier rate itself.