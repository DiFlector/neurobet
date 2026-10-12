"""Multi-sport Dataset Builder: Point-in-time feature extraction strictly on real database events."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from contracts import FeatureVector
from db.models import Event, EventStateSnapshot, OddsSnapshot, TrainingDataset
from features import (
    FeatureBuilder,
    TennisFeatureBuilder,
    FootballFeatureBuilder,
    BasketballFeatureBuilder,
    HockeyFeatureBuilder,
    VolleyballFeatureBuilder,
    registry as feature_registry,
)


class SportDatasetBuilder:
    """
    Multi-sport dataset builder: extracts completed events strictly from real database records.
    Enforces strict point-in-time cutoffs to guarantee zero future data leakage.
    Synthetic fallback is strictly disabled — operates exclusively on real historical and live data.
    """

    def __init__(self, sport_code: str = "tennis", feature_builder: Optional[FeatureBuilder] = None):
        self.sport_code = sport_code.lower()
        if feature_builder is not None:
            self.feature_builder = feature_builder
        else:
            self.feature_builder = feature_registry.get(self.sport_code) or TennisFeatureBuilder()

    def build_from_db(
        self,
        session: Session,
        train_ratio: float = 0.7,
        dataset_name: Optional[str] = None,
        save_dir: str = "/app/models/checkpoints",
        min_samples_required: int = 5,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str], Dict[str, Any]]:
        """
        Extracts completed real matches for this sport from database and constructs chronological train/val splits.
        Never generates synthetic data: raises ValueError if real events are insufficient.
        """
        events_stmt = (
            select(Event)
            .where(Event.sport_code == self.sport_code)
            .where(Event.status.in_(["finished", "completed"]))
            .order_by(Event.scheduled_start_at.asc())
        )
        events = list(session.execute(events_stmt).scalars())

        rows: List[List[float]] = []
        labels: List[int] = []
        feature_names: List[str] = []

        if not events:
            raise ValueError(
                f"В базе данных отсутствуют реальные завершенные события для вида спорта '{self.sport_code}'. "
                f"Генерация синтетических данных отключена: система работает только с реальными данными."
            )

        for ev in events:
            cs = ev.current_score or {}
            winner = ev.metadata_json.get("winner") or ev.metadata_json.get("participant_winner") or cs.get("winner")
            y_label: Optional[int] = None

            if winner:
                y_label = 1 if winner in ("player_a", "team_a", "home", "1", ev.participant_a_name) else 0
            else:
                if self.sport_code in ("tennis", "volleyball"):
                    sa = cs.get("sets_a")
                    sb = cs.get("sets_b")
                    if sa is not None and sb is not None:
                        try:
                            if int(sa) != int(sb):
                                y_label = 1 if int(sa) > int(sb) else 0
                        except Exception:
                            pass
                elif self.sport_code in ("football", "hockey"):
                    sa = cs.get("score_a")
                    sb = cs.get("score_b")
                    if sa is not None and sb is not None:
                        try:
                            if int(sa) != int(sb):
                                y_label = 1 if int(sa) > int(sb) else 0
                        except Exception:
                            pass
                elif self.sport_code == "basketball":
                    sa = cs.get("score_a", cs.get("pts_a"))
                    sb = cs.get("score_b", cs.get("pts_b"))
                    if sa is not None and sb is not None:
                        try:
                            if int(sa) != int(sb):
                                y_label = 1 if int(sa) > int(sb) else 0
                        except Exception:
                            pass

            if y_label is None:
                continue

            # 2. Fetch real snapshots chronologically
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

            if not odds:
                # Without real odds, point-in-time market betting feature cannot be formed
                continue

            # 3. Point-in-time extraction: evaluate features at observed cutoffs
            if states:
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

                    feature_vals = [float(vec.features.get(k, 0.0)) for k in feature_names]
                    rows.append(feature_vals)
                    labels.append(y_label)
            else:
                # Pre-match observation snapshot using real odds snapshot
                cutoff = odds[-1].observed_at
                vec = self.feature_builder.build_features(
                    event_id=str(ev.id),
                    cutoff_time=cutoff,
                    event_snapshots=[],
                    odds_snapshots=odds,
                )

                if not feature_names:
                    feature_names = sorted(list(vec.features.keys()))

                feature_vals = [float(vec.features.get(k, 0.0)) for k in feature_names]
                rows.append(feature_vals)
                labels.append(y_label)

        if len(rows) < min_samples_required:
            raise ValueError(
                f"Недостаточно реальных завершенных событий с котировками в базе данных для вида спорта '{self.sport_code}' "
                f"(найдено {len(rows)} срезов, требуется минимум {min_samples_required}). "
                f"Синтетические данные отключены: дождитесь накопления реальных наблюдений коллектора."
            )

        X = np.asarray(rows, dtype=float)
        y = np.asarray(labels, dtype=int)

        # Chronological time split
        n_samples = len(X)
        split_idx = max(1, int(n_samples * train_ratio))
        if split_idx >= n_samples:
            split_idx = n_samples - 1
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]

        # Save real artifact and register in training_datasets table
        dt_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        name = dataset_name or f"{self.sport_code}_real_dataset_{dt_str}"
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
            sport_code=self.sport_code,
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
            "is_real_data": True,
        }
        return X_train, y_train, X_val, y_val, feature_names, metadata


class TennisDatasetBuilder(SportDatasetBuilder):
    """Backwards-compatible TennisDatasetBuilder."""

    def __init__(self, feature_builder: Optional[TennisFeatureBuilder] = None):
        super().__init__(sport_code="tennis", feature_builder=feature_builder)


def get_dataset_builder(sport_code: str = "tennis") -> SportDatasetBuilder:
    """Factory retrieving SportDatasetBuilder for given sport code."""
    return SportDatasetBuilder(sport_code=sport_code)
