import csv
from datetime import datetime, timezone

from scripts import import_external_tennis


def _write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_charting_import_uses_ingestion_time_not_match_date_for_availability(tmp_path, monkeypatch):
    charting = tmp_path / "charting"
    charting.mkdir()
    _write_csv(
        charting / "charting-m-matches.csv",
        ["match_id", "Date", "Surface"],
        [{"match_id": "match-1", "Date": "20200101", "Surface": "Hard"}],
    )
    _write_csv(
        charting / "charting-m-stats-ServeBasics.csv",
        ["match_id", "row", "player", "pts", "pts_won"],
        [{"match_id": "match-1", "row": "Total", "player": "Player A", "pts": "50", "pts_won": "31"}],
    )
    monkeypatch.setattr(import_external_tennis, "DATA", tmp_path)

    rows = import_external_tennis.import_charting()

    assert len(rows) == 1
    source_event_at, available_at = rows[0][4:6]
    assert source_event_at == datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert available_at.tzinfo is not None
    assert available_at > source_event_at
    assert rows[0][6] == "database_ingest_time"
