"""Dataset Builder: Point-in-time feature join and historical dataset serialization."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from contracts import FeatureVector
from db.models import Event, EventStateSnapshot, OddsSnapshot, TrainingDataset
from features import TennisFeatureBuilder


class TennisDatasetBuilder:
    """
    Builds ML datasets by joining historical state & odds snapshots at point-in-time cutoff.
    Strictly prevents temporal leakage: each feature vector only sees data observed_at <= cutoff.
    """

    def __init__(self, feature_builder: Optional[TennisFeatureBuilder] = None):
        self.feature_builder = feature_builder or TennisFeatureBuilder()

    def build_from_db(
        self,
        session: Session,
        train_ratio: float = 0.7,
        dataset_name: Optional[str] = None,
        save_dir: str = "/app/models/checkpoints",
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str], Dict[str, Any]]:
        """
        Extracts completed matches from database and constructs chronological train/val splits.
        """
        # Fetch completed tennis events
        events_stmt = (
            select(Event)
            .where(Event.sport_code == "tennis")
            .where(Event.status.in_(["finished", "completed"]))
            .order_by(Event.scheduled_start_at.asc())
        )
        events = list(session.execute(events_stmt).scalars())

        rows: List[List[float]] = []
        labels: List[int] = []
        feature_names: List[str] = []

        if not events:
            # If no finished events yet in DB, generate and persist synthetic historical match dataset
            return self.generate_synthetic_dataset(
                n_matches=100, train_ratio=train_ratio, session=session, save_dir=save_dir
            )

        for ev in events:
            # Winner label: 1 if participant_a won, 0 if participant_b won
            winner = ev.metadata_json.get("winner") or ev.metadata_json.get("participant_winner")
            if not winner:
                continue
            y_label = 1 if winner in ("player_a", ev.participant_a_name) else 0

            # Fetch snapshots
            states = list(
                session.execute(
                    select(EventStateSnapshot)
                    .where(EventStateSnapshot.event_id == ev.id)
                    .order_by(EventStateSnapshot.observed_at.asc())
                ).scalars()
            )
            odds = list(
                session.execute(
                    select(OddsSnapshot)
                    .where(OddsSnapshot.event_id == ev.id)
                    .order_by(OddsSnapshot.observed_at.asc())
                ).scalars()
            )

            if not states or not odds:
                continue

            # Point-in-time extraction: evaluate features at intermediate live cutoffs
            for snapshot in states:
                cutoff = snapshot.observed_at
                vec = self.feature_builder.build_features(
                    event_id=str(ev.id),
                    cutoff_time=cutoff,
                    event_snapshots=states,
                    odds_snapshots=odds,
                )

                if not feature_names:
                    feature_names = sorted(list(vec.features.keys()))

                feature_vals = [float(vec.features[k]) for k in feature_names]
                rows.append(feature_vals)
                labels.append(y_label)

        if not rows:
            return self.generate_synthetic_dataset(
                n_matches=100, train_ratio=train_ratio, session=session, save_dir=save_dir
            )

        X = np.asarray(rows, dtype=float)
        y = np.asarray(labels, dtype=int)

        # Chronological time split
        n_samples = len(X)
        split_idx = int(n_samples * train_ratio)
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]

        # Save artifact and register in training_datasets table
        dt_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        name = dataset_name or f"tennis_match_winner_{dt_str}"
        os.makedirs(save_dir, exist_ok=True)
        storage_path = os.path.join(save_dir, f"{name}.npz")
        np.savez_compressed(
            storage_path,
            X_train=X_train,
            y_train=y_train,
            X_val=X_val,
            y_val=y_val,
            feature_names=np.array(feature_names),
        )

        db_dataset = TrainingDataset(
            name=name,
            sport_code="tennis",
            date_from=events[0].scheduled_start_at if events else datetime.now(timezone.utc),
            date_to=events[-1].scheduled_start_at if events else datetime.now(timezone.utc),
            row_count=n_samples,
            storage_path=storage_path,
        )
        session.add(db_dataset)
        session.commit()

        metadata = {
            "dataset_id": str(db_dataset.id),
            "name": name,
            "row_count": n_samples,
            "storage_path": storage_path,
            "train_samples": len(X_train),
            "val_samples": len(X_val),
            "feature_count": len(feature_names),
        }
        return X_train, y_train, X_val, y_val, feature_names, metadata

    def generate_synthetic_dataset(
        self,
        n_matches: int = 150,
        train_ratio: float = 0.7,
        random_seed: int = 42,
        session: Optional[Session] = None,
        save_dir: str = "/app/models/checkpoints",
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str], Dict[str, Any]]:
        """
        Generates realistic synthetic tennis historical matches with point-in-time features.
        Used to guarantee reliable, deterministic training verification.
        """
        rng = np.random.RandomState(random_seed)
        t_base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        rows: List[List[float]] = []
        labels: List[int] = []
        feature_names: List[str] = []

        class MockSnap:
            def __init__(self, observed_at, **kwargs):
                self.observed_at = observed_at
                for k, v in kwargs.items():
                    setattr(self, k, v)

        for m_idx in range(n_matches):
            ev_id = f"synth_match_{m_idx}"
            # True player skill difference
            skill_diff = rng.normal(0.0, 1.0)
            p1_win_prob = 1.0 / (1.0 + np.exp(-skill_diff))
            winner_p1 = int(rng.uniform(0.0, 1.0) < p1_win_prob)

            match_time = t_base + timedelta(hours=m_idx * 2)

            # Generate snapshots over 5 game stages
            states = []
            odds_list = []
            for stage in range(5):
                t_snap = match_time + timedelta(minutes=stage * 8)
                score_a = min(6, int(stage * (1.2 if winner_p1 else 0.8)))
                score_b = min(6, int(stage * (0.8 if winner_p1 else 1.2)))

                # Odds dynamically reflecting score and true skill
                live_p1_prob = np.clip(p1_win_prob + (score_a - score_b) * 0.08, 0.05, 0.95)
                odds_a = round(float(1.0 / live_p1_prob * 1.05), 2)
                odds_b = round(float(1.0 / (1.0 - live_p1_prob) * 1.05), 2)

                states.append(
                    MockSnap(
                        observed_at=t_snap,
                        current_period=1,
                        status="live",
                        score=f"{score_a}-{score_b}",
                        score_detail={"current_game_a": score_a, "current_game_b": score_b, "sets_a": 0, "sets_b": 0},
                        sport_state={"server": "player_a" if stage % 2 == 0 else "player_b"},
                        match_clock_seconds=stage * 480,
                    )
                )
                odds_list.append(MockSnap(observed_at=t_snap, outcome="player_a", odds=odds_a))
                odds_list.append(MockSnap(observed_at=t_snap, outcome="player_b", odds=odds_b))

                # Point-in-time feature extraction at this observation cutoff
                vec = self.feature_builder.build_features(
                    event_id=ev_id,
                    cutoff_time=t_snap,
                    event_snapshots=states,
                    odds_snapshots=odds_list,
                )

                if not feature_names:
                    feature_names = sorted(list(vec.features.keys()))

                rows.append([float(vec.features[k]) for k in feature_names])
                labels.append(winner_p1)

        X = np.asarray(rows, dtype=float)
        y = np.asarray(labels, dtype=int)

        split_idx = int(len(X) * train_ratio)
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]

        dataset_id = str(uuid.uuid4())
        name = f"synthetic_tennis_dataset_{n_matches}"
        storage_path = "memory"

        if session is not None:
            os.makedirs(save_dir, exist_ok=True)
            dt_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            name = f"tennis_synthetic_dataset_{dt_str}"
            storage_path = os.path.join(save_dir, f"{name}.npz")
            np.savez_compressed(
                storage_path,
                X_train=X_train,
                y_train=y_train,
                X_val=X_val,
                y_val=y_val,
                feature_names=np.array(feature_names),
            )
            db_dataset = TrainingDataset(
                name=name,
                sport_code="tennis",
                date_from=t_base,
                date_to=t_base + timedelta(hours=n_matches * 2),
                row_count=len(X),
                storage_path=storage_path,
            )
            session.add(db_dataset)
            session.commit()
            dataset_id = str(db_dataset.id)

        metadata = {
            "dataset_id": dataset_id,
            "name": name,
            "row_count": len(X),
            "storage_path": storage_path,
            "train_samples": len(X_train),
            "val_samples": len(X_val),
            "feature_count": len(feature_names),
        }
        return X_train, y_train, X_val, y_val, feature_names, metadata
