from types import SimpleNamespace

from db.tennis_confirmations import upsert_manual_confirmation


class _Query:
    def __init__(self, row):
        self.row = row

    def filter_by(self, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.row


class _Session:
    def __init__(self, row):
        self.row = row

    def execute(self, *_args, **_kwargs):
        return None

    def query(self, *_args):
        return _Query(self.row)


def test_pending_confirmation_evidence_and_version_are_frozen():
    row = SimpleNamespace(status="pending", evidence_json={"name": "Player A"}, version=4)
    session = _Session(row)

    unchanged = upsert_manual_confirmation(
        session, kind="player_profile", subject_id="participant", candidate_id="profile",
        evidence={"name": "Player A"},
    )
    assert unchanged.version == 4

    updated = upsert_manual_confirmation(
        session, kind="player_profile", subject_id="participant", candidate_id="profile",
        evidence={"name": "Player A", "opponent": "Player B"},
    )
    assert updated.version == 4
    assert updated.evidence_json == {"name": "Player A"}
