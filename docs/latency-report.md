# Latency report (MEASURED, local)

Measured by `python -m linter.lint` against the generated salon server on localhost (50 calls per tool, in-memory store, client and server on the same machine). **This is NOT a deployed-AWS number and does not include Alexa+ network time.** Budget: 500 ms round trip.

| Tool | p50 (ms) | p95 (ms) |
|---|---|---|
| business_info | 2.4 | 3.2 |
| search_services | 2.4 | 2.9 |
| check_availability | 3.3 | 4.2 |
| get_my_bookings | 2.3 | 4.4 |
