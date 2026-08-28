import copy
import json
import random
import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import lightgbm as lgb

from app.config import MODEL_DIR
from app.neuralbet import bankroll
from app.neuralbet.context import (
    NUM_SPORTS, NUM_MARKET_FAMILIES, sport_index, market_family_index,
    TEAM_HASH_BUCKETS, team_index,
)
from neurobet_filters import MIN_BET_COEFF, MAX_BET_COEFF, MIN_BET_EDGE_PCT, in_bet_band
from .checkpoint_gate import decide_online_checkpoint
from neurobet_features import (
    OVERROUND_EXPECTED_SIZE,
    GRU_INPUT_DIM,
    KB_CONTEXT_DIM,
    LGB_CATEGORICAL_FEATURES,
    LGB_FEATURE_NAMES,
    build_gru_sequence,
    build_model_input,
    kb_context_vector,
    lgb_feature_row,
    overround_group_key,
)

logger = logging.getLogger("ai_service_model")

# Minimum market_weight after tuning / checkpoint load — the only backtest-positive
# day in production had market_weight≈0.78; floor keeps the blend from drifting
# back to pure-model when val slices lie.
MARKET_WEIGHT_FLOOR = float(os.getenv("NEURALBET_MARKET_WEIGHT_FLOOR", "0.70"))

PYTORCH_WEIGHTS_PATH = os.path.join(MODEL_DIR, "pytorch_gru.pt")
LIGHTGBM_MODEL_PATH = os.path.join(MODEL_DIR, "lightgbm_model.txt")
LIGHTGBM_META_PATH = os.path.join(MODEL_DIR, "lightgbm_meta.json")
LGB_SEED = int(os.getenv("NEURALBET_LGB_SEED", "42"))
LGB_BRIER_TOLERANCE = float(os.getenv("NEURALBET_LGB_BRIER_TOLERANCE", "0.001"))

# Hardware has headroom, so the sequence encoder is sized generously: two GRU layers,
# 64 hidden units. (Was 1 layer / 32 units when the only signal it saw was the odds
# curve — now that score_diff is a real per-step feature (see _build_sequence) instead
# of a broadcast constant, the extra capacity is there to actually use it.)
HIDDEN_DIM = int(os.getenv("NEURALBET_HIDDEN_DIM", "64"))
GRU_LAYERS = int(os.getenv("NEURALBET_GRU_LAYERS", "2"))

# Online-training epoch budget. With mini-batches + a held-out validation split (see
# pipeline.py), more epochs are just "try harder," not "memorize harder" — early
# stopping (best-val-epoch selection below) keeps it honest. Ceiling raised 60 -> 200
# alongside TRAIN_BATCH_TOTAL (pipeline.py) — early stopping already governs how many
# actually run (observed landing around 15-25 with the old 300-sample batches), this
# just removes an artificial cap for when a bigger batch needs more passes to converge.
MAX_EPOCHS = int(os.getenv("NEURALBET_MAX_EPOCHS", "200"))
EARLY_STOP_PATIENCE = int(os.getenv("NEURALBET_EARLY_STOP_PATIENCE", "10"))
# A finished online pass only keeps its weights if they beat the incoming model on
# this pass's val split. The incoming weights ARE the last accepted checkpoint, so
# this is the like-for-like comparison; a second gate against the checkpoint's
# *recorded* val_loss (the admin-chart number) was removed 2026-08-19 — that number
# was measured on a different val split with a different win/loss mix, and val_loss
# is not comparable across splits (observed: a 0.1657 recorded on an 83.7%-hit-rate
# split rejected 40 consecutive passes whose fresh splits landed 0.20-0.37, freezing
# the model entirely). Same epsilon as the per-epoch check.
# 64 -> 128 -> 256: fewer, larger mini-batches per epoch use the CPU's vectorized
# matmuls more efficiently (more work per Python-level loop iteration) — meaningful as
# each training pass's sample count (pipeline.TRAIN_BATCH_TOTAL) has grown; 256 keeps
# minibatch count per epoch reasonable (~40 at a 10000-sample pass) without the batch
# getting so large a single gradient step stops responding to individual examples.
BATCH_SIZE = int(os.getenv("NEURALBET_BATCH_SIZE", "256"))
# 1e-3 → 1e-4 → 5e-5: from-scratch rates overfit each online batch (best_epoch=1,
# val_loss rising from epoch 1, checkpoint reject streak). Online fine-tuning of an
# already-converged GRU needs a smaller step so later epochs can still improve val.
LEARNING_RATE = float(os.getenv("NEURALBET_LEARNING_RATE", "5e-5"))
GRAD_CLIP_NORM = 1.0

