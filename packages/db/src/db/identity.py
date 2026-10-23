"""Point-in-time identity helpers shared by metric snapshot writers."""

from datetime import datetime

from sqlalchemy import select


def confirmed_source_player_id(session, participant_id, *, source: str, as_of: datetime) -> str | None:
    """Return the latest confirmed source identity available at ``as_of``.

    A later revocation or reassignment must not rewrite the identity attached
    to an older metric snapshot.
    """
    from db.models import TennisPlayerIdentityLink

    latest = session.execute(
        select(TennisPlayerIdentityLink)
        .where(
            TennisPlayerIdentityLink.participant_id == participant_id,
            TennisPlayerIdentityLink.source == source,
            TennisPlayerIdentityLink.available_at <= as_of,
        )
        .order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc())
        .limit(1)
    ).scalars().first()
    return latest.source_player_id if latest and latest.status == "confirmed" else None
