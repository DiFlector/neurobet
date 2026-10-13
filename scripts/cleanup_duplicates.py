"""Mark synthetic fixture events as finished so they leave the live board.

Rows are kept: paper bets and ledger entries may reference sim_evt_* / ext_*.
"""
import os
from sqlalchemy import create_engine, text

db_url = os.getenv(
    "DATABASE_URL",
    "postgresql://neurobet:Nb_Ts_9xK2mP5vR8wY1qZ4jL7nC3eA6hD@postgres:5432/neurobet",
)
engine = create_engine(db_url)

with engine.connect() as conn:
    total_events = conn.execute(text("SELECT count(*) FROM events;")).scalar()
    synthetic_live = conn.execute(text("""
        SELECT count(*) FROM events
        WHERE is_live = true
          AND (
            source_event_id LIKE 'sim_evt_%'
            OR source_event_id LIKE 'ext_%'
            OR source_event_id LIKE 'evt_%'
            OR source_event_id LIKE 'dr_%'
          );
    """)).scalar()
    print(f"Total events in DB: {total_events}, synthetic still live: {synthetic_live}")

    dup_groups = conn.execute(text("""
        SELECT participant_a_name, participant_b_name, sport_code, count(*) as cnt
        FROM events
        GROUP BY participant_a_name, participant_b_name, sport_code
        HAVING count(*) > 1
        ORDER BY count(*) DESC
        LIMIT 10;
    """)).fetchall()
    print(f"Duplicate match groups found: {len(dup_groups)}")
    for row in dup_groups:
        print(f"  {row[0]} vs {row[1]} ({row[2]}): {row[3]} duplicates")

    updated = conn.execute(text("""
        UPDATE events
        SET status = 'finished',
            is_live = false,
            finished_at = COALESCE(finished_at, NOW())
        WHERE is_live = true
          AND (
            source_event_id LIKE 'sim_evt_%'
            OR source_event_id LIKE 'ext_%'
            OR source_event_id LIKE 'evt_%'
            OR source_event_id LIKE 'dr_%'
            OR last_seen_at < NOW() - INTERVAL '12 hours'
          );
    """)).rowcount
    conn.commit()
    print(f"Marked {updated} synthetic live events as finished.")

    remaining_live = conn.execute(text("""
        SELECT count(*) FROM events
        WHERE is_live = true
          AND (
            source_event_id LIKE 'sim_evt_%'
            OR source_event_id LIKE 'ext_%'
            OR source_event_id LIKE 'evt_%'
            OR source_event_id LIKE 'dr_%'
          );
    """)).scalar()
    print(f"Synthetic events still live: {remaining_live}")
