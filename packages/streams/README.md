# neurobet-streams

Redis Streams messaging package for Neurobet platform.

### Features
- Canonical Stream constants (15 core streams + `streams.dead_letter`)
- `MessageEnvelope` with UUID4, idempotency keys, ISO8601 UTC timestamps, and retry metadata
- At-least-once delivery with Consumer Groups (`XREADGROUP`, `XACK`, `XPENDING`, `XCLAIM`)
- Idempotency guard via Redis atomic `SET NX EX 86400`
- Exponential backoff / retry policy and automatic dead-letter queue routing (`streams.dead_letter`)
- Synchronous and asynchronous publisher and consumer implementations
