import pytest
from fastapi import HTTPException

from app.admin_confirmations import _validate_confirmation_status_for_decision


@pytest.mark.parametrize(
    ("status", "decision", "previous"),
    [
        ("pending", "approve", None),
        ("pending", "reject", None),
        ("approved", "reject", "approved"),
        ("rejected", "approve", "rejected"),
    ],
)
def test_confirmation_decisions_allow_opposite_correction(status, decision, previous):
    assert _validate_confirmation_status_for_decision(status, decision) == previous


@pytest.mark.parametrize(("status", "decision"), [("approved", "approve"), ("rejected", "reject")])
def test_confirmation_decision_cannot_be_repeated_without_correction(status, decision):
    with pytest.raises(HTTPException) as error:
        _validate_confirmation_status_for_decision(status, decision)
    assert error.value.status_code == 409


def test_superseded_confirmation_cannot_be_corrected():
    with pytest.raises(HTTPException) as error:
        _validate_confirmation_status_for_decision("superseded", "approve")
    assert error.value.status_code == 409
