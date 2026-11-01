# Tennis API/UI live integration check — 2026-10-25

## Result

Completed a live-data integration pass through the backend API proxy and the dashboard. The frontend now handles the odds-free forecast contract when match format is unknown: it shows point/game/set probabilities, labels the match-win probability as unavailable, and does not render invalid percentages or a misleading match-win graph.

## Live event evidence

- Fonbet event `69280501`, Neurobet event `7f67bfb0-a9f4-42ba-998c-6a90f00df19b`, was live. Its latest persisted score snapshot was observed at `2026-10-25T06:17:27Z`.
- `POST /api/tennis-history/prepare` returned `ready=true` for both participants: Ишии С (186 history matches, metrics version 18, partial external history coverage) and Путинцева Ю (1,216 matches, metrics version 4, complete external history coverage).
- The latest forecast API response was calculated at `2026-10-25T06:17:50Z` from that live score. It reported next-point `0.62`, next-game `0.450696`, and current-set `0.734757`; `match_a` was absent because point-in-time match format was unknown. Data quality was `0.25`, with `SERVE_HISTORY_A_MISSING`, `POINT_SEQUENCE_UNAVAILABLE`, and `MATCH_FORMAT_UNKNOWN` recorded.
- The API proxy returned the live event and the same latest forecast under `/diflector/neurobet/api/...`.

## UI and service checks

- Rebuilt and recreated the frontend container. The Docker build completed its TypeScript phase; frontend and backend health endpoints returned `healthy`.
- Opened `/diflector/neurobet` in Chromium, enabled «Показывать все», and verified the live player card rendered its local probabilities and the explicit “Вероятность победы в матче не рассчитана: формат матча неизвестен” message.
- Browser checks found no `NaN`/`undefined`, page errors, or failed network requests. Dashboard API requests, including events, predictions, account, and performance, returned HTTP 200.
- The pass used read-only API requests and forecast persistence only; it did not submit a bet or call bankroll mutation APIs.

## Limitation

The participant histories are available with different coverage: one is partial, and the live PBP/serve evidence is missing. The UI correctly exposes the resulting low quality and unknown match format rather than implying a match-winner prediction.
