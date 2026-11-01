# Full doubles archive audit — 2026-11-01

- Scope: full database census; 1,213 frozen finished numeric Fonbet doubles events; cutoff `2026-10-30T07:38:22Z`.
- Census status: complete. Frozen event digest: `01f68882fcb656a9f63f76843ced849e7c567317d604aeeba73a52b26ebbd8ab`.
- Source corpus: 237,946 point-history documents; 238,094 parsed history versions; 231,119 unique source matches.
- Dataset: 1,203 match-labeled matches and 1,201 set-labeled matches; 122,842 usable PIT-certified examples (1,203 match examples and 121,639 set examples). Point/game labels: zero.
- Chronological admission: match/set initial causal train 544 each; holdout 241 each; validation windows 96, 96, 97, 96 each, below the required 100. Both classes are represented in match/set windows.
- Timing: 2 result timestamps unavailable; manifest result-availability certification is false. Overall gate is `blocked`; PIT feature certification is true, but the required target coverage and manifest certification are not met.
- Gate reasons: base target coverage/both-class requirements missing (point/game), walk-forward minimums unmet, training-label chronology gate, and uncertified manifest result availability.
- No model training or holdout quality evaluation was run. Thresholds remain 500 initial-train matches, 4×100 validation matches, 200 holdout matches, both classes, PIT clean.
- Performance workaround: snapshot page size reduced from 500 to 100 after a reproducible 30-second query timeout. Frozen IDs and cutoff were preserved.

See `report.json` for per-target diagnostics, exclusion reasons, counts and provenance; `training_manifest.json` for dataset identity and folds.
