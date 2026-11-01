# Tennis feature/source completeness audit — 2026-10-25

Read-only manifest analysis generated at 2026-10-25 09:31 UTC from [`tennis_training_examples_c7e3c68857dd.jsonl`](tennis_training_examples_c7e3c68857dd.jsonl), plus the same-day production category/source census. Machine-readable manifest counts and lag examples are in [`tennis_feature_source_completeness_2026-10-25.json`](tennis_feature_source_completeness_2026-10-25.json). No database rows, model artifacts, or holdout metrics were changed or opened.

## PIT features, format, surface, server, and serve statistics

| Signal | Coverage | Evidence |
|---|---:|---|
| PIT-certified examples | 0 / 4,826 | Every example is missing source-time evidence for `event_format`. |
| Event state | 4,826 / 4,826 available | The snapshot timestamp is present. |
| Event format | 0 / 4,826 | Neither `best_of` nor its source availability time is present. |
| Surface | 0 / 4,826 known | Surface is explicitly unknown/defaulted for this manifest. |
| Player metrics | 421 observed / 4,405 defaulted slices | Defaults are not evidence of source coverage. |
| Serve metrics, side A | 0 / 4,826 observed; denominator absent in all 4,826 | All values are contract defaults. |
| Serve metrics, side B | 0 / 4,826 observed; denominator absent in all 4,826 | All values are contract defaults. |
| Point server | 356 known / 4,470 unknown | From `source_point_server_known` in the saved examples. |

The external source census in [`tennis_source_coverage_2026-10-25.md`](tennis_source_coverage_2026-10-25.md) gives row denominators for stored serve fields: API-Tennis 70,621 / 496,868; Sackmann 40,267 / 542,113; `ten` 109,951 / 111,404; TML 128,166 / 826,960; other listed providers have no complete winner/loser serve pair in those columns. These are source-row counts, not point-opportunity denominators, and do not establish valid PIT serve features.

## Results, duplicates, conflicts, and chronology

The Phase 52 result-time audit identifies 2,211 results with official metadata, 277 with an exact previously observed final-score snapshot, and 143 without a defensible result time; the last group is excluded from the accepted manifest. No scheduled start time was substituted for result availability.

Within the saved forecast examples, there are zero exact duplicate `(snapshot, target, point_index)` rows and zero conflicting score/label states at that key. A timeline state appears in later snapshots 3,757 additional times; those are repeated observations over time, not duplicate rows. The independent label audit remains the authority for cross-source result contradictions and exclusions.

The snapshot-to-label availability lag is measurable for 4,811 examples: median 244,021 seconds (2.82 days), p95 708,268 seconds (8.20 days), maximum 839,159 seconds (9.71 days). Nine examples across three matches have label availability earlier than the forecast snapshot by 4–14 minutes. The example IDs and timestamps are recorded in the JSON report and need chronology review before they can support any future admission.

Provider ingestion latency cannot be calculated from this manifest: it does not carry the paired provider `retrieved_at` and Neurobet `received_at` timestamps for each feature. This is recorded as a timestamp-coverage gap, not reported as zero latency. The existing raw Fonbet S3 source audit found no explicit format field in 50 recent payloads / 3,079 event rows; current DB metadata had `best_of` and `best_of_available_at` on 0 / 3,847 events at that earlier census.

## Category and point-history coverage

The same-day category census contains 104,077 Flashscore matches, including 51,147 ITF and 23 junior rows (seasons 2022–2024 only). It contains 2,367 unknown categories. Of 4,344 Fonbet tennis events, 378 categories are unknown, 78 are ITF, and zero are junior. Linked PBP coverage has 78 ITF source matches and no junior PBP versions. No current Fonbet junior event cohort exists to link and enqueue as a junior PBP job; do not synthesize one.

## Decision

The requested completeness measurement is complete and reproducible. The data do not pass PIT readiness: `event_format` remains missing in every example, serve denominators remain unavailable, server identity is unknown in 4,470 examples, and source-to-ingest latency cannot be computed from absent timestamp pairs. The admission gate and sealed holdout remain unchanged.
