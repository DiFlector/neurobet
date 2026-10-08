# neurobet-collector

FON.BET live sports data collector for Neurobet platform.
- Focuses strictly on live in-progress events (`https://fon.bet/live/tennis`).
- Captures Russian-language entity names (`lang=ru`, `ru-RU` locale).
- Employs Playwright Chromium with long-lived browser context.
- Jittered polling (`random.uniform(5.0, 10.0)` seconds) with circuit breaker and backoff.
- Stores raw SHA-256 hashed snapshots in MinIO S3 (`raw-snapshots`).
- Publishes envelopes to Redis Stream `fonbet.raw`.
