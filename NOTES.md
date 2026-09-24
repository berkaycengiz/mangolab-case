# Notes

## Decisions

I query the requested date, never `latest`. For weekends and ECB holidays I use
Frankfurter's earlier rate and return its real `rate_date` beside `asked_date`.
Future dates, dates before the series, and upstream dates after the request are
rejected. I multiply with `Decimal` without rounding during the calculation,
then round the final result to two decimals with `ROUND_HALF_UP`. The limit of
18 digits before and 10 after the decimal point, and cent rounding, are service
choices. An in-memory cache stores the rate and its date by currency pair and
requested date.

## With another day

I would expire cached quotes for today after new ECB rates may be published,
and make concurrent requests for the same uncached quote share one upstream
call. The current cache handles repeated sequential requests but not those
two cases.

## AI tools

I used Codex to interpret the brief, check Frankfurter's v1 contract, draft the
service and tests, and review edge cases. I checked the behaviour with a live
Frankfurter call and offline fake-upstream tests.

## One thing the AI got wrong

A fake-upstream test exposed a precision bug in the AI's approach: the service
returned `.00` where rounding the final result should return `.01`. I changed
the calculation to avoid intermediate rounding and added a regression test for
this case.