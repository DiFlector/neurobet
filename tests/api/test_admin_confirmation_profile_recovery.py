import uuid

from app.admin_confirmations import _get_or_create_player_profile
from db.models import TennisPlayerProfile


class _Session:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []

    def get(self, model, _participant_id, with_for_update=False):
        assert model is TennisPlayerProfile
        assert with_for_update is True
        return self.existing

    def add(self, row):
        self.added.append(row)


def test_confirmation_recreates_missing_profile_row_from_durable_request():
    participant_id = uuid.uuid4()
    session = _Session()

    profile = _get_or_create_player_profile(session, participant_id)

    assert profile.participant_id == participant_id
    assert session.added == [profile]


def test_confirmation_reuses_existing_profile_row():
    participant_id = uuid.uuid4()
    existing = TennisPlayerProfile(participant_id=participant_id)
    session = _Session(existing)

    profile = _get_or_create_player_profile(session, participant_id)

    assert profile is existing
    assert session.added == []
