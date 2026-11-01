# Operational note: API-Tennis importer invoked while checking CLI help — 2026-10-26

While inspecting the offline archive-recovery command, I invoked `python scripts/import_api_tennis_archive.py --help`. The script does not define an argument parser or handle `--help`; it entered `main()` and began reading the API-Tennis MinIO archive using the production Research container's database and MinIO configuration.

The child process was identified in `docker top` and terminated with SIGTERM after an approximately 18-second observed runtime. A follow-up process check showed only the normal Research service process. No provider API key or provider network request is used by this importer. Its persistence path inserts source observations and uses idempotent checks; it does not rewrite saved raw objects.

The number of source observations committed before termination could not be measured safely: a bounded 15-second production count query timed out while scanning the observation table. In addition to idempotent source observations, the importer can update normalized result rows, canonical projections, source state, and source metrics; some of these may have been written before termination. The latest `canonical_projection` state row remained timestamped 00:16 UTC, before the attempted run, but that alone cannot rule out row writes before an interruption. No rows were deleted or raw archive objects rewritten. Production services were healthy at the subsequent check. The full archive-wide restore remains unverified and must be run only against a disposable isolated database, never via this production command path.

This note records an unintended partial production replay and its containment. It is not acceptance evidence for the isolated full restore.