# Weight on the decision-head loss (the bet/no-bet verdict) relative to the win-head BCE.
# 1.0 — same order of magnitude as bce_loss, so early training doesn't let one head starve
# the other of gradient.
DECISION_LOSS_WEIGHT = float(os.getenv("NEURALBET_DECISION_LOSS_WEIGHT", "0.0"))
# Cost-sensitive verdict BCE: vestigial when DECISION_LOSS_WEIGHT=0 (objective B).
DECISION_POS_WEIGHT_CAP = float(os.getenv("NEURALBET_DECISION_POS_WEIGHT_CAP", "1.0"))
PAIRED_MARKET_LOSS_WEIGHT = float(os.getenv("NEURALBET_PAIRED_MARKET_LOSS_WEIGHT", "0.15"))
# Checkpoint gate primary metric is mean in-band Brier (not raw win-BCE). Reject if
# attempted Brier is worse than last accepted by more than this absolute slack.
# Brier sits ~0.18–0.25; 0.02 ≈ one modest regression step (same env key as before).
CHECKPOINT_VAL_FLOOR_TOLERANCE = float(os.getenv("NEURALBET_CHECKPOINT_VAL_FLOOR_TOLERANCE", "0.02"))
# Must beat incoming Brier by this margin to accept; within ±eps, win-BCE can break ties.
CHECKPOINT_BRIER_EPS = float(os.getenv("NEURALBET_CHECKPOINT_BRIER_EPS", "1e-4"))
# Reject online passes that memorized the batch in 1–2 epochs (val Brier often
# still "improves" on a stale pin). 0 disables.
CHECKPOINT_MIN_BEST_EPOCH = int(os.getenv("NEURALBET_CHECKPOINT_MIN_BEST_EPOCH", "3"))
BRIER_LOSS_WEIGHT = float(os.getenv("NEURALBET_BRIER_LOSS_WEIGHT", "1.0"))
CHECKPOINT_IN_BAND_ONLY = os.getenv("NEURALBET_CHECKPOINT_IN_BAND_ONLY", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
THRESHOLD_BOOTSTRAP_SAMPLES = int(os.getenv("NEURALBET_THRESHOLD_BOOTSTRAP_SAMPLES", "200"))

def _select_device() -> torch.device:
    """cuda if the driver is visible, unless NEURALBET_DEVICE=cpu (or cuda is forced)."""
    choice = os.getenv("NEURALBET_DEVICE", "auto").strip().lower()
    if choice in ("cpu", "none", "off"):
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if choice == "cuda":
        logger.warning("NEURALBET_DEVICE=cuda but CUDA is not available — using CPU")
    return torch.device("cpu")


DEVICE = _select_device()


def _tensor(data, dtype=torch.float32) -> torch.Tensor:
    return torch.tensor(data, dtype=dtype, device=DEVICE)


def _optimizer_to_device(optimizer: torch.optim.Optimizer, device: torch.device) -> None:
    for state in optimizer.state.values():
        for key, value in list(state.items()):
            if torch.is_tensor(value):
                state[key] = value.to(device)


# On CPU, 16 intra-op threads saturates this host without starving Postgres.
# On CUDA the GRU barely uses the CPU — keep a few threads for dataloading.
_default_threads = "4" if DEVICE.type == "cuda" else "16"
torch.set_num_threads(int(os.getenv("NEURALBET_TORCH_THREADS", _default_threads)))

# How many candidate bets make up one "round" for the bankroll-allocation loss —
# roughly how many live outcomes the bot might be weighing at once in a real cycle.
ROUND_SIZE = 20


SPORT_EMB_DIM = 8
MARKET_EMB_DIM = 6
TEAM_EMB_DIM = 6
# Re-export: must match neurobet_features.TEAM_STATS_DIM / kb_context_vector length.


class OddsTrajectoryGRU(nn.Module):
    """
    PyTorch GRU sequence model over odds-trajectory + live-score steps, conditioned on
    which sport, which market family, and which two teams/players (team1_idx/team2_idx —
    see app/neuralbet/context.py's team_index) the bet is on, plus a fixed-length
    team-stats knowledge-base vector (rolling scored/conceded/WR/H2H — see
    neurobet_features.player_stats / kb_context_vector).
        Sequence input: (batch, seq_len=10, input_dim=GRU_INPUT_DIM) — per step built by
        neurobet_features.build_gru_sequence so live / train / backtest cannot drift.
        Sport/market/team embeddings and the KB context vector are concatenated onto the
        GRU's final hidden state.
    Output: 4 raw logits per sample — [win_logit, decision_logit, stake_logit,
    exposure_logit]:
      - win_logit: sigmoid() gives the win-probability estimate — blended with
        LightGBM + market, then calibrated. Live / backtest verdict (objective B)
        is EV-based: stake iff calibrated_p * coeff - 1 ≥ MIN_BET_EDGE.
      - decision_logit: kept for checkpoint shape compatibility. Residual-edge
        training is off by default (NEURALBET_DECISION_LOSS_WEIGHT=0); inference
        does not use this head for predicted_win.
      - stake_logit: sigmoid() scales Kelly size in bankroll.allocate().
      - exposure_logit: vestigial — checkpoint-compatible only.
    No activation is applied here; callers apply sigmoid as appropriate so the
    raw logits can feed BCEWithLogitsLoss directly.
    """
    def __init__(self, input_dim: int = GRU_INPUT_DIM, hidden_dim: int = HIDDEN_DIM, num_layers: int = GRU_LAYERS):
        super(OddsTrajectoryGRU, self).__init__()
        self.hidden_dim = hidden_dim
        self.gru = nn.GRU(
            input_dim, hidden_dim, num_layers, batch_first=True,
            dropout=0.1 if num_layers > 1 else 0.0,
        )
        self.sport_embed = nn.Embedding(NUM_SPORTS, SPORT_EMB_DIM)
        self.market_embed = nn.Embedding(NUM_MARKET_FAMILIES, MARKET_EMB_DIM)
        # Shared table for both slots (not two separate embeddings) — "this team plays
        # well as an underdog" should mean the same thing whether that team happens to
        # be sitting in the team_1 or team_2 column for a given match.
        self.team_embed = nn.Embedding(TEAM_HASH_BUCKETS, TEAM_EMB_DIM)
        self.fc1 = nn.Linear(
            hidden_dim + SPORT_EMB_DIM + MARKET_EMB_DIM + 2 * TEAM_EMB_DIM + KB_CONTEXT_DIM,
            32,
        )
        self.relu = nn.ReLU()
        self.head = nn.Linear(32, 4)

    def forward(
        self, x: torch.Tensor, sport_idx: torch.Tensor, market_idx: torch.Tensor,
        team1_idx: torch.Tensor, team2_idx: torch.Tensor,
        kb_ctx: torch.Tensor,
    ) -> torch.Tensor:
        out, _ = self.gru(x)
        last_step = out[:, -1, :]
        ctx = torch.cat([
            last_step, self.sport_embed(sport_idx), self.market_embed(market_idx),
            self.team_embed(team1_idx), self.team_embed(team2_idx),
            kb_ctx,
        ], dim=-1)
        h = self.relu(self.fc1(ctx))
        return self.head(h)


def _gru_seq(view_or_pairs, sport_path: Optional[str] = None) -> List[List[float]]:
    """GRU tensor rows from a prepared view dict or a raw step_pairs list."""
    if isinstance(view_or_pairs, dict):
        line = view_or_pairs.get("total_line")
        tl = float(line) if line else None
        if tl is not None and tl <= 0:
            tl = None
        return build_gru_sequence(
            view_or_pairs.get("step_pairs") or [],
            view_or_pairs.get("sport_path"),
            total_line=tl,
            period_index=int(view_or_pairs.get("period_index") or 0),
            overround=view_or_pairs.get("overround"),
        )
    return build_gru_sequence(view_or_pairs or [], sport_path)


# Floor on the decision threshold after the residual-head change: 0.5 means predicted
# edge 0. Anything below that is "the market is too high, do not bet."
VALUE_THRESHOLD_FLOOR = 0.50
# Per-sport *minimum* decision_threshold. The tuner may raise a sport above this
# (and EMA-smooth as usual) but cannot ease it back below — live inference and
# backtest both go through sport_threshold() / the floors-applied dict.
# Football: 40k backtest 19 Aug 2026 put −4.5% ROI on 98 football bets at the
# then-global ~0.56 cutoff, while table tennis on the same run was +9.4%. 0.62
# is three grid steps more selective than that losing cutoff without a full ban.
SPORT_THRESHOLD_FLOORS: Dict[str, float] = {
    "Футбол": float(os.getenv("NEURALBET_FOOTBALL_THRESHOLD_FLOOR", "0.62")),
}


def _sport_threshold_floor(sport: Optional[str]) -> float:
    if not sport:
        return VALUE_THRESHOLD_FLOOR
    return max(VALUE_THRESHOLD_FLOOR, SPORT_THRESHOLD_FLOORS.get(sport, VALUE_THRESHOLD_FLOOR))


def _market_prob_tensor(coefficients: torch.Tensor) -> torch.Tensor:
    return torch.clamp(1.0 / torch.clamp(coefficients, min=1.01), min=0.01, max=0.99)


def _decision_confidence(decision_logits: torch.Tensor) -> torch.Tensor:
    """Map residual-edge tanh to [0, 1] (legacy head; not used for live verdict)."""
    return (torch.tanh(decision_logits) + 1.0) * 0.5


def _ev_verdict_mask(
    win_probs: torch.Tensor,
    coeffs: torch.Tensor,
    *,
    min_edge_pct: float = MIN_BET_EDGE_PCT,
) -> torch.Tensor:
    """Hard bet mask: expected value vs book ≥ min edge (fractional)."""
    edge = win_probs * coeffs - 1.0
    return (edge >= (min_edge_pct / 100.0)).float()


def _checkpoint_val_prepared(prepared: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validation slice for checkpoint gate — live coefficient band only when enabled."""
    if not CHECKPOINT_IN_BAND_ONLY:
        return prepared
    return [p for p in prepared if in_bet_band(float(p.get("coefficient") or 0))]


class NeuralBetEnsemble:
    """
    Ensemble model combining PyTorch GRU sequence model and LightGBM GBDT.
    Saves and loads weight checkpoints from /app/data/models/ persistent volume.
    """
    def __init__(self):
        self._reset_state()
        # Load weights if checkpoint exists — reset() reuses this same state-init logic
        # but deliberately skips this call (a reset means *discarding* whatever's on
        # disk, not reloading it right back).
        self.load_checkpoints()
        if DEVICE.type == "cuda":
            logger.info(
                "PyTorch GRU on CUDA: %s (%.1f GiB)",
                torch.cuda.get_device_name(0),
                torch.cuda.get_device_properties(0).total_memory / (1024 ** 3),
            )
        else:
            logger.info("PyTorch GRU on CPU (CUDA not visible)")

    def reload_checkpoints(self) -> None:
        """Hot-reload weights from MODEL_DIR after admin activates a registry model."""
        self._reset_state()
        self.load_checkpoints()

    def _reset_state(self):
        """
        Everything that makes this a "fresh, never-trained" ensemble — shared by
        __init__ (cold container start, then load_checkpoints() may overwrite it with a
        saved state) and reset() (admin-triggered wipe, which deliberately does NOT
        reload afterward). Keeping this in one place means the two can never drift apart
        on what "untrained" actually means.
        """
        self.pytorch_model = OddsTrajectoryGRU().to(DEVICE)
        self.pytorch_optimizer = optim.AdamW(self.pytorch_model.parameters(), lr=LEARNING_RATE)
        self.is_trained = False

        # Weight on pytorch_prob and on the raw bookmaker-implied probability (1/coeff)
        # in the blended win probability (LightGBM gets whatever's left:
        # 1 - blend_weight - market_weight), and the cutoff on the decision head's
        # residual-edge mapped to [0, 1] ((tanh+1)/2): 0.5 = no edge, above = bet.
        # be fixed constants picked once and never revisited; tune_ensemble() re-picks
        # them against held-out validation data periodically (see pipeline.py), so
        # these are just the cold-start defaults before the first tune runs.
        # market_weight anchors the blend on the bookmaker's own price: when the
        # model's Brier score is worse than the market's (observed in production — see
        # the neurobets stats page), the grid search in tune_ensemble naturally pushes
        # weight onto this term instead of onto an undertrained model.
        self.blend_weight = float(os.getenv("NEURALBET_BLEND_WEIGHT_INIT", "0.35"))
        self.market_weight = float(os.getenv("NEURALBET_MARKET_WEIGHT_INIT", "0.0"))
        self.decision_threshold = float(os.getenv("NEURALBET_DECISION_THRESHOLD_INIT", "0.52"))

        # Per-sport overrides of decision_threshold, keyed by top-level sport (same
        # string calibration.py/backtest.py already group by) — tuned in tune_ensemble()
        # whenever a sport's val slice clears MIN_THRESHOLD_BETS_PER_SPORT, since a
        # single global threshold can't be optimal for every sport at once. Sports in
        # SPORT_THRESHOLD_FLOORS also get a minimum cutoff even before they earn a
        # tuned value (football, after a hold-out ROI leak at the global threshold).
        self.sport_decision_thresholds: Dict[str, float] = {}
        self._apply_sport_threshold_floors()
        self._apply_market_weight_floor()

        # Real gradient-boosted classifier, fit on actual resolved bets (see
        # train_lightgbm). None until the first successful training pass — until then
        # predict_single() falls back to the odds-implied heuristic.
        self.lgb_model: Any = None
        self.lgb_trained = False
        self.lgb_last_val_brier: Optional[float] = None
        self.lgb_last_accepted_at: Optional[str] = None
        self.lgb_newest_finished_at: Optional[str] = None
        # Last *accepted* in-band val Brier — floor gate compares against this.
        # Field name kept for checkpoint JSON compatibility (was win-BCE historically).
        self.last_accepted_val_loss: Optional[float] = None
        # Identity of the val pin that last_accepted_val_loss was measured on.
        # Recovery across a pin refresh is invalid (different yardstick).
        self.last_accepted_val_pin_id: Optional[str] = None
        # Cold-start streams many chunks as one global epoch. This snapshot is the
        # epoch-start baseline; chunks update the in-memory model without gating, and
        # only the complete archive pass is accepted/restored as one checkpoint.
        self._checkpoint_window_state: Optional[
            Tuple[Dict[str, Any], Dict[str, Any]]
        ] = None
        self._checkpoint_window_val_loss: Optional[float] = None  # pinned incoming Brier
        self._checkpoint_window_win_bce: Optional[float] = None

    def reset(self) -> None:
        """
        Admin-triggered "reset neural network": discards the live PyTorch weights,
        optimizer momentum, and LightGBM booster, and puts blend_weight/market_weight/
        decision_threshold back to their cold-start defaults — then deletes both
        on-disk checkpoints so a container restart doesn't silently reload the very
        state this just threw away. Deliberately does NOT touch finished_bets/
        finished_events: the resolved-bet archive (the expensive-to-recreate part) is
        left alone on purpose, so the caller (pipeline.reset_neural_network) can zero
        trained_count on it and let training start over from that same existing history,
        instead of needing weeks of fresh matches to rebuild a dataset that was already
        sitting there. pipeline.reset_neural_network then starts a cold-start walk of
        that archive (full train-pool passes, from-scratch LR, checkpoint gate off)
        instead of jumping straight into the online 10k fine-tune loop.
        """
        self._reset_state()
        for path in (PYTORCH_WEIGHTS_PATH, LIGHTGBM_MODEL_PATH, LIGHTGBM_META_PATH):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                logger.error(f"Error removing checkpoint {path} during reset: {e}")

    def _apply_sport_threshold_floors(self) -> None:
        """Ensure every sport in SPORT_THRESHOLD_FLOORS has a stored cutoff at least
        as high as its floor. Mutates sport_decision_thresholds in place so a
        backtest snapshot of the dict (see backtest.py) sees the same values live
        inference uses via sport_threshold()."""
        for sport, floor in SPORT_THRESHOLD_FLOORS.items():
            current = self.sport_decision_thresholds.get(sport)
            if current is None or current < floor:
                self.sport_decision_thresholds[sport] = floor

    def _apply_market_weight_floor(self) -> None:
        """Raise market_weight to MARKET_WEIGHT_FLOOR, taking the deficit from
        blend_weight first so lgb_weight = 1 - blend - market stays non-negative."""
        floor = MARKET_WEIGHT_FLOOR
        if self.market_weight >= floor:
            return
        need = floor - self.market_weight
        self.market_weight = floor
        take = min(need, self.blend_weight)
        self.blend_weight = max(0.0, self.blend_weight - take)

    def sport_threshold(self, sport: Optional[str]) -> float:
        """The decision-head cutoff to use for this sport: its own tuned value if
        tune_ensemble has ever seen enough validation bets for it, otherwise the global
        decision_threshold — then raised to that sport's SPORT_THRESHOLD_FLOORS entry
        if one exists. Single source of truth for this lookup — every caller
        (pipeline.py's live inference, backtest.py's current_pred) goes through this
        instead of reading decision_threshold directly, so the two can never drift out
        of sync on which threshold a given sport actually uses."""
        floor = _sport_threshold_floor(sport)
        if sport is None:
            return max(floor, self.decision_threshold)
        return max(
            floor,
            self.sport_decision_thresholds.get(sport, self.decision_threshold),
        )

    def save_checkpoints(self, extra: Optional[Dict[str, Any]] = None):
        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            self._apply_sport_threshold_floors()
            payload = {
                "model_state": self.pytorch_model.state_dict(),
                "optimizer_state": self.pytorch_optimizer.state_dict(),
                "blend_weight": self.blend_weight,
                "market_weight": self.market_weight,
                "decision_threshold": self.decision_threshold,
                "sport_decision_thresholds": self.sport_decision_thresholds,
            }
            if extra:
                payload.update(extra)
            if self.last_accepted_val_loss is not None:
                payload["last_accepted_val_loss"] = self.last_accepted_val_loss
            if self.last_accepted_val_pin_id is not None:
                payload["last_accepted_val_pin_id"] = self.last_accepted_val_pin_id
            torch.save(payload, PYTORCH_WEIGHTS_PATH)
            logger.info(f"Saved PyTorch model weights checkpoint to {PYTORCH_WEIGHTS_PATH}")
            try:
                from app.neuralbet import model_registry
                model_registry.bootstrap_legacy_if_needed()
            except Exception as bootstrap_err:
                logger.warning("Registry bootstrap after checkpoint save failed: %s", bootstrap_err)
        except Exception as e:
            logger.error(f"Error saving model weights: {e}")

    def save_ensemble(self) -> bool:
        """Write blend/market/decision thresholds onto the existing GRU checkpoint
        without replacing model_state. tune_ensemble runs more often than an accepted
        train_online pass; if we only persisted those scalars together with a new GRU
        file, a restart after a reject-storm rolled blend/threshold back to whatever
        the last accepted cold-start checkpoint carried (seen in production 19 Aug 2026:
        live 0.06/0.526 vanished, init 0.35/0.52 came back). Returns True if the file
        was updated."""
        self._apply_sport_threshold_floors()
        ensemble_fields = {
            "blend_weight": self.blend_weight,
            "market_weight": self.market_weight,
            "decision_threshold": self.decision_threshold,
            "sport_decision_thresholds": dict(self.sport_decision_thresholds),
        }
        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            if not os.path.exists(PYTORCH_WEIGHTS_PATH):
                # No GRU file yet — a full save would dump random-init weights as if
                # they were a real checkpoint. Leave the scalars in memory; the first
                # accepted train_online pass writes both together.
                logger.info(
                    "Ensemble weights not persisted — no GRU checkpoint on disk yet."
                )
                return False
            blob = torch.load(PYTORCH_WEIGHTS_PATH, map_location="cpu")
            if not isinstance(blob, dict) or "model_state" not in blob:
                # Bare state_dict from before ensemble fields existed. Full save
                # keeps the GRU currently in memory (the accepted/restored one) and
                # adds the scalars.
                self.save_checkpoints()
                return True
            blob.update(ensemble_fields)
            tmp_path = PYTORCH_WEIGHTS_PATH + ".tmp"
            torch.save(blob, tmp_path)
            os.replace(tmp_path, PYTORCH_WEIGHTS_PATH)
            logger.info(f"Saved ensemble weights to {PYTORCH_WEIGHTS_PATH}")
            return True
        except Exception as e:
            logger.error(f"Error saving ensemble weights: {e}")
            return False

    def _persist_checkpoint_meta(self) -> None:
        """Write last-accepted floor + val-pin id without replacing GRU weights."""
        if not os.path.exists(PYTORCH_WEIGHTS_PATH):
            return
        try:
            blob = torch.load(PYTORCH_WEIGHTS_PATH, map_location="cpu")
            if not isinstance(blob, dict) or "model_state" not in blob:
                return
            if self.last_accepted_val_loss is not None:
                blob["last_accepted_val_loss"] = self.last_accepted_val_loss
            if self.last_accepted_val_pin_id is not None:
                blob["last_accepted_val_pin_id"] = self.last_accepted_val_pin_id
            tmp_path = PYTORCH_WEIGHTS_PATH + ".tmp"
            torch.save(blob, tmp_path)
            os.replace(tmp_path, PYTORCH_WEIGHTS_PATH)
        except Exception as e:
            logger.error(f"Error persisting checkpoint floor meta: {e}")

    def _same_val_pin(
        self, val_pin_id: Optional[str], val_pin_changed: bool,
    ) -> bool:
        if val_pin_changed:
            return False
        if not val_pin_id or not self.last_accepted_val_pin_id:
            return True
        return val_pin_id == self.last_accepted_val_pin_id

    def _rebase_val_pin_floor(
        self, incoming_brier: Optional[float], val_pin_id: Optional[str],
    ) -> None:
        old = self.last_accepted_val_loss
        if incoming_brier is not None:
            self.last_accepted_val_loss = float(incoming_brier)
        if val_pin_id:
            self.last_accepted_val_pin_id = val_pin_id
        self._persist_checkpoint_meta()
        logger.info(
            "Val pin changed — last-accepted Brier floor rebased "
            f"{None if old is None else f'{old:.4f}'} → "
            f"{None if self.last_accepted_val_loss is None else f'{self.last_accepted_val_loss:.4f}'} "
            f"on pin {val_pin_id}; weights unchanged."
        )

    def _bind_val_pin_id(self, val_pin_id: Optional[str]) -> None:
        if not val_pin_id or self.last_accepted_val_pin_id is not None:
            return
        self.last_accepted_val_pin_id = val_pin_id
        self._persist_checkpoint_meta()

    def _snapshot_train_state(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return (
            copy.deepcopy(self.pytorch_model.state_dict()),
            copy.deepcopy(self.pytorch_optimizer.state_dict()),
        )

    def _restore_train_state(self, model_state: Dict[str, Any], optim_state: Dict[str, Any]) -> None:
        self.pytorch_model.load_state_dict(model_state)
        try:
            self.pytorch_optimizer.load_state_dict(optim_state)
            for group in self.pytorch_optimizer.param_groups:
                group["lr"] = LEARNING_RATE
        except Exception:
            pass

    @property
    def checkpoint_window_active(self) -> bool:
        return self._checkpoint_window_state is not None

    def begin_checkpoint_window(
        self, val_data: List[Dict[str, Any]],
    ) -> Optional[float]:
        """Pin the model/optimizer baseline for one streaming cold-start epoch."""
        bankroll.reset_account("training")
        if self._checkpoint_window_state is not None:
            return self._checkpoint_window_val_loss
        prepared_val = [
            p
            for p in (
                self._prepare_sample(sample, mode="val") for sample in val_data
            )
            if p is not None
        ]
        if not prepared_val:
            return None
        prepared_val = _checkpoint_val_prepared(prepared_val)
        if not prepared_val:
            return None
        self._checkpoint_window_state = self._snapshot_train_state()
        brier, _, win_bce = self._forward_metrics(prepared_val)
        self._checkpoint_window_val_loss = brier
        self._checkpoint_window_win_bce = win_bce
        self.pytorch_model.train()
        return self._checkpoint_window_val_loss

    def finish_checkpoint_window(
        self,
        val_data: List[Dict[str, Any]],
        *,
        best_epoch: Optional[int] = None,
        val_pin_id: Optional[str] = None,
        val_pin_changed: bool = False,
    ) -> Dict[str, Any]:
        """Gate a complete streaming epoch against its pinned incoming baseline.

        Primary metric: mean in-band Brier (same as train_online). See
        `_checkpoint_gate_decision`.
        """
        baseline_state = self._checkpoint_window_state
        val_incoming = self._checkpoint_window_val_loss
        incoming_bce = self._checkpoint_window_win_bce
        if baseline_state is None or val_incoming is None:
            return {
                "checkpoint_accepted": False,
                "checkpoint_reject_reason": "missing_epoch_baseline",
                "val_loss_incoming": val_incoming,
                "val_loss_attempted": None,
                "val_loss": None,
                "val_guess_rate": None,
                "checkpoint_saved": False,
            }

        prepared_val = [
            p
            for p in (
                self._prepare_sample(sample, mode="val") for sample in val_data
            )
            if p is not None
        ]
        prepared_val = _checkpoint_val_prepared(prepared_val)
        if not prepared_val:
            return {
                "checkpoint_accepted": False,
                "checkpoint_reject_reason": "no_in_band_val",
                "val_loss_incoming": val_incoming,
                "val_loss_attempted": None,
                "val_loss": None,
                "val_guess_rate": None,
                "checkpoint_saved": False,
            }
        val_attempted, val_guess, attempted_bce = self._forward_metrics(prepared_val)
        same_val_pin = self._same_val_pin(val_pin_id, val_pin_changed)
        accepted, reject_reason = self._checkpoint_gate_decision(
            attempted_brier=val_attempted,
            incoming_brier=val_incoming,
            attempted_win_bce=attempted_bce,
            incoming_win_bce=incoming_bce,
            best_epoch=best_epoch,
            same_val_pin=same_val_pin,
        )

        if accepted:
            self._apply_val_pin_after_gate(
                accepted=True,
                incoming_brier=val_incoming,
                attempted_brier=val_attempted,
                val_pin_id=val_pin_id,
                same_val_pin=same_val_pin,
            )
            self.save_checkpoints(
                extra={"best_epoch": best_epoch, "cold_start_epoch": True}
            )
        else:
            self._restore_train_state(*baseline_state)
            self._apply_val_pin_after_gate(
                accepted=False,
                incoming_brier=val_incoming,
                attempted_brier=val_attempted,
                val_pin_id=val_pin_id,
                same_val_pin=same_val_pin,
            )
            logger.info(
                "Cold-start epoch checkpoint rejected "
                f"({reject_reason}): attempted Brier {val_attempted:.4f}, "
                f"incoming {val_incoming:.4f}; restored epoch-start weights."
            )

        self._checkpoint_window_state = None
        self._checkpoint_window_val_loss = None
        self._checkpoint_window_win_bce = None
        self.pytorch_model.train()
        return {
            "checkpoint_accepted": accepted,
            "checkpoint_reject_reason": reject_reason,
            "val_loss_incoming": round(val_incoming, 4),
            "val_loss_attempted": round(val_attempted, 4),
            "val_loss": round(val_attempted, 4),
            "val_guess_rate": round(val_guess * 100.0, 1),
            "checkpoint_saved": accepted,
        }

    def cancel_checkpoint_window(self) -> None:
        """Restore the epoch-start state when a reset/abort interrupts cold-start."""
        if self._checkpoint_window_state is not None:
            self._restore_train_state(*self._checkpoint_window_state)
        self._checkpoint_window_state = None
        self._checkpoint_window_val_loss = None
        self._checkpoint_window_win_bce = None
        self.pytorch_model.train()

    def _checkpoint_gate_decision(
        self,
        *,
        attempted_brier: float,
        incoming_brier: float,
        attempted_win_bce: Optional[float] = None,
        incoming_win_bce: Optional[float] = None,
        best_epoch: Optional[int] = None,
        same_val_pin: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """
        Accept attempted weights when in-band val Brier improves vs incoming
        (primary). Floor vs last accepted applies to *attempted* only: if incoming
        has already drifted over the floor, a probe that beats incoming (and
        clears CHECKPOINT_MIN_BEST_EPOCH) is allowed so catch-up cannot deadlock.
        Recovery is disabled when same_val_pin is False (val-pin refresh).
        """
        return decide_online_checkpoint(
            attempted_brier=attempted_brier,
            incoming_brier=incoming_brier,
            last_accepted=self.last_accepted_val_loss,
            floor_tol=CHECKPOINT_VAL_FLOOR_TOLERANCE,
            brier_eps=CHECKPOINT_BRIER_EPS,
            min_best_epoch=CHECKPOINT_MIN_BEST_EPOCH,
            best_epoch=best_epoch,
            attempted_win_bce=attempted_win_bce,
            incoming_win_bce=incoming_win_bce,
            same_val_pin=same_val_pin,
        )

    def _apply_val_pin_after_gate(
        self,
        *,
        accepted: bool,
        incoming_brier: Optional[float],
        attempted_brier: Optional[float],
        val_pin_id: Optional[str],
        same_val_pin: bool,
    ) -> None:
        """Keep last-accepted floor and pin id on the same yardstick.

        Pin refresh: rebase the floor to incoming Brier of the *current* (restored)
        weights on the new pin. Same pin: bind pin id on legacy checkpoints.
        """
        if accepted:
            if attempted_brier is not None:
                self.last_accepted_val_loss = float(attempted_brier)
            if val_pin_id:
                self.last_accepted_val_pin_id = val_pin_id
            return
        if not same_val_pin:
            self._rebase_val_pin_floor(incoming_brier, val_pin_id)
            return
        self._bind_val_pin_id(val_pin_id)

    def _load_model_state_soft(self, state_dict: Dict[str, Any]) -> bool:
        """
        Loads a checkpoint's weights, tolerating the two architecture-shape changes
        made after the first deploys: the head growing from 3 logits to 4 (adding the
        decision-verdict output), and the GRU's input growing from 3 features to 4
        (adding the match_time feature — see _build_sequence). A plain load_state_dict()
        raises RuntimeError on any shape mismatch, and the old code just dropped the
        whole checkpoint on that error — silently resetting training to scratch on this
        exact deploy. Instead, copy every matching tensor as-is, and:
          - for `head.weight`/`head.bias`: remap the old [win, stake, exposure] rows
            onto the new [win, decision, stake, exposure] layout (old row 0 -> new row
            0, old row 1 -> new row 2, old row 2 -> new row 3); the new decision row
            keeps its random init.
          - for `gru.weight_ih_l0` (the only GRU parameter whose shape depends on
            input_dim): copy the old 3 input columns as-is and zero the new 4th column
            — the match_time feature starts contributing *nothing* to the GRU's gates,
            so loading an old checkpoint doesn't suddenly shift behavior on every
            sequence; gradient descent picks up the new column's weights from there.
        Returns True if a shape remap actually happened (i.e. this is an old-format
        checkpoint) — callers use this to know the saved optimizer momentum buffers for
        the resized tensors are now stale and must not be reused (see load_checkpoints).
        """
        own_state = self.pytorch_model.state_dict()
        old_to_new_head_rows = [0, 2, 3]
        resized = False
        for name, param in state_dict.items():
            if name not in own_state:
                continue
            target = own_state[name]
            if param.shape == target.shape:
                target.copy_(param)
            elif name in ("head.weight", "head.bias") and param.shape[0] == 3 and target.shape[0] == 4:
                with torch.no_grad():
                    for old_idx, new_idx in enumerate(old_to_new_head_rows):
                        target[new_idx].copy_(param[old_idx])
                resized = True
            elif (
                name == "gru.weight_ih_l0" and param.dim() == 2 and target.dim() == 2
                and param.shape[0] == target.shape[0] and param.shape[1] < target.shape[1]
            ):
                # 3→4 (match_time) then 4→6 (set-point + pad_mask). New columns
                # start at zero so an old checkpoint does not jump on load.
                with torch.no_grad():
                    target[:, :param.shape[1]].copy_(param)
                    target[:, param.shape[1]:].zero_()
                resized = True
            else:
                logger.warning(
                    f"Skipping checkpoint tensor {name}: shape {tuple(param.shape)} "
                    f"does not match model {tuple(target.shape)}."
                )
        self.pytorch_model.load_state_dict(own_state)
        return resized

    def load_checkpoints(self):
        try:
            if os.path.exists(PYTORCH_WEIGHTS_PATH):
                blob = torch.load(PYTORCH_WEIGHTS_PATH, map_location=DEVICE)
                # Backward compat: older checkpoints were a bare state_dict rather than
                # {"model_state": ...}.
                state = blob["model_state"] if isinstance(blob, dict) and "model_state" in blob else blob
                arch_resized = self._load_model_state_soft(state)
                if isinstance(blob, dict):
                    self.blend_weight = float(blob.get("blend_weight", self.blend_weight))
                    self.market_weight = float(blob.get("market_weight", self.market_weight))
                    self.decision_threshold = max(
                        VALUE_THRESHOLD_FLOOR,
                        float(blob.get("decision_threshold", self.decision_threshold)),
                    )
                    self.sport_decision_thresholds = {
                        str(k): max(VALUE_THRESHOLD_FLOOR, float(v))
                        for k, v in (blob.get("sport_decision_thresholds") or {}).items()
                    }
                    self._apply_sport_threshold_floors()
                    self._apply_market_weight_floor()
                    lav = blob.get("last_accepted_val_loss")
                    if lav is not None:
                        self.last_accepted_val_loss = float(lav)
                    pin_id = blob.get("last_accepted_val_pin_id")
                    if pin_id:
                        self.last_accepted_val_pin_id = str(pin_id)
                # AdamW's load_state_dict() stores per-parameter momentum buffers
                # *positionally*, with no shape check against the live model — a mismatch
                # only blows up later, inside optimizer.step()'s elementwise ops against
                # the (now differently-shaped) resized tensors' gradients, as a
                # "tensor a (3) must match tensor b (4)" RuntimeError on every single
                # training step. So when a tensor was just remapped to a new shape, skip
                # restoring the optimizer state entirely — a fresh AdamW simply rebuilds
                # its momentum over the next few steps, which is harmless; reusing stale
                # buffers for a resized layer is not.
                if not arch_resized and isinstance(blob, dict) and "optimizer_state" in blob:
                    try:
                        self.pytorch_optimizer.load_state_dict(blob["optimizer_state"])
                        # load_state_dict restores param_groups wholesale, including the
                        # lr the checkpoint was saved with — without this, lowering
                        # LEARNING_RATE (env or default) would be silently undone on
                        # every restart by whatever rate the old checkpoint carried.
                        # Momentum buffers are what we want restored; the rate is config.
                        for group in self.pytorch_optimizer.param_groups:
                            group["lr"] = LEARNING_RATE
                        _optimizer_to_device(self.pytorch_optimizer, DEVICE)
                    except Exception:
                        # Optimizer shape changed (e.g. architecture bump) — keep the
                        # fresh optimizer state, the model weights still loaded fine.
                        pass
                self.is_trained = True
                logger.info(f"Successfully loaded PyTorch model weights from {PYTORCH_WEIGHTS_PATH}")
        except Exception as e:
            logger.error(f"Error loading model weights: {e}")

        try:
            if os.path.exists(LIGHTGBM_MODEL_PATH):
                booster = lgb.Booster(model_file=LIGHTGBM_MODEL_PATH)
                if booster.num_feature() != len(LGB_FEATURE_NAMES):
                    # Feature set changed (e.g. sport_idx added) since this booster was
                    # saved — a mismatched-shape predict() would just crash every
                    # inference cycle. Discard it and fall back to the heuristic score
                    # until the next scheduled refit produces a compatible booster.
                    logger.warning(
                        f"Ignoring saved LightGBM model at {LIGHTGBM_MODEL_PATH}: "
                        f"has {booster.num_feature()} features, expected {len(LGB_FEATURE_NAMES)}."
                    )
                else:
                    self.lgb_model = booster
                    self.lgb_trained = True
                    self._load_lgb_meta()
                    logger.info(f"Successfully loaded LightGBM model from {LIGHTGBM_MODEL_PATH}")
        except Exception as e:
            logger.error(f"Error loading LightGBM model: {e}")

    def _lgb_scores(self, views: List[Dict[str, Any]]) -> List[float]:
        if self.lgb_trained and self.lgb_model is not None:
            X = [lgb_feature_row(v) for v in views]
            return [
                min(max(float(p), 0.02), 0.98)
                for p in self.lgb_model.predict(np.array(X, dtype=np.float64))
            ]
        out = []
        for v in views:
            coeff = float(v.get("current_coeff") or v.get("coefficient") or 1.5)
            initial = float(v.get("initial_coeff") or v.get("initial_coefficient") or coeff)
            implied = (1.0 / coeff) if coeff > 1.0 else 0.85
            drop = (initial - coeff) / initial if initial > 0 else 0.0
            score = float(v.get("score_diff") or 0.0)
            out.append(min(max(implied + drop * 0.18 + score * 0.025, 0.12), 0.95))
        return out

    def _lgb_feature_row(self, view: Dict[str, Any]) -> List[float]:
        return lgb_feature_row(view)

    def predict_single(
        self,
        step_pairs: List[Tuple],
        current_coeff: float,
        initial_coeff: float,
        factor_id: int = 0,
        sport_path: Optional[str] = None,
        team_1: Optional[str] = None,
        team_2: Optional[str] = None,
        overround: Optional[float] = None,
    ) -> Tuple[float, float, float, float, float, float, float]:
        view = {
            "step_pairs": step_pairs,
            "current_coeff": current_coeff,
            "initial_coeff": initial_coeff,
            "factor_id": factor_id,
            "sport_path": sport_path or "",
            "team_1": team_1 or "",
            "team_2": team_2 or "",
            "overround": overround,
            "sport_idx": sport_index(sport_path),
            "market_idx": market_family_index(factor_id),
            "team1_idx": team_index(team_1),
            "team2_idx": team_index(team_2),
        }
        return self.predict_batch([view])[0]

    def predict_batch(self, items: List[Dict[str, Any]]) -> List[Tuple[float, float, float, float, float, float, float]]:
        """
        Same math as predict_single, but for a whole cycle's worth of live odds at once:
        one PyTorch forward pass and one LightGBM predict() call for the entire batch
        instead of thousands of individual Python-level calls. Each item needs a
        `step_pairs` list (chronological (coefficient, score_diff[, ts_epoch]) snapshots, see
        _build_sequence), plus `current_coeff`, `initial_coeff`, `factor_id`,
        `sport_path`, `team_1`, `team_2` (used to condition both models — see
        app/neuralbet/context.py).
        Returns results in the same order as `items`: (win_probability, error_rate,
        lgb_score, pytorch_score, decision_prob, stake_logit, exposure_logit).
        decision_prob is the residual-edge mapped to [0, 1] (>= 0.5 means predicted
        +EV vs 1/coeff — see OddsTrajectoryGRU's docstring); stake_logit/exposure_logit are
        only meaningful for the live-bankroll allocator (bankroll.allocate), not stored in
        ai_predictions.
        """
        if not items:
            return []

        sequences = [_gru_seq(it) for it in items]
        x_tensor = _tensor(np.array(sequences, dtype=np.float32))
        sport_idxs = [it.get("sport_idx", sport_index(it.get("sport_path"))) for it in items]
        market_idxs = [it.get("market_idx", market_family_index(it.get("factor_id", 0))) for it in items]
        team1_idxs = [it.get("team1_idx", team_index(it.get("team_1"))) for it in items]
        team2_idxs = [it.get("team2_idx", team_index(it.get("team_2"))) for it in items]
        sport_tensor = _tensor(sport_idxs, dtype=torch.long)
        market_tensor = _tensor(market_idxs, dtype=torch.long)
        team1_tensor = _tensor(team1_idxs, dtype=torch.long)
        team2_tensor = _tensor(team2_idxs, dtype=torch.long)
        kb_tensor = _tensor(
            [kb_context_vector(it) for it in items], dtype=torch.float32,
        )
        self.pytorch_model.eval()
        with torch.no_grad():
            logits = self.pytorch_model(
                x_tensor, sport_tensor, market_tensor, team1_tensor, team2_tensor, kb_tensor,
            )
            pytorch_probs = torch.sigmoid(logits[:, 0]).tolist()
            decision_probs = _decision_confidence(logits[:, 1]).tolist()
            stake_logits = logits[:, 2].tolist()
            exposure_logits = logits[:, 3].tolist()

        lgb_scores = self._lgb_scores(items)

        market_probs = [
            (min(max(1.0 / it["current_coeff"], 0.01), 0.99) if it["current_coeff"] > 1.0 else 0.99)
            for it in items
        ]
        lgb_weight = max(0.0, 1.0 - self.blend_weight - self.market_weight)

        results = []
        for pytorch_prob, market_prob, lgb_score, decision_prob, stake_logit, exposure_logit in zip(
            pytorch_probs, market_probs, lgb_scores, decision_probs, stake_logits, exposure_logits
        ):
            ensemble_ratio = (
                self.blend_weight * pytorch_prob + self.market_weight * market_prob + lgb_weight * lgb_score
            )
            win_probability = min(max(ensemble_ratio * 100.0, 1.0), 99.0)
            error_rate = round(100.0 - win_probability, 1)
            results.append((
                round(win_probability, 1), error_rate, round(lgb_score, 3), round(pytorch_prob, 3),
                round(decision_prob, 3), stake_logit, exposure_logit,
            ))
        return results

    def _load_lgb_meta(self) -> None:
        meta: Dict[str, Any] = {}
        if os.path.exists(LIGHTGBM_META_PATH):
            try:
                with open(LIGHTGBM_META_PATH, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
            except Exception as e:
                logger.warning(f"Could not read LightGBM meta {LIGHTGBM_META_PATH}: {e}")
        brier = meta.get("val_brier")
        self.lgb_last_val_brier = float(brier) if brier is not None else None
        self.lgb_last_accepted_at = meta.get("accepted_at")
        self.lgb_newest_finished_at = meta.get("newest_finished_at")
        if self.lgb_trained and not self.lgb_last_accepted_at and os.path.exists(LIGHTGBM_MODEL_PATH):
            ts = datetime.fromtimestamp(os.path.getmtime(LIGHTGBM_MODEL_PATH), tz=timezone.utc)
            self.lgb_last_accepted_at = ts.isoformat()
            if not self.lgb_newest_finished_at:
                self.lgb_newest_finished_at = self.lgb_last_accepted_at
            self._save_lgb_meta()

    def _save_lgb_meta(self) -> None:
        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            payload = {
                "val_brier": self.lgb_last_val_brier,
                "accepted_at": self.lgb_last_accepted_at,
                "newest_finished_at": self.lgb_newest_finished_at,
                "seed": LGB_SEED,
            }
            tmp = LIGHTGBM_META_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, LIGHTGBM_META_PATH)
        except Exception as e:
            logger.error(f"Error saving LightGBM meta: {e}")

    @staticmethod
    def _binary_brier(probs: np.ndarray, labels: np.ndarray) -> float:
        p = np.clip(probs.astype(np.float64), 0.0, 1.0)
        y = labels.astype(np.float64)
        return float(np.mean((p - y) ** 2))

    def train_lightgbm(
        self,
        rows: List[Dict[str, Any]],
        val_rows: Optional[List[Dict[str, Any]]] = None,
        newest_finished_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fits a real binary GBDT classifier on resolved bets: given the odds trajectory
        shape (drop ratio, volatility), sample count, market type and score state at the
        point the bet was cut off, predict whether it actually won. `rows`/`val_rows`
        must already be leak-free (score_diff + coefficient as they stood at the cutoff,
        not the final match state — see pipeline.py) and time-split (val_rows strictly
        later than rows) so the reported accuracy means something.

        Cutoffs are deterministic (mode=val) so the same 40k archive rows rebuild the
        same booster. A candidate is kept only if val Brier is not worse than the
        live booster (plus a small tolerance).
        """
        usable = []
        for r in rows:
            view = r if r.get("step_pairs") else build_model_input(r, mode="val")
            if view is None or view.get("is_win") is None:
                continue
            usable.append(view)
        if len(usable) < 50:
            return {"trained": False, "reason": "not_enough_samples", "samples": len(usable)}

        def to_xy(items):
            X, y = [], []
            for r in items:
                X.append(lgb_feature_row(r))
                y.append(float(r["is_win"]))
            return np.array(X, dtype=np.float64), np.array(y, dtype=np.float64)

        X_arr, y_arr = to_xy(usable)
        train_data = lgb.Dataset(
            X_arr, label=y_arr,
            feature_name=LGB_FEATURE_NAMES,
            categorical_feature=LGB_CATEGORICAL_FEATURES,
        )

        valid_sets = [train_data]
        valid_names = ["train"]
        usable_val = []
        for r in (val_rows or []):
            view = r if r.get("step_pairs") else build_model_input(r, mode="val")
            if view is None or view.get("is_win") is None:
                continue
            usable_val.append(view)
        if len(usable_val) >= 20:
            X_val, y_val = to_xy(usable_val)
            val_data = lgb.Dataset(
                X_val, label=y_val,
                feature_name=LGB_FEATURE_NAMES,
                categorical_feature=LGB_CATEGORICAL_FEATURES,
                reference=train_data,
            )
            valid_sets.append(val_data)
            valid_names.append("val")
        else:
            X_val, y_val = X_arr, y_arr

        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "verbosity": -1,
            "num_leaves": 15,
            "learning_rate": 0.05,
            "min_data_in_leaf": 10,
            "seed": LGB_SEED,
            "feature_fraction_seed": LGB_SEED,
            "bagging_seed": LGB_SEED,
            "data_random_seed": LGB_SEED,
            "deterministic": True,
            "force_row_wise": True,
        }
        callbacks = [lgb.log_evaluation(period=0)]
        if len(valid_sets) > 1:
            callbacks.append(lgb.early_stopping(stopping_rounds=15, verbose=False))
        booster = lgb.train(
            params, train_data, num_boost_round=200,
            valid_sets=valid_sets, valid_names=valid_names, callbacks=callbacks,
        )

        eval_split = "val" if len(usable_val) >= 20 else "train"
        new_probs = booster.predict(X_val, num_iteration=booster.best_iteration or None)
        new_brier = self._binary_brier(np.asarray(new_probs), y_val)
        pred_labels = [1.0 if p >= 0.5 else 0.0 for p in new_probs]
        accuracy = sum(1 for p, t in zip(pred_labels, y_val) if p == t) / len(y_val)

        previous_brier = None
        if self.lgb_trained and self.lgb_model is not None and len(X_val) >= 20:
            try:
                old_probs = self.lgb_model.predict(X_val, num_iteration=self.lgb_model.best_iteration or None)
                previous_brier = self._binary_brier(np.asarray(old_probs), y_val)
            except Exception as e:
                logger.warning(f"Could not score live LightGBM booster on val: {e}")
                previous_brier = self.lgb_last_val_brier
        elif self.lgb_last_val_brier is not None:
            previous_brier = self.lgb_last_val_brier

        accepted = True
        reject_reason = None
        if previous_brier is not None and new_brier > previous_brier + LGB_BRIER_TOLERANCE:
            accepted = False
            reject_reason = (
                f"val Brier {new_brier:.4f} > live {previous_brier:.4f}+{LGB_BRIER_TOLERANCE}"
            )

        importance = dict(zip(LGB_FEATURE_NAMES, [int(v) for v in booster.feature_importance()]))
        result = {
            "trained": True,
            "accepted": accepted,
            "reject_reason": reject_reason,
            "samples": len(usable),
            "val_samples": len(usable_val),
            "eval_split": eval_split,
            "train_accuracy": round(accuracy * 100.0, 1),
            "val_brier": round(new_brier, 4),
            "previous_val_brier": round(previous_brier, 4) if previous_brier is not None else None,
            "feature_importance": importance,
        }
        if not accepted:
            return result

        self.lgb_model = booster
        self.lgb_trained = True
        self.lgb_last_val_brier = new_brier
        self.lgb_last_accepted_at = datetime.now(timezone.utc).isoformat()
        if newest_finished_at:
            self.lgb_newest_finished_at = str(newest_finished_at)

        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            booster.save_model(LIGHTGBM_MODEL_PATH)
            logger.info(f"Saved LightGBM model checkpoint to {LIGHTGBM_MODEL_PATH}")
        except Exception as e:
            logger.error(f"Error saving LightGBM model: {e}")
        self._save_lgb_meta()
        return result

    # Grid-search candidates for tune_ensemble(). blend_weight/market_weight now search
    # a 2D grid together (w_py + w_market <= 1, LightGBM gets the remainder) — coarser
    # steps (0.2) than the old single-axis 0.05 so the combined grid (~21 valid pairs)
    # stays as cheap as the old 1D one, since a few hundred validation samples can't
    # support a finer 2D search without just fitting noise.
    _BLEND_CANDIDATES = [round(x * 0.2, 2) for x in range(6)]  # 0.0 .. 1.0
    # 0.50 = predicted edge 0 after the residual-head mapping (tanh+1)/2. Sweeping
    # below that would mean betting when the head itself says the market is too high.
    # 0.50 .. 0.70 → edge 0 .. 0.40. The EV gate (neurobet_filters.MIN_BET_EDGE_PCT) still
    # guards the live allocator on top of this.
    _THRESHOLD_CANDIDATES = [round(0.50 + x * 0.02, 2) for x in range(11)]  # 0.50 .. 0.70
    _MIN_TUNE_SAMPLES = 30
    # Raised from 20 — a threshold accepted on 20-40 validation bets is one long-shot
    # coefficient away from looking great by pure luck (observed in production: a single
    # winning 10x+ bet was enough to swing "best" ROI between thresholds cycle to cycle).
    # 80 doesn't eliminate the noise, but it takes several such flukes instead of one.
    _MIN_THRESHOLD_BETS = int(os.getenv("NEURALBET_MIN_THRESHOLD_BETS", "80"))
    # Exponential-smoothing rate applied to every tuned parameter below: a single tuning
    # pass moves at most this fraction of the way from the current value towards
    # whatever the grid search preferred this cycle, instead of jumping straight there.
    # Without this, blend_weight/decision_threshold visibly whipsawed cycle to cycle in
    # production logs (blend_weight 0.45 -> 0.0 -> 0.3 -> 0.35 -> 0.0 within minutes),
    # which meant the live "which outcomes count as a predicted win" set was being
    # driven by tuning noise, not by anything the model had actually learned.
    _TUNE_SMOOTH_ALPHA = float(os.getenv("NEURALBET_TUNE_SMOOTH_ALPHA", "0.3"))
    # Subtracted from a threshold candidate's val ROI, divided by sqrt(n_bets), before
    # comparing candidates — a threshold that only clears _MIN_THRESHOLD_BETS by a
    # handful is penalized relative to one supported by many more, so the search doesn't
    # keep picking whichever small sample got luckiest this cycle.
    _THRESHOLD_SAMPLE_PENALTY = float(os.getenv("NEURALBET_THRESHOLD_SAMPLE_PENALTY", "0.5"))
    # Lower than _MIN_THRESHOLD_BETS (80): a per-sport slice of one already-limited val
    # batch is inherently smaller than the whole batch (table tennis/basketball combined
    # are roughly half the archive, leaving the rest to split single-digit percentages
    # each), so demanding the same 80 as the global threshold would mean almost no sport
    # ever earns its own value. 50 still requires several dozen independent outcomes —
    # a real floor against small-sample luck, just not as strict as the pooled figure.
    # Sports that never clear this simply keep using decision_threshold (see
    # sport_threshold()) — there's no path where a sport ends up with no usable cutoff.
    _MIN_THRESHOLD_BETS_PER_SPORT = int(os.getenv("NEURALBET_MIN_THRESHOLD_BETS_PER_SPORT", "50"))
    # Same 1e-4 the GRU checkpoint gate uses. Brier sits ~0.18, so this is "not
    # worse" rather than "visibly better" — a grid point has to beat the *live*
    # mixture, not just the other 0.2-step candidates (live blend_weight is often
    # off-grid after EMA, e.g. 0.89, and the grid would otherwise walk toward a
    # coarser 0.8/1.0 that scores worse on the same val slice).
    _TUNE_BRIER_EPS = 1e-4

    def tune_ensemble(
        self,
        val_data: List[Dict[str, Any]],
        blend_market_frozen: bool = False,
    ) -> Dict[str, Any]:
        """
        Re-picks blend_weight / market_weight (and optionally decision thresholds)
        against held-out validation data. Called from pipeline.py at the same
        (throttled — see TUNE_EVERY_CYCLES) cadence as the LightGBM refit.

        - blend_weight / market_weight: 2D grid search over _BLEND_CANDIDATES
          minimizing Brier on data neither model trained on. market_weight puts
          raw bookmaker-implied probability (1/coeff) in the blend.
        - decision_threshold / sport_decision_thresholds: only when
          NEURALBET_DECISION_LOSS_WEIGHT > 0. Under Objective B (default weight 0)
          live predicted_win is EV-based (calibrated_p * coeff - 1 ≥ MIN_BET_EDGE),
          so residual-head cutoffs do not affect staking — sweeps are skipped and
          thresholds stay frozen.
        - Every updated parameter is EMA-smoothed (_TUNE_SMOOTH_ALPHA).
        - Blend/market are gated like GRU weights: best Brier must beat the live
          mixture and the market-only baseline (_TUNE_BRIER_EPS).
        - Accepted scalars are written via save_ensemble immediately.
        """
        prepared = [p for p in (self._prepare_sample(s, mode="val") for s in val_data) if p is not None]
        if len(prepared) < self._MIN_TUNE_SAMPLES:
            return {"tuned": False, "reason": "not_enough_val_samples", "samples": len(prepared)}

        seqs = _tensor([_gru_seq(p) for p in prepared], dtype=torch.float32)
        sport_t, market_t, team1_t, team2_t, kb_t = self._context_tensors(prepared)
        self.pytorch_model.eval()
        with torch.no_grad():
            logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t, kb_t)
            pytorch_probs = torch.sigmoid(logits[:, 0]).tolist()
            decision_probs = _decision_confidence(logits[:, 1]).tolist()

        targets = [p["target"] for p in prepared]
        coeffs = [p["coefficient"] for p in prepared]
        market_probs = [min(max(1.0 / c, 0.01), 0.99) if c > 1.0 else 0.99 for c in coeffs]

        if self.lgb_trained and self.lgb_model is not None:
            lgb_scores = self._lgb_scores(prepared)
        else:
            # No booster yet — every blend weight collapses onto pure pytorch_prob, so
            # the search below still runs, just without LightGBM in the mix.
            lgb_scores = [0.5] * len(prepared)

        # The odds-only baseline (no model at all) — logged alongside the tuned blend's
        # Brier so a regression (model doing worse than just trusting the bookmaker) is
        # visible in every training cycle's log line, not just in an offline export.
        def _mixture_brier(w_py: float, w_mkt: float) -> float:
            w_lgb = max(0.0, 1.0 - w_py - w_mkt)
            return sum(
                (w_py * gp + w_mkt * mp + w_lgb * lp - t) ** 2
                for gp, mp, lp, t in zip(pytorch_probs, market_probs, lgb_scores, targets)
            ) / len(prepared)

        base_brier = sum((mp - t) ** 2 for mp, t in zip(market_probs, targets)) / len(prepared)
        incoming_brier = _mixture_brier(self.blend_weight, self.market_weight)

        # Seed the grid with the live mixture so an off-grid blend (0.89 after EMA)
        # is not abandoned for a coarser 0.8/1.0 that scores worse on this slice.
        best_blend, best_market, best_brier = self.blend_weight, self.market_weight, incoming_brier
        for w_py in self._BLEND_CANDIDATES:
            for w_mkt in self._BLEND_CANDIDATES:
                if w_py + w_mkt > 1.0 + 1e-9:
                    continue
                brier = _mixture_brier(w_py, w_mkt)
                if brier < best_brier - self._TUNE_BRIER_EPS:
                    best_brier = brier
                    best_blend, best_market = w_py, w_mkt

        blend_accepted = (
            not blend_market_frozen
            and best_brier < incoming_brier - self._TUNE_BRIER_EPS
            and best_brier < base_brier - self._TUNE_BRIER_EPS
        )

        # Objective B (DECISION_LOSS_WEIGHT=0): EV policy ignores decision_threshold.
        tune_thresholds = DECISION_LOSS_WEIGHT > 0
        old_blend, old_market, old_threshold = self.blend_weight, self.market_weight, self.decision_threshold
        a = self._TUNE_SMOOTH_ALPHA
        if blend_accepted:
            self.blend_weight = old_blend + a * (best_blend - old_blend)
            self.market_weight = old_market + a * (best_market - old_market)
        self._apply_market_weight_floor()

        best_threshold, best_roi, best_bets = old_threshold, None, 0
        sport_threshold_report: Dict[str, Dict[str, Any]] = {}
        if tune_thresholds:
            best_threshold, best_roi, best_bets = self._sweep_threshold(
                decision_probs, coeffs, targets, self.decision_threshold, self._MIN_THRESHOLD_BETS,
            )
            if best_bets >= self._MIN_THRESHOLD_BETS:
                self.decision_threshold = max(
                    VALUE_THRESHOLD_FLOOR,
                    old_threshold + a * (best_threshold - old_threshold),
                )

            by_sport: Dict[str, List[int]] = {}
            for idx, p in enumerate(prepared):
                by_sport.setdefault(p.get("sport") or "Другое", []).append(idx)

            for sport, idxs in by_sport.items():
                sport_dp = [decision_probs[i] for i in idxs]
                sport_coeffs = [coeffs[i] for i in idxs]
                sport_targets = [targets[i] for i in idxs]
                old_sport_thr = self.sport_decision_thresholds.get(sport, self.decision_threshold)
                thr, roi, bets = self._sweep_threshold(
                    sport_dp, sport_coeffs, sport_targets, old_sport_thr, self._MIN_THRESHOLD_BETS_PER_SPORT,
                )
                if bets < self._MIN_THRESHOLD_BETS_PER_SPORT:
                    continue
                new_sport_thr = max(
                    _sport_threshold_floor(sport),
                    old_sport_thr + a * (thr - old_sport_thr),
                )
                self.sport_decision_thresholds[sport] = new_sport_thr
                sport_threshold_report[sport] = {
                    "old": round(old_sport_thr, 2), "new": round(new_sport_thr, 2), "target": round(thr, 2),
                    "val_roi_pct": round(roi * 100.0, 1), "val_bets": bets,
                }

            # Sports that didn't clear the val-bets floor this pass still need their
            # SPORT_THRESHOLD_FLOORS minimum in the stored dict (backtest snapshots it).
            self._apply_sport_threshold_floors()

        threshold_moved = abs(self.decision_threshold - old_threshold) > 1e-9
        changed = blend_accepted or threshold_moved or bool(sport_threshold_report)
        persisted = self.save_ensemble() if changed else False

        return {
            "tuned": True,
            "accepted": blend_accepted,
            "blend_market_frozen": blend_market_frozen,
            "thresholds_tuned": tune_thresholds,
            "persisted": persisted,
            "samples": len(prepared),
            "val_brier_base": round(base_brier, 4),
            "val_brier_incoming": round(incoming_brier, 4),
            "blend_weight": {
                "old": round(old_blend, 2), "new": round(self.blend_weight, 2),
                "target": round(best_blend, 2), "val_brier": round(best_brier, 4),
            },
            "market_weight": {
                "old": round(old_market, 2), "new": round(self.market_weight, 2), "target": round(best_market, 2),
            },
            "decision_threshold": {
                "old": round(old_threshold, 2), "new": round(self.decision_threshold, 2),
                "target": (
                    round(best_threshold, 2)
                    if tune_thresholds and best_bets >= self._MIN_THRESHOLD_BETS
                    else None
                ),
                "val_roi_pct": (
                    round(best_roi * 100.0, 1)
                    if tune_thresholds and best_bets >= self._MIN_THRESHOLD_BETS and best_roi is not None
                    else None
                ),
                "val_bets": best_bets if tune_thresholds else 0,
                "skipped": not tune_thresholds,
            },
            "sport_decision_threshold": sport_threshold_report,
        }

    def _sweep_threshold(
        self, decision_probs: List[float], coeffs: List[float], targets: List[float],
        current: float, min_bets: int,
    ) -> Tuple[float, Optional[float], int]:
        """
        Grid search over _THRESHOLD_CANDIDATES for the flat-stake-ROI-maximizing cutoff
        scored by bootstrap ROI lower bound (when enough bets), with sample-size penalty
        — see _THRESHOLD_SAMPLE_PENALTY. Shared by tune_ensemble's global sweep and
        each of its per-sport sweeps so the two can never drift onto different selection
        logic.
        """
        import random as _random

        best_threshold, best_score, best_roi, best_bets = current, float("-inf"), None, 0
        for thr in self._THRESHOLD_CANDIDATES:
            bet_returns: List[float] = []
            for dp, c, t in zip(decision_probs, coeffs, targets):
                if dp < thr or not in_bet_band(c):
                    continue
                bet_returns.append(c if t >= 0.5 else 0.0)
            n_bets = len(bet_returns)
            if n_bets < min_bets:
                continue
            staked = float(n_bets)
            returned = sum(bet_returns)
            roi = (returned - staked) / staked
            if n_bets >= min_bets + 5 and THRESHOLD_BOOTSTRAP_SAMPLES > 0:
                rois: List[float] = []
                for _ in range(THRESHOLD_BOOTSTRAP_SAMPLES):
                    sample = [_random.choice(bet_returns) for _ in range(n_bets)]
                    s_ret = sum(sample)
                    rois.append((s_ret - n_bets) / n_bets)
                rois.sort()
                roi_score = rois[int(0.025 * len(rois))]
            else:
                roi_score = roi
            score = roi_score - self._THRESHOLD_SAMPLE_PENALTY / (n_bets ** 0.5)
            if score > best_score:
                best_score = score
                best_roi = roi
                best_threshold = thr
                best_bets = n_bets
        return best_threshold, best_roi, best_bets

    def _decision_cost_loss(
        self,
        decision_logits: torch.Tensor,
        targets: torch.Tensor,
        coeffs: torch.Tensor,
    ) -> torch.Tensor:
        """Cost-sensitive BCE on bet/no-bet in the live coefficient band only."""
        band = (coeffs >= MIN_BET_COEFF) & (coeffs <= MAX_BET_COEFF)
        if int(band.sum().item()) == 0:
            return decision_logits.new_zeros(())
        # For weighted BCE, pos_weight=(c-1) makes the 0.5 verdict boundary
        # equivalent to p*c > 1. Keep a small positive floor for defensive env
        # configurations; in the live 1.5-2.0 band this is naturally 0.5-1.0.
        pos_w = torch.clamp(
            coeffs[band] - 1.0,
            min=0.05,
            max=DECISION_POS_WEIGHT_CAP,
        )
        loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_w)
        return loss_fn(decision_logits[band], targets[band])

    @staticmethod
    def _paired_market_loss(
        batch: List[Dict[str, Any]],
        pred_probs: torch.Tensor,
    ) -> Optional[torch.Tensor]:
        """Soft sum-to-1 penalty for complete sibling sets (overround_group_key)."""
        if PAIRED_MARKET_LOSS_WEIGHT <= 0:
            return None
        groups: Dict[Any, List[int]] = {}
        for idx, item in enumerate(batch):
            gk = overround_group_key(
                item.get("factor_id"),
                str(item.get("parameter") or ""),
                str(item.get("market_prefix") or ""),
            )
            if gk is None:
                continue
            key = (item.get("event_id"), gk)
            groups.setdefault(key, []).append(idx)
        penalties = []
        for (_eid, gk), idxs in groups.items():
            need = OVERROUND_EXPECTED_SIZE.get(gk[0], 0)
            if not need or len(idxs) != need:
                continue
            sub = pred_probs[idxs]
            penalties.append((sub.sum() - 1.0) ** 2)
        if not penalties:
            return None
        return torch.stack(penalties).mean()

    def _prepare_sample(self, sample: Dict[str, Any], mode: str = "train") -> Optional[Dict[str, Any]]:
        """Same view builder live/backtest use. Train=random eligible cutoff; val=deterministic."""
        if sample.get("is_win") is None and mode == "train":
            return None
        val_mode = "val" if mode == "val" else mode
        return build_model_input(sample, mode=val_mode)

    @staticmethod
    def _context_tensors(
        prepared: List[Dict[str, Any]],
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        sport_t = _tensor([p.get("sport_idx", 0) for p in prepared], dtype=torch.long)
        market_t = _tensor([p.get("market_idx", 0) for p in prepared], dtype=torch.long)
        team1_t = _tensor([p.get("team1_idx", 0) for p in prepared], dtype=torch.long)
        team2_t = _tensor([p.get("team2_idx", 0) for p in prepared], dtype=torch.long)
        kb_t = _tensor([kb_context_vector(p) for p in prepared], dtype=torch.float32)
        return sport_t, market_t, team1_t, team2_t, kb_t

    def _forward_metrics(self, prepared: List[Dict[str, Any]]) -> Tuple[float, float, float]:
        """
        Returns (mean_brier, hit_rate, win_bce) for a prepared batch in eval mode.

        Checkpoint gate and early-stop use mean Brier on win-head probs (Objective B /
        EV path cares about calibrated probability quality, not raw logit BCE alone).
        win_bce is retained for the Brier-tie break and logging. Hit rate: among
        in-band rows with EV ≥ MIN_BET_EDGE, how often is_win=1.
        Chunked by BATCH_SIZE to avoid OOM on large cold-start pools.
        """
        if not prepared:
            return float("nan"), float("nan"), float("nan")
        brier_sum = 0.0
        brier_n = 0
        loss_sum = 0.0
        loss_n = 0
        hit_sum = 0.0
        hit_n = 0
        self.pytorch_model.eval()
        with torch.no_grad():
            for i in range(0, len(prepared), BATCH_SIZE):
                chunk = prepared[i:i + BATCH_SIZE]
                seqs = _tensor(
                    [_gru_seq(p) for p in chunk], dtype=torch.float32
                )
                targets = _tensor([p["target"] for p in chunk], dtype=torch.float32)
                coeffs = _tensor(
                    [p["coefficient"] for p in chunk], dtype=torch.float32
                )
                sport_t, market_t, team1_t, team2_t, kb_t = self._context_tensors(chunk)
                logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t, kb_t)
                win_logits = logits[:, 0]
                chunk_loss = nn.functional.binary_cross_entropy_with_logits(
                    win_logits, targets,
                ).item()
                loss_sum += chunk_loss * len(chunk)
                loss_n += len(chunk)
                win_probs = torch.sigmoid(win_logits)
                brier_sum += float(((win_probs - targets) ** 2).sum().item())
                brier_n += len(chunk)
                would_bet = (
                    (win_probs * coeffs - 1.0 >= MIN_BET_EDGE_PCT / 100.0)
                    & (coeffs >= MIN_BET_COEFF)
                    & (coeffs <= MAX_BET_COEFF)
                )
                n_bet = int(would_bet.sum().item())
                if n_bet:
                    hit_sum += float((targets[would_bet] >= 0.5).sum().item())
                    hit_n += n_bet
        brier = (brier_sum / brier_n) if brier_n else float("nan")
        loss = (loss_sum / loss_n) if loss_n else float("nan")
        hit_rate = (hit_sum / hit_n) if hit_n else 0.0
        return brier, hit_rate, loss

    def _bankroll_pass(
        self, prepared: List[Dict[str, Any]], account: str, commit: bool,
    ) -> Dict[str, Any]:
        """
        Replays `prepared` samples in rounds of ROUND_SIZE against the named persistent
        bankroll account, using the model's *current* weights to decide stakes each
        round. If commit=True the results are actually written to bankroll_accounts/
        bankroll_ledger (used once, after the best epoch is chosen, for the "training"
        account — see train_online). If commit=False this only computes the
        differentiable bankroll loss for backprop and does not touch the DB.
        Returns {"loss": tensor-or-None, "bank_end": float, "rounds": int, ...}.
        """
        acc = bankroll.get_account(account) if commit else None
        bank = float(acc["balance"]) if acc is not None else float(bankroll.START_BALANCE)
        start_bank = bank
        losses = []
        total_staked = 0.0
        total_returned = 0.0
        bets_count = 0
        wins_count = 0
        losses_count = 0
        rounds_played = 0
        ruin_events = 0

        # commit=True is a bookkeeping-only replay (writing the chosen epoch's decisions
        # to the DB) — no backward() ever follows it, so skip building an autograd graph.
        grad_ctx = torch.no_grad() if commit else torch.enable_grad()
        with grad_ctx:
            for i in range(0, len(prepared), ROUND_SIZE):
                chunk = prepared[i:i + ROUND_SIZE]
                if len(chunk) < 2:
                    continue
                seqs = _tensor([_gru_seq(c) for c in chunk], dtype=torch.float32)
                sport_t, market_t, team1_t, team2_t, kb_t = self._context_tensors(chunk)
                logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t, kb_t)
                win_probs = torch.sigmoid(logits[:, 0])
                stake_logits = logits[:, 2]
                coeffs = _tensor([c["coefficient"] for c in chunk], dtype=torch.float32)
                wins = _tensor([c["target"] for c in chunk], dtype=torch.float32)

                fractions = bankroll.allocate(win_probs, coeffs, stake_logits)
                # Objective B: risk money only when raw ensemble EV clears min edge
                # (same rule as live predicted_win after calibration).
                verdict = _ev_verdict_mask(win_probs, coeffs).detach()
                # Match live: only size a position the bot is allowed to place (1.5–2.0).
                coeff_ok = ((coeffs >= MIN_BET_COEFF) & (coeffs <= MAX_BET_COEFF)).float()
                fractions = fractions * verdict * coeff_ok

                # Never stake more than one position on the same match in one round —
                # not just strictly mutually-exclusive outcomes, but any two markets on
                # the same event (they're routinely correlated even when they aren't
                # exclusive: "П1 wins" and "team 2's individual total over 2.5" pull
                # against each other). Live betting already refuses this
                # (backend/database.py's place_live_bet_candidates occupied_events
                # check) — without the same rule here, the stake head could learn to
                # split exposure across a match's markets in a way the real bot can
                # never execute. It genuinely happens in training batches:
                # _fetch_training_batch takes the freshest resolved bets, and when a
                # match settles *all* of its markets become fresh at once, so several of
                # them routinely land in the same round. Keeps whichever candidate on a
                # given event the allocator sized largest and zeroes the rest; detached
                # like every other hard keep/drop cut here.
                conflict_seen: Dict[Any, int] = {}
                drop_idx: List[int] = []
                sized = fractions.detach()
                for idx, c in enumerate(chunk):
                    key = c.get("conflict_key")
                    if key is None or sized[idx].item() <= 0.0:
                        continue
                    prev = conflict_seen.get(key)
                    if prev is None:
                        conflict_seen[key] = idx
                    elif sized[idx].item() > sized[prev].item():
                        drop_idx.append(prev)
                        conflict_seen[key] = idx
                    else:
                        drop_idx.append(idx)
                if drop_idx:
                    keep_mask = torch.ones_like(fractions)
                    keep_mask[drop_idx] = 0.0
                    fractions = fractions * keep_mask

                gain = bankroll.settle(fractions, coeffs, wins)
                round_loss = -torch.log(torch.clamp(gain, min=0.01))

                bank_next = bank * float(gain.detach().item())
                ruined = bank_next <= bankroll.RUIN_THRESHOLD
                if ruined:
                    round_loss = round_loss + bankroll.RUIN_PENALTY
                    ruin_events += 1

                fr = fractions.detach()
                staked = float((fr * bank).sum().item())
                returned = float((fr * coeffs * wins * bank).detach().sum().item())
                n_bets = int((fr >= bankroll.MIN_STAKE_FRACTION).sum().item())
                round_wins = int(((fr >= bankroll.MIN_STAKE_FRACTION) & (wins >= 0.5)).sum().item())
                round_losses = int(((fr >= bankroll.MIN_STAKE_FRACTION) & (wins < 0.5)).sum().item())

                if commit:
                    bankroll.apply_round_result(
                        account, staked=staked, returned=returned, bets_count=n_bets,
                        wins=round_wins, losses=round_losses,
                    )
                    bank = bankroll.get_account(account)["balance"]
                else:
                    bank = acc["start_balance"] if ruined else bank_next

                losses.append(round_loss)
                total_staked += staked
                total_returned += returned
                bets_count += n_bets
                wins_count += round_wins
                losses_count += round_losses
                rounds_played += 1

        loss_tensor = torch.stack(losses).mean() if losses else None
        turnover_roi = (
            (total_returned / total_staked - 1.0) * 100.0 if total_staked > 0 else None
        )
        return {
            "loss": loss_tensor,
            "bank_start": start_bank,
            "bank_end": bank,
            "rounds": rounds_played,
            "bets_count": bets_count,
            "wins": wins_count,
            "losses": losses_count,
            "total_staked": total_staked,
            "total_returned": total_returned,
            "turnover_roi": turnover_roi,
            "ruin_events": ruin_events,
        }

    def _train_minibatch_pass(
        self,
        prepared: List[Dict[str, Any]],
        bce: nn.Module,
        *,
        use_bankroll_loss: bool = True,
        should_abort: Optional[Any] = None,
    ) -> Tuple[float, bool]:
        """One shuffled mini-batch sweep — shared by online epochs and cold-start chunks."""
        order = list(range(len(prepared)))
        random.shuffle(order)
        epoch_train_losses: List[float] = []
        aborted = False

        for start in range(0, len(order), BATCH_SIZE):
            if should_abort is not None and should_abort():
                aborted = True
                break
            batch_idx = order[start:start + BATCH_SIZE]
            batch = [prepared[i] for i in batch_idx]
            seqs = _tensor([_gru_seq(b) for b in batch], dtype=torch.float32)
            targets = _tensor(
                [b["target"] for b in batch], dtype=torch.float32
            ).unsqueeze(1)
            sport_t, market_t, team1_t, team2_t, kb_t = self._context_tensors(batch)

            self.pytorch_optimizer.zero_grad()
            logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t, kb_t)
            win_logits = logits[:, 0:1]
            bce_loss = bce(win_logits, targets)
            win_probs = torch.sigmoid(win_logits)

            loss = bce_loss
            if BRIER_LOSS_WEIGHT > 0:
                coeffs_t = _tensor(
                    [b["coefficient"] for b in batch], dtype=torch.float32
                )
                in_band = (coeffs_t >= MIN_BET_COEFF) & (coeffs_t <= MAX_BET_COEFF)
                if int(in_band.sum().item()) > 0:
                    p = win_probs.squeeze(1)[in_band]
                    y = targets.squeeze(1)[in_band]
                    loss = loss + BRIER_LOSS_WEIGHT * ((p - y) ** 2).mean()

            if DECISION_LOSS_WEIGHT > 0:
                # Legacy residual + cost-sensitive decision heads (disabled in objective B).
                decision_logits = logits[:, 1]
                coeffs_t = _tensor(
                    [b["coefficient"] for b in batch], dtype=torch.float32
                )
                pred_edge = torch.tanh(decision_logits)
                true_edge = targets.squeeze(1) - _market_prob_tensor(coeffs_t)
                verdict_mask = (
                    (coeffs_t >= MIN_BET_COEFF)
                    & (coeffs_t <= MAX_BET_COEFF)
                )
                per = (pred_edge - true_edge) ** 2
                if int(verdict_mask.sum().item()) == 0:
                    decision_loss = per.new_zeros(())
                else:
                    decision_loss = per[verdict_mask].mean()
                decision_bce = self._decision_cost_loss(
                    decision_logits, targets.squeeze(1), coeffs_t,
                )
                loss = loss + DECISION_LOSS_WEIGHT * (decision_loss + decision_bce)

            paired = self._paired_market_loss(
                batch, win_probs.squeeze(1)
            )
            if paired is not None:
                loss = loss + PAIRED_MARKET_LOSS_WEIGHT * paired

            bank_pass = self._bankroll_pass(batch, account="training", commit=False)
            if use_bankroll_loss and bank_pass["loss"] is not None:
                br_loss = torch.clamp(
                    bank_pass["loss"], max=bankroll.BANKROLL_LOSS_CLIP
                )
                loss = loss + bankroll.BANKROLL_LOSS_WEIGHT * br_loss

            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                self.pytorch_model.parameters(), GRAD_CLIP_NORM
            )
            self.pytorch_optimizer.step()
            epoch_train_losses.append(float(bce_loss.item()))

        train_loss = sum(epoch_train_losses) / max(len(epoch_train_losses), 1)
        return train_loss, aborted

    def train_cold_start_chunk(
        self,
        training_data: List[Dict[str, Any]],
        *,
        learning_rate: Optional[float] = None,
        rebalance_classes: bool = True,
        should_abort: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Streaming cold-start chunk: exactly one shuffled mini-batch pass, no validation,
        no early stopping, no checkpoint gate, no bankroll replay. Weights accumulate
        across chunks; the caller gates/saves only after a full archive epoch.
        """
        empty_metrics = {
            "samples_used": 0,
            "samples_skipped": len(training_data),
            "positive_count": 0,
            "negative_count": 0,
            "epochs_run": 0,
            "final_loss": None,
            "train_guess_rate": None,
            "checkpoint_reject_reason": None,
        }

        prepared = [
            p for p in (self._prepare_sample(s) for s in training_data) if p is not None
        ]
        skipped = len(training_data) - len(prepared)
        if len(prepared) < 2:
            return {**empty_metrics, "samples_skipped": skipped}

        positive_count = sum(1 for p in prepared if p["target"] == 1.0)
        negative_count = len(prepared) - positive_count
        if rebalance_classes and positive_count and negative_count:
            pos_weight = _tensor(
                [negative_count / positive_count], dtype=torch.float32
            )
            bce = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        else:
            bce = nn.BCEWithLogitsLoss()

        old_lrs = [g["lr"] for g in self.pytorch_optimizer.param_groups]
        if learning_rate is not None:
            for group in self.pytorch_optimizer.param_groups:
                group["lr"] = learning_rate

        self.pytorch_model.train()
        aborted = False
        try:
            train_loss, aborted = self._train_minibatch_pass(
                prepared,
                bce,
                use_bankroll_loss=False,
                should_abort=should_abort,
            )
        finally:
            for group, lr in zip(self.pytorch_optimizer.param_groups, old_lrs):
                group["lr"] = lr

        if aborted:
            logger.info("Cold-start chunk aborted for model reset.")
            return {
                **empty_metrics,
                "samples_used": len(prepared),
                "samples_skipped": skipped,
                "positive_count": positive_count,
                "negative_count": negative_count,
                "epochs_run": 1,
                "checkpoint_reject_reason": "aborted",
            }

        self.is_trained = True
        # Skip a second full forward over the 40k chunk — pass_loss is the
        # training metric; val/checkpoint still run at epoch end.
        logger.info(
            f"Cold-start chunk complete: {len(prepared)} samples, "
            f"pass_loss {train_loss:.4f}."
        )

        return {
            "samples_used": len(prepared),
            "samples_skipped": skipped,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "epochs_run": 1,
            "final_loss": round(train_loss, 4),
            "train_guess_rate": None,
            "pass_loss": round(train_loss, 4),
            "checkpoint_reject_reason": None,
        }

    def train_online(
        self,
        training_data: List[Dict[str, Any]],
        val_data: Optional[List[Dict[str, Any]]] = None,
        epochs: int = MAX_EPOCHS,
        on_epoch: Any = None,
        skip_checkpoint_gate: bool = False,
        save_checkpoint: bool = True,
        learning_rate: Optional[float] = None,
        rebalance_classes: bool = True,
        use_bankroll_loss: bool = True,
        should_abort: Optional[Any] = None,
        val_pin_id: Optional[str] = None,
        val_pin_changed: bool = False,
    ) -> Dict[str, Any]:
        """
        Online-training pass: mini-batch AdamW over resolved bets.
        Primary loss: BCE-with-logits on is_win for the win-probability head
        (ensemble blend → calibration → EV verdict). Optional legacy decision-head
        residual/cost BCE when NEURALBET_DECISION_LOSS_WEIGHT > 0 (default 0).
        Plus paired-market soft constraint and bankroll utility (Kelly replay,
        masked by EV ≥ MIN_BET_EDGE). Early-stop on held-out in-band val Brier;
        checkpoint gate vs incoming uses the same Brier (win-BCE tie-break) and
        CHECKPOINT_VAL_FLOOR_TOLERANCE vs last accepted Brier.
        See OddsTrajectoryGRU docstring for head roles.
        """
        empty_metrics = {
            "samples_used": 0, "samples_skipped": len(training_data), "positive_count": 0,
            "negative_count": 0, "epochs_run": 0, "epoch_losses": [], "initial_loss": None,
            "final_loss": None, "train_guess_rate": None, "val_loss": None, "val_guess_rate": None,
            "best_epoch": None, "bankroll": None, "checkpoint_accepted": None,
            "checkpoint_saved": False,
            "val_loss_incoming": None, "val_loss_attempted": None,
            "checkpoint_reject_reason": None,
        }

        # The virtual "training" bank compounds round over round purely for the loss
        # signal, with nothing capping its growth across passes — left alone it drifts
        # into float overflow (inf) after enough online-training passes, which then
        # poisons every future round (inf - inf = NaN, which SQLite stores as NULL and
        # rejects via the NOT NULL constraint). Starting each pass from a clean
        # start_balance keeps it bounded.
        bankroll.reset_account("training")

        prepared = [p for p in (self._prepare_sample(s) for s in training_data) if p is not None]
        skipped = len(training_data) - len(prepared)
        if len(prepared) < 2:
            return {**empty_metrics, "samples_skipped": skipped}

        prepared_val = [
            p for p in (self._prepare_sample(s, mode="val") for s in (val_data or [])) if p is not None
        ]
        checkpoint_val = _checkpoint_val_prepared(prepared_val)

        incoming_state = None
        val_incoming = None
        val_incoming_guess = None
        val_incoming_bce = None
        if checkpoint_val:
            incoming_state = self._snapshot_train_state()
            val_incoming, val_incoming_guess, val_incoming_bce = self._forward_metrics(
                checkpoint_val
            )

        positive_count = sum(1 for p in prepared if p["target"] == 1.0)
        negative_count = len(prepared) - positive_count
        # pos_weight = n_neg/n_pos undoes a caller-imposed class mix (a 10/90 batch
        # would be trained as if it were 50/50). Cold-start keeps it; online random
        # mix does not.
        if rebalance_classes and positive_count and negative_count:
            pos_weight = _tensor(
                [negative_count / positive_count], dtype=torch.float32
            )
            bce = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        else:
            bce = nn.BCEWithLogitsLoss()

        old_lrs = [g["lr"] for g in self.pytorch_optimizer.param_groups]
        if learning_rate is not None:
            for group in self.pytorch_optimizer.param_groups:
                group["lr"] = learning_rate

        best_val = float("inf")
        best_state = None
        best_epoch = 0
        epochs_without_improvement = 0
        epoch_losses: List[float] = []

        self.pytorch_model.train()
        aborted = False
        try:
            for epoch_idx in range(1, epochs + 1):
                if should_abort is not None and should_abort():
                    aborted = True
                    break
                train_loss, pass_aborted = self._train_minibatch_pass(
                    prepared,
                    bce,
                    use_bankroll_loss=use_bankroll_loss,
                    should_abort=should_abort,
                )
                if pass_aborted:
                    aborted = True
                    break

                epoch_losses.append(train_loss)

                if checkpoint_val:
                    val_loss, val_guess_rate, _ = self._forward_metrics(checkpoint_val)
                else:
                    val_loss, val_guess_rate = train_loss, None
                selection_metric = val_loss if checkpoint_val else train_loss

                logger.info(
                    f"PyTorch online training epoch {epoch_idx}/{epochs} — "
                    f"train_loss: {train_loss:.4f}, val_brier: {val_loss:.4f}"
                )
                if on_epoch is not None:
                    try:
                        on_epoch(epoch_idx, train_loss, val_loss if checkpoint_val else None)
                    except Exception:
                        pass

                if selection_metric < best_val - CHECKPOINT_BRIER_EPS:
                    best_val = selection_metric
                    best_state = copy.deepcopy(self.pytorch_model.state_dict())
                    best_epoch = epoch_idx
                    epochs_without_improvement = 0
                else:
                    epochs_without_improvement += 1
                    if epochs_without_improvement >= EARLY_STOP_PATIENCE:
                        break
                    if (
                        CHECKPOINT_MIN_BEST_EPOCH > 0
                        and epoch_idx >= CHECKPOINT_MIN_BEST_EPOCH
                        and best_epoch < CHECKPOINT_MIN_BEST_EPOCH
                    ):
                        logger.info(
                            f"Online pass stopped at epoch {epoch_idx}: best_epoch="
                            f"{best_epoch} < {CHECKPOINT_MIN_BEST_EPOCH} (batch memorization)."
                        )
                        break

                self.pytorch_model.train()
                if aborted:
                    break
        finally:
            for group, lr in zip(self.pytorch_optimizer.param_groups, old_lrs):
                group["lr"] = lr

        if aborted:
            if incoming_state is not None:
                self._restore_train_state(*incoming_state)
            logger.info("PyTorch online training aborted for model reset.")
            return {
                **empty_metrics,
                "samples_used": len(prepared),
                "samples_skipped": skipped,
                "positive_count": positive_count,
                "negative_count": negative_count,
                "epochs_run": len(epoch_losses),
                "checkpoint_accepted": False,
                "checkpoint_reject_reason": "aborted",
            }

        if best_state is not None:
            self.pytorch_model.load_state_dict(best_state)

        if checkpoint_val:
            final_val_loss, final_val_guess_rate, final_val_bce = self._forward_metrics(
                checkpoint_val
            )
        else:
            final_val_loss, final_val_guess_rate, final_val_bce = None, None, None
        final_train_loss, final_train_guess_rate, _ = self._forward_metrics(prepared)

        # The number that used to land in the TRAINING log as "1000 → 4 000 000 ₽"
        # was a Kelly compound over the *training* set (10000 / ROUND_SIZE ≈ 500
        # rounds) after the network had just fitted that same set. A 1–2% in-sample
        # edge becomes a thousand-fold "profit" and says nothing about whether the
        # model can bet. Replay on held-out val instead. Skip the in-sample replay
        # on a full-archive cold-start: commit=False keeps autograd on, and 270k
        # rounds of that after early-stopping is what killed the worker every
        # ~22 min (epoch 11, then process restart, epoch 1/2 from scratch).
        in_sample_bank: Dict[str, Any] = {"bank_end": None}
        if len(prepared) <= 20000:
            in_sample_bank = self._bankroll_pass(prepared, account="training", commit=False)
        bank_result = self._bankroll_pass(
            prepared_val if prepared_val else prepared[:20000],
            account="training",
            commit=True,
        )

        self.is_trained = True
        self.pytorch_model.train()

        logger.info(
            f"PyTorch online training complete: best epoch {best_epoch}/{len(epoch_losses)}, "
            f"train_brier {final_train_loss:.4f}, val_brier "
            f"{final_val_loss if final_val_loss is not None else float('nan'):.4f}, "
            f"training bankroll {bank_result['bank_start']:.1f} -> {bank_result['bank_end']:.1f}."
        )

        checkpoint_accepted = True
        checkpoint_reject_reason = None
        val_loss_attempted = final_val_loss
        same_val_pin = self._same_val_pin(val_pin_id, val_pin_changed)
        if (
            not skip_checkpoint_gate
            and incoming_state is not None
            and final_val_loss is not None
            and val_incoming is not None
        ):
            checkpoint_accepted, checkpoint_reject_reason = self._checkpoint_gate_decision(
                attempted_brier=final_val_loss,
                incoming_brier=val_incoming,
                attempted_win_bce=final_val_bce,
                incoming_win_bce=val_incoming_bce,
                best_epoch=best_epoch,
                same_val_pin=same_val_pin,
            )
            if not checkpoint_accepted:
                self._restore_train_state(*incoming_state)
                logger.info(
                    f"Online pass checkpoint rejected ({checkpoint_reject_reason}): "
                    f"attempted Brier {val_loss_attempted:.4f}, incoming {val_incoming:.4f}"
                    + (
                        f", last accepted floor {self.last_accepted_val_loss:.4f}"
                        if self.last_accepted_val_loss is not None
                        else ""
                    )
                    + "; restored previous weights, checkpoint file unchanged."
                )
            self._apply_val_pin_after_gate(
                accepted=checkpoint_accepted,
                incoming_brier=val_incoming,
                attempted_brier=final_val_loss,
                val_pin_id=val_pin_id,
                same_val_pin=same_val_pin,
            )
        elif val_pin_id and self.last_accepted_val_pin_id is None:
            self._bind_val_pin_id(val_pin_id)

        if checkpoint_accepted and save_checkpoint:
            self.save_checkpoints(extra={"best_epoch": best_epoch})
        elif not checkpoint_accepted:
            save_checkpoint = False

        return {
            "samples_used": len(prepared),
            "samples_skipped": skipped,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "epochs_run": len(epoch_losses),
            "epoch_losses": [round(l, 4) for l in epoch_losses],
            "initial_loss": round(epoch_losses[0], 4) if epoch_losses else None,
            "final_loss": round(final_train_loss, 4),
            "train_guess_rate": round(final_train_guess_rate * 100.0, 1),
            "val_loss": round(final_val_loss, 4) if final_val_loss is not None else None,
            "val_guess_rate": round(final_val_guess_rate * 100.0, 1) if final_val_guess_rate is not None else None,
            "best_epoch": best_epoch,
            "checkpoint_accepted": checkpoint_accepted,
            "checkpoint_saved": bool(checkpoint_accepted and save_checkpoint),
            "checkpoint_reject_reason": checkpoint_reject_reason,
            "val_loss_incoming": round(val_incoming, 4) if val_incoming is not None else None,
            "val_loss_attempted": (
                round(val_loss_attempted, 4) if val_loss_attempted is not None else None
            ),
            "bankroll": {
                "start": round(bank_result["bank_start"], 2),
                "end": round(bank_result["bank_end"], 2),
                "rounds": bank_result["rounds"],
                "bets_count": bank_result["bets_count"],
                "wins": bank_result["wins"],
                "losses": bank_result["losses"],
                "turnover_roi": (
                    round(bank_result["turnover_roi"], 1)
                    if bank_result.get("turnover_roi") is not None
                    else None
                ),
                "on_val": bool(prepared_val),
                "in_sample_end": (
                    round(in_sample_bank["bank_end"], 2)
                    if in_sample_bank.get("bank_end") is not None
                    else None
                ),
                "ruin_events": bank_result["ruin_events"],
            },
        }
