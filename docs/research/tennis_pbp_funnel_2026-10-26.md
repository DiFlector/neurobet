# Tennis PBP funnel — 2026-10-26

Read-only production census. It counts Flashscore source documents and latest parsed versions linked to numeric Fonbet event IDs. No training or model evaluation was run.

| Funnel stage | Current count |
|---|---:|
| Confirmed Flashscore identity links | 704 participants |
| Flashscore PBP source match IDs with any document | 204 |
| Source match IDs with a complete document | 130 |
| Non-retrospective source match IDs | 65 |
| Non-retrospective source match IDs with complete PBP | 43 |
| Confirmed/manual linked source match IDs | 186 unique IDs across both statuses; 187 Fonbet events |
| Flashscore PBP documents | 4,397; 1,207 marked complete |

For the four label targets, the latest stored versions cover 4,128 numeric Fonbet events: point labels are present on 114 events (20,440 transitions), game labels on 110 (3,027), set labels on 103 (286), and finished events number 4,121. Only 9 point-labelled events, 9 game-labelled events, and 7 set-labelled events have non-retrospective versions. Match-result `available_at` alignment is not certified. These counts include retrospective records and are not training-admission counts.

The archive has crossed 200 unique source match IDs with some PBP document, but fewer than 200 have a complete sequence, and only 43 are both complete and non-retrospective. The four-target observed-label coverage remains too small to call the PBP corpus complete or PIT-ready. Current PBP queue: 3,189 waiting for players, 168 retry, 29 queued, 20 waiting for match, and 103 finalized. The profile refresh queue is 746 completed, 932 retry, 440 blocked, 441 failed, 23 queued, and 84 doubles skipped.

Machine-readable counts: [`tennis_pbp_funnel_2026-10-26.json`](tennis_pbp_funnel_2026-10-26.json).
