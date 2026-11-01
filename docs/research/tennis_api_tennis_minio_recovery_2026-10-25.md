# API-Tennis MinIO archive and offline recovery check

Cutoff: 2026-10-25 14:47 UTC. This was a read-only production inventory plus a temporary offline parser smoke. No provider request or production import was performed.

## Manifest inventory

- `external_tennis_source_jobs`: 17,934 API-Tennis jobs are `complete`; all 17,934 have an archived raw key and content hash.
- MinIO lists 17,940 objects under `snapshots/external-tennis/api-tennis/`. Every DB-referenced key exists; six objects are not referenced by a currently complete job.
- All 17,934 DB content hashes match the hash encoded in their object key.
- A deterministic sample of 32 objects was fetched and decoded. All 32 passed gzip, JSON, provider envelope, and content-address hash checks; their S3 `sha256` metadata matched. The sample covered 323 events; 22 were successful empty responses.
- There are 12,066 completed jobs with `record_count=0`; all are explicitly marked `provider_empty_without_result=true`. There are 5,868 completed jobs with records. This is an important distinction from missing/corrupt archive data.

## Recovery defect and fix

The provider's successful empty response is exactly `{"success": 1}`. The offline importer previously rejected it because it required a `result` list, which would log legitimate empty archives as invalid. `scripts/import_api_tennis_archive.py` now accepts this exact empty envelope, rejects other missing/non-list result shapes, counts valid empty responses separately, and does not call persistence for an empty result.

Targeted tests: `tests/research/test_import_api_tennis_archive.py` — 4 passed.

Offline smoke: one archived non-empty object (24 events) and one real archived empty object both passed `decode_archive_object` in a one-off container with `--network none` and an empty `API_TENNIS_API_KEY`. No provider was contacted and no DB was configured for that container.

## Remaining work

This check proves object inventory, sampled raw-object integrity, and keyless/offline decoding. It does **not** prove a full database restore into an isolated DB copy, complete recovery of every event, or completion of all queued interval imports. The live archive remains time-bounded: API-Tennis quota was 6,000/6,000 at cutoff; 3,549 interval jobs were queued. The full restore/import acceptance remains open for the planned final reconciliation before the key expires on 2026-11-07.

## Build verification

The updated `neurobet-external-tennis-sources` image was built as `sha256:ab5b503de27d6dff66a9e2ed1b39adee307537a12da961355d1e407af18164aa`. The same real non-empty and empty raw objects then decoded successfully in that built image under `--network none`, with an empty API key. The running source worker was not restarted; its application code was not changed, and the built image is available for the offline restore command. All current services remained healthy.

## Isolated database restoration smoke

A disposable TimescaleDB instance was initialized on an internal-only Docker network and migrated through revision `054_tennis_point_doc_status`. A one-off importer container had access only to that database and the copied MinIO object file; it had no MinIO credentials, no provider key, and no external network access. The real 24-event response was restored twice through `SourceArchive.persist_events`.

Post-restore query: 24 source observations, all 24 retained the source `available_at` timestamp `2026-10-25T01:48:40.459081+00:00`, and the repeated restore created no duplicates. The disposable database container and its network were stopped and removed. This validates sample recovery and idempotency; a full archive-wide isolated restore is still pending.
