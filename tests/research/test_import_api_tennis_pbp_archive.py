from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if not (SCRIPTS / "import_api_tennis_pbp_archive.py").is_file():
    SCRIPTS = Path("/app/scripts")
sys.path.insert(0, str(SCRIPTS))

from import_api_tennis_pbp_archive import build_arg_parser  # noqa: E402
from app.archive_backfill import migrate_to_available_time_cursor  # noqa: E402


def test_pbp_importer_accepts_bounded_checkpoint_batches():
    args = build_arg_parser().parse_args(["--max-batches", "3"])

    assert args.max_batches == 3


def test_pbp_importer_help_is_parsed_instead_of_starting_archive_work():
    try:
        build_arg_parser().parse_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--help must exit without running the importer")


def test_old_source_id_checkpoint_restarts_with_auditable_v1_state():
    migrated = migrate_to_available_time_cursor({
        "status": "running", "rows_scanned": 174400, "documents_saved": 172236,
        "last_source_match_id": "12109732", "last_available_at": "2026-10-23T12:00:00+00:00",
    })

    assert migrated["cursor_version"] == 2
    assert migrated["status"] == "running"
    assert "last_source_match_id" not in migrated
    assert migrated["legacy_cursor_v1"]["last_source_match_id"] == "12109732"


def test_available_time_checkpoint_migration_is_idempotent():
    migrated = migrate_to_available_time_cursor({"status": "running", "rows_scanned": 7})

    assert migrate_to_available_time_cursor(migrated) == migrated
