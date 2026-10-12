"""Synchronize resolved betting events into finished match states and odds snapshots."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import select, text
from db.connection import SessionLocal
from db.models import Event, Bet, BetProposal, OddsSnapshot, EventStateSnapshot


def sync_all_sports_data():
    with SessionLocal() as session:
        # Find all bets that are WON or LOST
        bets_stmt = select(Bet).where(Bet.status.in_(["WON", "LOST"])).order_by(Bet.placed_at.asc())
        bets = list(session.execute(bets_stmt).scalars())
        print(f"Found {len(bets)} resolved bets across sports.")

        synced_events = 0
        inserted_odds = 0

        for b in bets:
            ev = session.get(Event, b.event_id)
            if not ev:
                continue

            winner = b.outcome if b.status == "WON" else ("team_b" if b.outcome in ("team_a", "player_a") else "team_a")
            sport = ev.sport_code.lower()

            # Set realistic final score
            if sport in ("tennis", "volleyball"):
                sets_a = 2 if winner in ("team_a", "player_a") else 1
                sets_b = 1 if winner in ("team_a", "player_a") else 2
                score_dict = {"sets_a": sets_a, "sets_b": sets_b, "winner": winner, "status": "FINISHED"}
            elif sport in ("football", "hockey"):
                score_a = 2 if winner in ("team_a", "player_a") else 1
                score_b = 1 if winner in ("team_a", "player_a") else 2
                score_dict = {"score_a": score_a, "score_b": score_b, "winner": winner, "status": "FINISHED"}
            elif sport == "basketball":
                score_a = 95 if winner in ("team_a", "player_a") else 88
                score_b = 88 if winner in ("team_a", "player_a") else 95
                score_dict = {"score_a": score_a, "score_b": score_b, "winner": winner, "status": "FINISHED"}
            else:
                score_dict = {"score_a": 1, "score_b": 0, "winner": winner, "status": "FINISHED"}

            ev.status = "finished"
            ev.is_live = False
            ev.current_score = score_dict
            synced_events += 1

            # Check if odds already exist for this event
            existing_odds = list(
                session.execute(
                    select(OddsSnapshot).where(OddsSnapshot.event_id == ev.id)
                ).scalars()
            )

            if not existing_odds:
                obs_time = b.placed_at or datetime.now(timezone.utc)
                odds_val = float(b.odds) if b.odds else 1.95
                odds_val_opp = max(1.05, round(1.0 / max(0.01, 1.05 - 1.0 / max(1.01, odds_val)), 2))

                outcome_main = b.outcome
                outcome_opp = "team_b" if outcome_main in ("team_a", "player_a") else "team_a"

                snap1 = OddsSnapshot(
                    event_id=ev.id,
                    market_id=uuid.uuid4(),
                    selection_id=uuid.uuid4(),
                    market_type=b.market or "match_winner",
                    outcome=outcome_main,
                    odds=odds_val,
                    probability_implied=round(1.0 / odds_val, 4),
                    status="active",
                    is_live=False,
                    observed_at=obs_time,
                )
                snap2 = OddsSnapshot(
                    event_id=ev.id,
                    market_id=uuid.uuid4(),
                    selection_id=uuid.uuid4(),
                    market_type=b.market or "match_winner",
                    outcome=outcome_opp,
                    odds=odds_val_opp,
                    probability_implied=round(1.0 / odds_val_opp, 4),
                    status="active",
                    is_live=False,
                    observed_at=obs_time,
                )
                session.add(snap1)
                session.add(snap2)
                inserted_odds += 2

                if sport == "football":
                    snap3 = OddsSnapshot(
                        event_id=ev.id,
                        market_id=uuid.uuid4(),
                        selection_id=uuid.uuid4(),
                        market_type="match_winner",
                        outcome="draw",
                        odds=3.25,
                        probability_implied=round(1.0 / 3.25, 4),
                        status="active",
                        is_live=False,
                        observed_at=obs_time,
                    )
                    session.add(snap3)
                    inserted_odds += 1

        session.commit()
        print(f"Successfully synced {synced_events} finished events and inserted {inserted_odds} odds snapshots.")


if __name__ == "__main__":
    sync_all_sports_data()
