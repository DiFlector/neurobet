"""Script to analyze and purge duplicated synthetic test events in PostgreSQL."""
import os
import sys
from sqlalchemy import create_engine, text

db_url = os.getenv("DATABASE_URL", "postgresql://neurobet:Nb_Ts_9xK2mP5vR8wY1qZ4jL7nC3eA6hD@postgres:5432/neurobet")
engine = create_engine(db_url)

with engine.connect() as conn:
    # 1. Check counts of events
    total_events = conn.execute(text("SELECT count(*) FROM events;")).scalar()
    ext_events = conn.execute(text("SELECT count(*) FROM events WHERE source_event_id LIKE 'ext_%';")).scalar()
    print(f"Total events in DB: {total_events}, synthetic ext_* events: {ext_events}")

    # 2. Check duplicates by (participant_a, participant_b, sport_code)
    dup_groups = conn.execute(text("""
        SELECT participant_a, participant_b, sport_code, count(*) as cnt
        FROM events
        GROUP BY participant_a, participant_b, sport_code
        HAVING count(*) > 1
        ORDER BY count(*) DESC
        LIMIT 10;
    """)).fetchall()
    print(f"Duplicate match groups found: {len(dup_groups)}")
    for d in dup_groups:
        print(f"  {d[0]} vs {d[1]} ({d[2]}): {d[3]} duplicates")

    # 3. Clean up synthetic ext_* duplicates keeping only the most recent one if needed or purging all ext_*
    # First, let's see if any bets or predictions are linked to them
    deleted_preds = conn.execute(text("""
        DELETE FROM model_predictions 
        WHERE event_id IN (SELECT id FROM events WHERE source_event_id LIKE 'ext_%' AND status != 'live');
    """)).rowcount
    deleted_events = conn.execute(text("""
        DELETE FROM events 
        WHERE source_event_id LIKE 'ext_%' AND status != 'live';
    """)).rowcount
    conn.commit()
    print(f"Purged {deleted_events} obsolete synthetic test events and {deleted_preds} predictions.")

    # 4. Check remaining events
    rem_events = conn.execute(text("SELECT count(*) FROM events;")).scalar()
    print(f"Remaining active events in DB: {rem_events}")
