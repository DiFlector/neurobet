"""Multi-sport Dataset Builder: Point-in-time feature extraction strictly on real database events."""

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from sklearn.base import clone
from sqlalchemy import select
from sqlalchemy.orm import Session

from contracts import FeatureVector
from db.models import Event, EventStateSnapshot, OddsSnapshot, TrainingDataset
logger = logging.getLogger("neural.dataset")

from features import FeatureBuilder, TennisFeatureBuilder, registry as feature_registry

from .commit import MIN_COMMIT_EDGE, committed_rows


@dataclass
class MatchSnapshots:
    """Chronological feature rows of one finished match, sharing one final-winner label."""

    rows: List[List[float]]
    label: int


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
            self.feature_builder = feature_registry.get(self.sport_code)
            if self.feature_builder is None:
                raise ValueError(f"No feature builder registered for sport '{self.sport_code}'")

    def build_from_db(
        self,
        session: Session,
        estimator: Any,
        train_ratio: float = 0.7,
        dataset_name: Optional[str] = None,
        save_dir: str = "/app/models/checkpoints",
        min_samples_required: int = 5,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str], Dict[str, Any]]:
        """
        Extracts completed real matches and keeps one row per match.

        A provisional copy of the estimator, fit only on the first snapshot of each
        training match, replays the match in time. The saved row is the first snapshot
        whose edge reaches MIN_COMMIT_EDGE. A match that never reaches it is omitted.
        The label stays the final winner. Train and validation are split by match.
        """
        events_stmt = (
            select(Event)
            .where(Event.sport_code == self.sport_code)
            .where(Event.status.in_(["finished", "completed"]))
            .where(Event.source_event_id.op("~")(r"^[0-9]+$"))
            .order_by(Event.scheduled_start_at.asc())
        )
        events = list(session.execute(events_stmt).scalars())

        matches: List[MatchSnapshots] = []
        feature_names: List[str] = []
        snapshot_count = 0

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

            # Every snapshot stays a candidate. The side is chosen later, once per match.
            match_rows: List[List[float]] = []
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

                    match_rows.append([float(vec.features.get(k, 0.0)) for k in feature_names])
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

                match_rows.append([float(vec.features.get(k, 0.0)) for k in feature_names])

            if not match_rows:
                continue
            matches.append(MatchSnapshots(rows=match_rows, label=y_label))
            snapshot_count += len(match_rows)
            if len(matches) == 1 or len(matches) % 10 == 0:
                logger.info(
                    "Dataset %s: %d matches, %d snapshots",
                    self.sport_code,
                    len(matches),
                    snapshot_count,
                )

        if estimator is None:
            raise ValueError("Обучение требует модель, чтобы выбрать один срез матча с перевесом не меньше 2%.")

        if len(matches) < min_samples_required + 1:
            raise ValueError(
                f"Недостаточно реальных завершенных событий с котировками в базе данных для вида спорта '{self.sport_code}' "
                f"(найдено {len(matches)} матчей, требуется минимум {min_samples_required + 1}). "
                f"Синтетические данные отключены: дождитесь накопления реальных наблюдений коллектора."
            )

        # Split by match, then pick the first snapshot where this model would commit a side.
        split_idx = max(1, int(len(matches) * train_ratio))
        if split_idx >= len(matches):
            split_idx = len(matches) - 1
        train_matches = matches[:split_idx]
        val_matches = matches[split_idx:]

        provisional = clone(estimator)
        first_rows = np.asarray([match.rows[0] for match in train_matches], dtype=float)
        first_labels = np.asarray([match.label for match in train_matches], dtype=int)
        if len(set(first_labels.tolist())) < 2:
            raise ValueError(
                f"На первых срезах матчей '{self.sport_code}' есть только один класс победителя, "
                f"поэтому нельзя выбрать сторону."
            )
        provisional.fit(first_rows, first_labels)

        def probabilities_for_match(match_rows: List[List[float]]):
            probs = provisional.predict_proba(np.asarray(match_rows, dtype=float))
            return [(float(row[1]), float(row[0])) for row in probs]

        train_rows, train_labels = committed_rows(
            [(match.rows, match.label) for match in train_matches],
            probabilities_for_match,
            feature_names,
        )
        val_rows, val_labels = committed_rows(
            [(match.rows, match.label) for match in val_matches],
            probabilities_for_match,
            feature_names,
        )
        if len(train_rows) < min_samples_required or not val_rows:
            raise ValueError(
                f"После правила одного выбора на матч для '{self.sport_code}' осталось "
                f"{len(train_rows)} обучающих и {len(val_rows)} проверочных матчей "
                f"(нужно минимум {min_samples_required} и 1). "
                f"Матч попадает в выборку только в первый момент, когда перевес не меньше {MIN_COMMIT_EDGE:.0%}."
            )

        X_train = np.asarray(train_rows, dtype=float)
        y_train = np.asarray(train_labels, dtype=int)
        X_val = np.asarray(val_rows, dtype=float)
        y_val = np.asarray(val_labels, dtype=int)
        n_samples = len(train_rows) + len(val_rows)

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
            "match_count": len(matches),
            "snapshot_count": snapshot_count,
            "committed_matches": n_samples,
            "min_commit_edge": MIN_COMMIT_EDGE,
            "one_decision_per_match": True,
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
