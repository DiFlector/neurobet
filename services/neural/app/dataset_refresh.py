"""Build the training tape once, then append new matches in batches of at least ten."""

import logging
import os
import time

from .dataset import get_dataset_builder
from .dataset_cache import MIN_NEW_MATCHES, append_store, ids_to_build, read_manifest, refresh_lock

logger = logging.getLogger("neural.dataset_refresh")

POLL_SECONDS = 60


def refresh_once(session, sport_code: str = "tennis") -> bool:
    """Append every waiting match when at least ten are new. An empty tape is built in full."""
    with refresh_lock():
        builder = get_dataset_builder(sport_code)
        version = str(getattr(builder.feature_builder, "feature_version", "0"))
        events = builder.list_events(session)
        manifest = read_manifest(sport_code, version)
        saved = list(manifest.get("saved_ids") or []) if manifest else []
        skipped = list(manifest.get("skipped_ids") or []) if manifest else []
        pending = ids_to_build(
            [str(event.id) for event in events],
            saved,
            skipped,
            store_exists=manifest is not None,
            minimum=MIN_NEW_MATCHES,
        )
        if not pending:
            waiting = len(ids_to_build([str(event.id) for event in events], saved, skipped, store_exists=False))
            logger.info("Training tape waits: %d new %s matches, threshold %d", waiting, sport_code, MIN_NEW_MATCHES)
            return False
        chosen = {item for item in pending}
        batch = [event for event in events if str(event.id) in chosen]
        names = list(manifest.get("feature_names") or []) if manifest else None
        logger.info("Building %d %s matches into the training tape", len(batch), sport_code)
        matches, feature_names, skipped_now = builder.build_matches(session, batch, names)
        if manifest is None and len(matches) < 4:
            raise RuntimeError(f"Недостаточно матчей для ленты {sport_code}: {len(matches)}")
        append_store(sport_code, version, matches, feature_names, skipped_now)
        return True


def main() -> None:
    from db.connection import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sport = os.getenv("NEUROBET_DATASET_SPORT", "tennis")
    while True:
        acted = False
        try:
            with SessionLocal() as session:
                acted = refresh_once(session, sport)
        except Exception:
            logger.exception("Training tape refresh failed")
        if not acted:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
