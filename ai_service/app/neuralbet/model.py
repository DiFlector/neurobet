import copy
import math
import random
import os
import logging
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
from neurobet_filters import MIN_BET_COEFF, MAX_BET_COEFF, in_bet_band

logger = logging.getLogger("ai_service_model")

PYTORCH_WEIGHTS_PATH = os.path.join(MODEL_DIR, "pytorch_gru.pt")
LIGHTGBM_MODEL_PATH = os.path.join(MODEL_DIR, "lightgbm_model.txt")

LGB_FEATURE_NAMES = ["coefficient", "initial_coefficient", "drop_ratio", "volatility", "samples_count", "factor_id", "score_diff", "sport_idx", "team1_idx", "team2_idx", "overround"]
# Sentinel for "overround unknown" (missing sibling-market data, or a market outside the
# core set overround is even computed for — see backend/database.py's
# _overround_group_key). Real overrounds are always > 1.0 (that's what "the bookmaker's
# margin" means), so 0.0 is unambiguous as "no info," letting the tree split it away
# from real values instead of quietly biasing every unknown row towards zero margin.
OVERROUND_UNKNOWN = 0.0

SEQ_LEN = 10

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
# A finished online pass only keeps its weights if they beat BOTH: the incoming
# model on this pass's val split, AND the last saved checkpoint's recorded val_loss
# (the number on the admin chart). Same-split stops a failed pass becoming live;
# the chart cap stops a "barely better than incoming on a hard val" (0.2654 vs
# 0.2658) from overwriting a 0.20 checkpoint. Same epsilon as the per-epoch check.
# 64 -> 128 -> 256: fewer, larger mini-batches per epoch use the CPU's vectorized
# matmuls more efficiently (more work per Python-level loop iteration) — meaningful as
# each training pass's sample count (pipeline.TRAIN_BATCH_TOTAL) has grown; 256 keeps
# minibatch count per epoch reasonable (~40 at a 10000-sample pass) without the batch
# getting so large a single gradient step stops responding to individual examples.
BATCH_SIZE = int(os.getenv("NEURALBET_BATCH_SIZE", "256"))
# 1e-3 -> 1e-4: 1e-3 is a from-scratch rate; for online fine-tuning of an
# already-converged network it was large enough that every epoch after the first
# dragged the weights toward the current batch and away from the general solution —
# observed as "best epoch 1/11, val_loss rising monotonically from epoch 1" even on
# 5000-sample batches, where batch size clearly wasn't the cause anymore.
LEARNING_RATE = float(os.getenv("NEURALBET_LEARNING_RATE", "1e-4"))
GRAD_CLIP_NORM = 1.0

# Weight on the decision-head loss (the bet/no-bet verdict) relative to the win-head BCE.
# 1.0 — same order of magnitude as bce_loss, so early training doesn't let one head starve
# the other of gradient.
DECISION_LOSS_WEIGHT = float(os.getenv("NEURALBET_DECISION_LOSS_WEIGHT", "1.0"))

# No GPU here (torch.cuda.is_available() is False in this container) — everything runs
# on CPU, so thread count is the real lever. Defaults to PyTorch's own heuristic (which
# undercounts on this host — observed torch.get_num_threads() == 6 on a 12-core
# machine); pin it explicitly instead of leaving throughput on the table. Left just
# short of the full core count so the FastAPI/Uvicorn process and the rest of the
# container still get scheduled promptly.
torch.set_num_threads(int(os.getenv("NEURALBET_TORCH_THREADS", str(max((os.cpu_count() or 4) - 2, 1)))))

# How many candidate bets make up one "round" for the bankroll-allocation loss —
# roughly how many live outcomes the bot might be weighing at once in a real cycle.
ROUND_SIZE = 20


SPORT_EMB_DIM = 8
MARKET_EMB_DIM = 6
TEAM_EMB_DIM = 6


class OddsTrajectoryGRU(nn.Module):
    """
    PyTorch GRU sequence model over odds-trajectory + live-score steps, conditioned on
    which sport, which market family, and which two teams/players (team1_idx/team2_idx —
    see app/neuralbet/context.py's team_index) the bet is on.
    Sequence input: (batch, seq_len=10, input_dim=4) — per step: [log(coefficient),
    score_diff, t_norm, match_time]. score_diff has a completely different scale/meaning
    per sport (3 goals in football vs. 3 points in basketball), and match_time (how far
    into the match this snapshot was, see _build_sequence's TIMER_UNKNOWN sentinel) is
    what actually distinguishes "odds dropped on minute 5" from "odds dropped on minute
    85" — the sequence alone says nothing about what kind of outcome is even being
    priced, *who* is playing, or *when* in the match this happened, so
    sport_idx/market_idx/team1_idx/team2_idx are embedded and concatenated onto the
    GRU's final hidden state before the output head, giving the model static context
    the odds curve by itself can't provide. Team identity uses a hashed embedding
    (TEAM_HASH_BUCKETS is far larger than the small closed sport/market vocabularies)
    since there's no fixed roster of every team/player Fonbet will ever cover.
    Output: 4 raw logits per sample — [win_logit, decision_logit, stake_logit,
    exposure_logit]:
      - win_logit: sigmoid() gives the win-probability estimate (same role the old
        single-output head had) — still an input to the LightGBM/PyTorch ensemble blend.
      - decision_logit: tanh() is the predicted residual vs the bookmaker
        (is_win - 1/coeff). Mapped to [0, 1] as (tanh+1)/2 for storage/thresholds:
        0.5 means "no edge", above 0.5 means "bet — the market underprices this".
        Trained with MSE on that residual, not BCE on is_win — a two-way book is
        50/50 by construction, so "will this line win" has no headroom.
      - stake_logit: sigmoid() scales a candidate's position size down from its
        formula-capped fractional-Kelly size — see bankroll.allocate(). Unused at plain
        inference.
      - exposure_logit: vestigial. Kelly sizing (bankroll.allocate) no longer needs a
        learned "how much of the bank to sit out" signal — each position is sized
        independently and capped, so nothing reads this output anymore. Kept only so
        the 4-logit head shape stays checkpoint-compatible; not worth a head resize and
        another _load_model_state_soft remap for a fifth number nothing would use.
    No activation is applied here; callers apply sigmoid/softmax as appropriate so the
    raw logits can feed BCEWithLogitsLoss directly (numerically more stable than a
    Sigmoid layer + BCELoss).
    """
    def __init__(self, input_dim: int = 4, hidden_dim: int = HIDDEN_DIM, num_layers: int = GRU_LAYERS):
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
        self.fc1 = nn.Linear(hidden_dim + SPORT_EMB_DIM + MARKET_EMB_DIM + 2 * TEAM_EMB_DIM, 32)
        self.relu = nn.ReLU()
        self.head = nn.Linear(32, 4)

    def forward(
        self, x: torch.Tensor, sport_idx: torch.Tensor, market_idx: torch.Tensor,
        team1_idx: torch.Tensor, team2_idx: torch.Tensor,
    ) -> torch.Tensor:
        out, _ = self.gru(x)
        last_step = out[:, -1, :]
        ctx = torch.cat([
            last_step, self.sport_embed(sport_idx), self.market_embed(market_idx),
            self.team_embed(team1_idx), self.team_embed(team2_idx),
        ], dim=-1)
        h = self.relu(self.fc1(ctx))
        return self.head(h)


# Normalizer for the real-elapsed-time-between-updates feature: 2 hours maps to 1.0,
# longer clamps there. Covers the live span of virtually every match type this bot
# sees; chosen as a fixed constant (not per-sequence max) so "odds moved fast" vs "odds
# moved slowly" stay distinguishable — normalizing by each sequence's own span would
# erase exactly the signal this feature exists to carry.
TIME_NORM_SECONDS = float(os.getenv("NEURALBET_TIME_NORM_SECONDS", "7200.0"))

# Normalizer for the match_time feature (elapsed time *into the match*, not between
# odds updates): 90 minutes maps to 1.0. Football-sized on purpose — a shorter-clock
# sport (basketball, table tennis) just never reaches 1.0, which is fine, since the
# sport embedding already gives the network the context to calibrate the scale per
# sport (same reasoning score_diff's docstring already covers).
MATCH_TIME_NORM_SECONDS = float(os.getenv("NEURALBET_MATCH_TIME_NORM_SECONDS", "5400.0"))
# Sentinel for "match_time unknown" (Fonbet's live timer field wasn't a recognizable
# clock at this snapshot — see backend/database.py's _parse_timer_seconds). Real
# normalized values always land in [0, 1], so -1.0 is unambiguous as "no info" and lets
# the network learn to treat it specially instead of quietly reading it as kickoff.
MATCH_TIME_UNKNOWN = -1.0


def _build_sequence(step_pairs: List[Tuple]) -> List[List[float]]:
    """
    Turns a list of (coefficient, score_diff[, ts_epoch[, match_time_seconds]])
    snapshots — in chronological order, one per odds update — into the fixed-length
    (SEQ_LEN, 4) feature sequence the GRU takes. Shared by predict_single/predict_batch/
    train_online so the call sites can't drift out of sync with each other.

    Coefficient is log-scaled: raw coefficients range ~1.01-50, which saturates a GRU's
    gates; log(coeff) is roughly symmetric around 0 and much better behaved.

    Two distinct time signals, not one:
      - t_norm: elapsed wall-clock time *between odds updates* (real when every step in
        this sequence carries a timestamp, else falls back to the step's position 0..1
        — see TIME_NORM_SECONDS). Ten odds moves in 3 minutes and ten moves across 2
        hours are different market behavior a step-index alone can't tell apart.
      - match_time: *where in the match* this snapshot happened (see
        MATCH_TIME_NORM_SECONDS/MATCH_TIME_UNKNOWN) — a coefficient drop on minute 5
        and the same drop on minute 85 mean very different things, and nothing else in
        this feature vector says which one a given step was. Per-step, not per-sequence
        (unlike t_norm's timestamp check): a step with an unparseable timer string gets
        MATCH_TIME_UNKNOWN on its own, the rest of the sequence keeps real values.
    """
    pairs = list(step_pairs) if step_pairs else [(1.5, 0)]
    if len(pairs) < SEQ_LEN:
        pairs = [pairs[0]] * (SEQ_LEN - len(pairs)) + pairs
    else:
        pairs = pairs[-SEQ_LEN:]
    n = len(pairs)

    timestamps = [(p[2] if len(p) > 2 else None) for p in pairs]
    use_real_time = all(t is not None for t in timestamps)
    if use_real_time:
        base = float(timestamps[0])
        t_feats = [min(max(float(t) - base, 0.0) / TIME_NORM_SECONDS, 1.0) for t in timestamps]
    else:
        t_feats = [(i / (n - 1)) if n > 1 else 1.0 for i in range(n)]

    match_times = [(p[3] if len(p) > 3 else None) for p in pairs]
    match_time_feats = [
        min(max(float(mt), 0.0) / MATCH_TIME_NORM_SECONDS, 1.0) if mt is not None else MATCH_TIME_UNKNOWN
        for mt in match_times
    ]

    return [
        [math.log(max(float(p[0]), 1.01)), float(p[1]), t, mt]
        for p, t, mt in zip(pairs, t_feats, match_time_feats)
    ]


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
    """Map residual-edge tanh to [0, 1]: 0.5 = edge 0, >0.5 = predicted +EV vs market."""
    return (torch.tanh(decision_logits) + 1.0) * 0.5


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

    def _reset_state(self):
        """
        Everything that makes this a "fresh, never-trained" ensemble — shared by
        __init__ (cold container start, then load_checkpoints() may overwrite it with a
        saved state) and reset() (admin-triggered wipe, which deliberately does NOT
        reload afterward). Keeping this in one place means the two can never drift apart
        on what "untrained" actually means.
        """
        self.pytorch_model = OddsTrajectoryGRU()
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

        # Real gradient-boosted classifier, fit on actual resolved bets (see
        # train_lightgbm). None until the first successful training pass — until then
        # predict_single() falls back to the odds-implied heuristic.
        self.lgb_model: Any = None
        self.lgb_trained = False

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
        for path in (PYTORCH_WEIGHTS_PATH, LIGHTGBM_MODEL_PATH):
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
            torch.save(payload, PYTORCH_WEIGHTS_PATH)
            logger.info(f"Saved PyTorch model weights checkpoint to {PYTORCH_WEIGHTS_PATH}")
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
                and param.shape[0] == target.shape[0] and param.shape[1] == 3 and target.shape[1] == 4
            ):
                with torch.no_grad():
                    target[:, :3].copy_(param)
                    target[:, 3].zero_()
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
                blob = torch.load(PYTORCH_WEIGHTS_PATH, map_location="cpu")
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
                    logger.info(f"Successfully loaded LightGBM model from {LIGHTGBM_MODEL_PATH}")
        except Exception as e:
            logger.error(f"Error loading LightGBM model: {e}")

    def _lgb_feature_row(self, coeff: float, initial_coeff: float, min_coeff: float, max_coeff: float, samples_count: int, factor_id: int, score_diff: int, sport_idx: int = 0, team1_idx: int = 0, team2_idx: int = 0, overround: Optional[float] = None) -> List[float]:
        drop_ratio = (initial_coeff - coeff) / initial_coeff if initial_coeff > 0 else 0.0
        volatility = (max_coeff - min_coeff) if (max_coeff is not None and min_coeff is not None) else 0.0
        return [coeff, initial_coeff, drop_ratio, volatility, float(samples_count or 0), float(factor_id or 0), float(score_diff), float(sport_idx), float(team1_idx), float(team2_idx), float(overround) if overround else OVERROUND_UNKNOWN]

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
        seq = _build_sequence(step_pairs)
        tensor_in = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
        sp_idx = sport_index(sport_path)
        mk_idx = market_family_index(factor_id)
        t1_idx = team_index(team_1)
        t2_idx = team_index(team_2)
        sport_tensor = torch.tensor([sp_idx], dtype=torch.long)
        market_tensor = torch.tensor([mk_idx], dtype=torch.long)
        team1_tensor = torch.tensor([t1_idx], dtype=torch.long)
        team2_tensor = torch.tensor([t2_idx], dtype=torch.long)

        self.pytorch_model.eval()
        with torch.no_grad():
            logits = self.pytorch_model(tensor_in, sport_tensor, market_tensor, team1_tensor, team2_tensor)[0]
            pytorch_prob = float(torch.sigmoid(logits[0]).item())
            decision_prob = float(_decision_confidence(logits[1]).item())
            stake_logit = float(logits[2].item())
            exposure_logit = float(logits[3].item())

        score_diff = step_pairs[-1][1] if step_pairs else 0
        if self.lgb_trained and self.lgb_model is not None:
            coeffs = [p[0] for p in step_pairs] if step_pairs else [current_coeff]
            min_coeff, max_coeff = min(coeffs), max(coeffs)
            row = self._lgb_feature_row(current_coeff, initial_coeff, min_coeff, max_coeff, len(step_pairs), factor_id, score_diff, sp_idx, t1_idx, t2_idx, overround)
            raw_pred = self.lgb_model.predict(np.array([row], dtype=np.float64))[0]
            lgb_score = min(max(float(raw_pred), 0.02), 0.98)
        else:
            # No trained LightGBM model yet (not enough resolved bets) — fall back to an
            # odds-implied heuristic so the app still produces usable numbers.
            implied_prob = (1.0 / current_coeff) if current_coeff > 1.0 else 0.85
            coeff_drop_ratio = (initial_coeff - current_coeff) / initial_coeff if initial_coeff > 0 else 0.0
            trend_boost = coeff_drop_ratio * 0.18
            score_boost = score_diff * 0.025
            lgb_score = min(max(implied_prob + trend_boost + score_boost, 0.12), 0.95)

        # Bookmaker-implied probability, the same odds-only baseline the "Brier (база)"
        # column in the stats page compares against — folded into the blend itself (not
        # just the comparison) so a weak model automatically loses influence to it (see
        # market_weight's docstring in __init__).
        market_prob = min(max(1.0 / current_coeff, 0.01), 0.99) if current_coeff > 1.0 else 0.99
        lgb_weight = max(0.0, 1.0 - self.blend_weight - self.market_weight)
        ensemble_ratio = (
            self.blend_weight * pytorch_prob + self.market_weight * market_prob + lgb_weight * lgb_score
        )
        # Floor/ceiling relaxed from the old 12/95 — that clamp put a hard floor under
        # how low a probability could ever be shown, which meant a true long-shot
        # (real chance well under 12%) always looked artificially closer to fair value,
        # inflating its computed EV. 1/99 only rules out literal 0%/100% certainty.
        win_probability = min(max(ensemble_ratio * 100.0, 1.0), 99.0)
        error_rate = round(100.0 - win_probability, 1)

        return (
            round(win_probability, 1), error_rate, round(lgb_score, 3), round(pytorch_prob, 3),
            round(decision_prob, 3), stake_logit, exposure_logit,
        )

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

        sequences = [_build_sequence(it["step_pairs"]) for it in items]
        x_tensor = torch.tensor(np.array(sequences, dtype=np.float32))
        sport_idxs = [sport_index(it.get("sport_path")) for it in items]
        market_idxs = [market_family_index(it.get("factor_id", 0)) for it in items]
        team1_idxs = [team_index(it.get("team_1")) for it in items]
        team2_idxs = [team_index(it.get("team_2")) for it in items]
        sport_tensor = torch.tensor(sport_idxs, dtype=torch.long)
        market_tensor = torch.tensor(market_idxs, dtype=torch.long)
        team1_tensor = torch.tensor(team1_idxs, dtype=torch.long)
        team2_tensor = torch.tensor(team2_idxs, dtype=torch.long)
        self.pytorch_model.eval()
        with torch.no_grad():
            logits = self.pytorch_model(x_tensor, sport_tensor, market_tensor, team1_tensor, team2_tensor)
            pytorch_probs = torch.sigmoid(logits[:, 0]).tolist()
            decision_probs = _decision_confidence(logits[:, 1]).tolist()
            stake_logits = logits[:, 2].tolist()
            exposure_logits = logits[:, 3].tolist()

        score_diffs = [(it["step_pairs"][-1][1] if it["step_pairs"] else 0) for it in items]

        if self.lgb_trained and self.lgb_model is not None:
            X = []
            for it, score_diff, sp_idx, t1_idx, t2_idx in zip(items, score_diffs, sport_idxs, team1_idxs, team2_idxs):
                pairs = it["step_pairs"]
                coeffs = [p[0] for p in pairs] if pairs else [it["current_coeff"]]
                min_coeff, max_coeff = min(coeffs), max(coeffs)
                X.append(self._lgb_feature_row(
                    it["current_coeff"], it["initial_coeff"], min_coeff, max_coeff,
                    len(pairs), it.get("factor_id", 0), score_diff, sp_idx, t1_idx, t2_idx,
                    it.get("overround"),
                ))
            lgb_scores = [min(max(float(p), 0.02), 0.98) for p in self.lgb_model.predict(np.array(X, dtype=np.float64))]
        else:
            lgb_scores = []
            for it, score_diff in zip(items, score_diffs):
                coeff = it["current_coeff"]
                initial_coeff = it["initial_coeff"]
                implied_prob = (1.0 / coeff) if coeff > 1.0 else 0.85
                coeff_drop_ratio = (initial_coeff - coeff) / initial_coeff if initial_coeff > 0 else 0.0
                trend_boost = coeff_drop_ratio * 0.18
                score_boost = score_diff * 0.025
                lgb_scores.append(min(max(implied_prob + trend_boost + score_boost, 0.12), 0.95))

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

    def train_lightgbm(self, rows: List[Dict[str, Any]], val_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Fits a real binary GBDT classifier on resolved bets: given the odds trajectory
        shape (drop ratio, volatility), sample count, market type and score state at the
        point the bet was cut off, predict whether it actually won. `rows`/`val_rows`
        must already be leak-free (score_diff + coefficient as they stood at the cutoff,
        not the final match state — see pipeline.py) and time-split (val_rows strictly
        later than rows) so the reported accuracy means something.
        """
        usable = [r for r in rows if r.get("is_win") is not None]
        if len(usable) < 50:
            return {"trained": False, "reason": "not_enough_samples", "samples": len(usable)}

        def to_xy(items):
            X, y = [], []
            for r in items:
                initial_coeff = r.get("initial_coefficient") or 1.5
                coeff = r.get("coefficient") or initial_coeff
                row = self._lgb_feature_row(
                    coeff, initial_coeff,
                    r.get("min_coefficient"), r.get("max_coefficient"),
                    r.get("samples_count"), r.get("factor_id"), r.get("score_diff", 0),
                    sport_index(r.get("sport_path")),
                    team_index(r.get("team_1")), team_index(r.get("team_2")),
                    r.get("overround_close"),
                )
                X.append(row)
                y.append(float(r["is_win"]))
            return np.array(X, dtype=np.float64), np.array(y, dtype=np.float64)

        X_arr, y_arr = to_xy(usable)
        train_data = lgb.Dataset(X_arr, label=y_arr, feature_name=LGB_FEATURE_NAMES)

        valid_sets = [train_data]
        valid_names = ["train"]
        usable_val = [r for r in (val_rows or []) if r.get("is_win") is not None]
        if len(usable_val) >= 20:
            X_val, y_val = to_xy(usable_val)
            val_data = lgb.Dataset(X_val, label=y_val, feature_name=LGB_FEATURE_NAMES, reference=train_data)
            valid_sets.append(val_data)
            valid_names.append("val")

        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "verbosity": -1,
            "num_leaves": 15,
            "learning_rate": 0.05,
            "min_data_in_leaf": 10,
        }
        callbacks = [lgb.log_evaluation(period=0)]
        if len(valid_sets) > 1:
            callbacks.append(lgb.early_stopping(stopping_rounds=15, verbose=False))
        booster = lgb.train(
            params, train_data, num_boost_round=200,
            valid_sets=valid_sets, valid_names=valid_names, callbacks=callbacks,
        )

        # Report accuracy on the held-out split when we have one — a train-set accuracy
        # number is not a meaningful "is this model any good" metric.
        eval_X, eval_y = (X_val, y_val) if len(valid_sets) > 1 else (X_arr, y_arr)
        preds = booster.predict(eval_X, num_iteration=booster.best_iteration or None)
        pred_labels = [1.0 if p >= 0.5 else 0.0 for p in preds]
        accuracy = sum(1 for p, t in zip(pred_labels, eval_y) if p == t) / len(eval_y)
        eval_split = "val" if len(valid_sets) > 1 else "train"

        self.lgb_model = booster
        self.lgb_trained = True

        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            booster.save_model(LIGHTGBM_MODEL_PATH)
            logger.info(f"Saved LightGBM model checkpoint to {LIGHTGBM_MODEL_PATH}")
        except Exception as e:
            logger.error(f"Error saving LightGBM model: {e}")

        importance = dict(zip(LGB_FEATURE_NAMES, [int(v) for v in booster.feature_importance()]))

        return {
            "trained": True,
            "samples": len(usable),
            "val_samples": len(usable_val),
            "eval_split": eval_split,
            "train_accuracy": round(accuracy * 100.0, 1),
            "feature_importance": importance,
        }

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

    def tune_ensemble(self, val_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Re-picks blend_weight, market_weight and decision_threshold against held-out
        validation data instead of leaving them at whatever they were initialized to
        forever. Called from pipeline.py at the same (now throttled — see
        TUNE_EVERY_CYCLES) cadence as the LightGBM refit, since that's when a
        freshly-fit booster and a validation split are both on hand together.

        - blend_weight / market_weight: 2D grid search over _BLEND_CANDIDATES (w_py,
          w_market pairs with w_py + w_market <= 1; LightGBM gets 1 - w_py - w_market),
          keeping whichever pair minimizes Brier score (mean squared error of the
          blended probability against the true 0/1 outcome) on data neither model
          trained on. market_weight puts the raw bookmaker-implied probability (1/coeff)
          directly in the blend — when the model's own Brier is worse than the market's
          (val_brier_base below; this was consistently true in production), the search
          naturally shifts weight onto the market term instead of an undertrained model.
        - decision_threshold: grid search over _THRESHOLD_CANDIDATES (see
          _sweep_threshold), keeping whichever cutoff on the decision head maximizes a
          sample-size-penalized simulated flat-stake ROI on validation (see
          _THRESHOLD_SAMPLE_PENALTY) — a direct proxy for "does betting on this verdict
          make money," not just "is the verdict usually right," while discounting
          thresholds propped up by a small, lucky sample. Thresholds that would place
          fewer than _MIN_THRESHOLD_BETS bets are skipped entirely; if none qualify, the
          previous threshold is kept.
        - sport_decision_thresholds: the same sweep repeated per top-level sport (see
          sport_threshold()) — a single global cutoff can't be optimal for every sport
          when Brier-vs-market quality varies this much between them (production
          backtests: model beats market on football/basketball/hockey, loses on table
          tennis). Needs _MIN_THRESHOLD_BETS_PER_SPORT val bets *for that sport
          specifically* to update at all; sports that don't clear it this pass simply
          keep whatever they had (or the global decision_threshold, if they've never
          earned a value).
        - Every parameter is smoothed towards this cycle's grid-search result rather
          than snapping to it (see _TUNE_SMOOTH_ALPHA) — the target values are reported
          alongside the smoothed ones so the log line shows both.
        - The tuned scalars are written onto the existing GRU checkpoint immediately
          (see save_ensemble): they must survive a container restart even when the
          next train_online pass is rejected and does not rewrite the file.

        Uses the same random-cutoff trajectory view as training (_prepare_sample) so
        this is evaluating the model the way it actually gets scored elsewhere (val_loss,
        val_guess_rate), not on full-match hindsight.
        """
        prepared = [p for p in (self._prepare_sample(s) for s in val_data) if p is not None]
        if len(prepared) < self._MIN_TUNE_SAMPLES:
            return {"tuned": False, "reason": "not_enough_val_samples", "samples": len(prepared)}

        seqs = torch.tensor([_build_sequence(p["step_pairs"]) for p in prepared], dtype=torch.float32)
        sport_t, market_t, team1_t, team2_t = self._context_tensors(prepared)
        self.pytorch_model.eval()
        with torch.no_grad():
            logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t)
            pytorch_probs = torch.sigmoid(logits[:, 0]).tolist()
            decision_probs = _decision_confidence(logits[:, 1]).tolist()

        targets = [p["target"] for p in prepared]
        coeffs = [p["coefficient"] for p in prepared]
        market_probs = [min(max(1.0 / c, 0.01), 0.99) if c > 1.0 else 0.99 for c in coeffs]

        if self.lgb_trained and self.lgb_model is not None:
            X = []
            for p in prepared:
                coeffs_seen = [sp[0] for sp in p["step_pairs"]]
                X.append(self._lgb_feature_row(
                    p["coefficient"], coeffs_seen[0], min(coeffs_seen), max(coeffs_seen),
                    len(p["step_pairs"]), p.get("factor_id"), p["step_pairs"][-1][1],
                    p.get("sport_idx", 0), p.get("team1_idx", 0), p.get("team2_idx", 0),
                    p.get("overround_close"),
                ))
            lgb_scores = [min(max(float(v), 0.02), 0.98) for v in self.lgb_model.predict(np.array(X, dtype=np.float64))]
        else:
            # No booster yet — every blend weight collapses onto pure pytorch_prob, so
            # the search below still runs, just without LightGBM in the mix.
            lgb_scores = [0.5] * len(prepared)

        # The odds-only baseline (no model at all) — logged alongside the tuned blend's
        # Brier so a regression (model doing worse than just trusting the bookmaker) is
        # visible in every training cycle's log line, not just in an offline export.
        base_brier = sum((mp - t) ** 2 for mp, t in zip(market_probs, targets)) / len(prepared)

        best_blend, best_market, best_brier = self.blend_weight, self.market_weight, float("inf")
        for w_py in self._BLEND_CANDIDATES:
            for w_mkt in self._BLEND_CANDIDATES:
                if w_py + w_mkt > 1.0 + 1e-9:
                    continue
                w_lgb = 1.0 - w_py - w_mkt
                brier = sum(
                    (w_py * gp + w_mkt * mp + w_lgb * lp - t) ** 2
                    for gp, mp, lp, t in zip(pytorch_probs, market_probs, lgb_scores, targets)
                ) / len(prepared)
                if brier < best_brier:
                    best_brier = brier
                    best_blend, best_market = w_py, w_mkt

        best_threshold, best_roi, best_bets = self._sweep_threshold(
            decision_probs, coeffs, targets, self.decision_threshold, self._MIN_THRESHOLD_BETS,
        )

        old_blend, old_market, old_threshold = self.blend_weight, self.market_weight, self.decision_threshold
        a = self._TUNE_SMOOTH_ALPHA
        self.blend_weight = old_blend + a * (best_blend - old_blend)
        self.market_weight = old_market + a * (best_market - old_market)
        if best_bets >= self._MIN_THRESHOLD_BETS:
            self.decision_threshold = max(
                VALUE_THRESHOLD_FLOOR,
                old_threshold + a * (best_threshold - old_threshold),
            )

        # Per-sport decision_threshold — same sweep, run again per sport group of the
        # same val samples (see sport_threshold()'s docstring for why: a single global
        # cutoff can't be optimal for every sport when Brier-vs-market quality varies
        # this much between them). Groups with too few val bets this pass are left
        # untouched — they keep whatever they had before (or the global fallback if
        # they've never earned a value at all), not overwritten with a worse guess.
        by_sport: Dict[str, List[int]] = {}
        for idx, p in enumerate(prepared):
            by_sport.setdefault(p.get("sport") or "Другое", []).append(idx)

        sport_threshold_report: Dict[str, Dict[str, Any]] = {}
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
        persisted = self.save_ensemble()

        return {
            "tuned": True,
            "persisted": persisted,
            "samples": len(prepared),
            "val_brier_base": round(base_brier, 4),
            "blend_weight": {
                "old": round(old_blend, 2), "new": round(self.blend_weight, 2),
                "target": round(best_blend, 2), "val_brier": round(best_brier, 4),
            },
            "market_weight": {
                "old": round(old_market, 2), "new": round(self.market_weight, 2), "target": round(best_market, 2),
            },
            "decision_threshold": {
                "old": round(old_threshold, 2), "new": round(self.decision_threshold, 2),
                "target": round(best_threshold, 2) if best_bets >= self._MIN_THRESHOLD_BETS else None,
                "val_roi_pct": round(best_roi * 100.0, 1) if best_bets >= self._MIN_THRESHOLD_BETS else None,
                "val_bets": best_bets,
            },
            # Only sports that actually cleared _MIN_THRESHOLD_BETS_PER_SPORT this pass
            # appear here — most won't, on any given pass, since the val batch splits
            # across 12+ sports and only the couple of largest ones (table tennis,
            # basketball) reliably clear the floor. That's expected, not an error: every
            # other sport keeps using its last tuned value (or decision_threshold, if it
            # has never earned one) via sport_threshold() — see that method's docstring.
            "sport_decision_threshold": sport_threshold_report,
        }

    def _sweep_threshold(
        self, decision_probs: List[float], coeffs: List[float], targets: List[float],
        current: float, min_bets: int,
    ) -> Tuple[float, Optional[float], int]:
        """
        Grid search over _THRESHOLD_CANDIDATES for the flat-stake-ROI-maximizing cutoff
        (sample-size-penalized — see _THRESHOLD_SAMPLE_PENALTY), shared by tune_ensemble's
        global sweep and each of its per-sport sweeps so the two can never drift onto
        different selection logic. Returns (threshold, roi, n_bets) for whichever
        candidate scored best; falls back to (`current`, None, 0) if no candidate
        cleared `min_bets` — the caller decides what "no qualifying candidate" means
        (global: keep the old threshold; per-sport: leave that sport untouched this pass).
        """
        best_threshold, best_score, best_roi, best_bets = current, float("-inf"), None, 0
        for thr in self._THRESHOLD_CANDIDATES:
            staked = 0.0
            returned = 0.0
            n_bets = 0
            for dp, c, t in zip(decision_probs, coeffs, targets):
                if dp < thr or not in_bet_band(c):
                    continue
                n_bets += 1
                staked += 1.0
                if t >= 0.5:
                    returned += c
            if n_bets < min_bets:
                continue
            roi = (returned - staked) / staked
            score = roi - self._THRESHOLD_SAMPLE_PENALTY / (n_bets ** 0.5)
            if score > best_score:
                best_score = score
                best_roi = roi
                best_threshold = thr
                best_bets = n_bets
        return best_threshold, best_roi, best_bets

    def _prepare_sample(self, sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Builds one training example from a resolved bet, cutting its odds/score history
        off at a random point instead of using the whole match — the model only ever
        sees a partial trajectory at real inference time (the match is still live), so
        training must match that (see plan section B3). Returns None for bets with no
        usable history.
        """
        if sample.get("is_win") is None:
            return None
        odds_seq = sample.get("odds_seq") or []
        score_seq = sample.get("score_seq") or []
        if not odds_seq:
            return None
        if len(score_seq) != len(odds_seq):
            # Older archived bets predate score_seq_json — fall back to a flat score_diff.
            fallback = sample.get("score_diff_at_bet", 0)
            score_seq = [fallback] * len(odds_seq)
        ts_seq = sample.get("ts_seq") or []
        if len(ts_seq) != len(odds_seq):
            # Bets archived before ts_seq_json (migration 0004) — all-None makes
            # _build_sequence use its positional time fallback for this sample.
            ts_seq = [None] * len(odds_seq)
        timer_seq = sample.get("timer_seq") or []
        if len(timer_seq) != len(odds_seq):
            # Bets archived before timer_seq_json (migration 0005) — all-None makes
            # _build_sequence use MATCH_TIME_UNKNOWN for every step of this sample.
            timer_seq = [None] * len(odds_seq)

        n = len(odds_seq)
        cutoff = random.randint(min(3, n), n) if n > 1 else n
        pairs = list(zip(odds_seq[:cutoff], score_seq[:cutoff], ts_seq[:cutoff], timer_seq[:cutoff]))

        return {
            "step_pairs": pairs,
            "target": float(sample["is_win"]),
            "coefficient": float(odds_seq[cutoff - 1]),
            "factor_id": sample.get("factor_id"),
            # Which event this outcome belongs to, so the bankroll replay can refuse to
            # stake two positions on the same match in one round (see _bankroll_pass) —
            # not just strictly mutually-exclusive outcomes, but any two markets on the
            # same event, since even non-exclusive ones are routinely correlated (e.g.
            # "П1 wins" and "team 2's individual total over 2.5" pull against each
            # other). None when missing — such a sample simply never conflicts with
            # anything, which is the safe default.
            "conflict_key": sample.get("event_id"),
            "overround_close": sample.get("overround_close"),
            # Top-level sport as text (not just the embedding index below) — used by
            # tune_ensemble to group validation samples for per-sport decision_threshold
            # tuning (see NeuralBetEnsemble.sport_threshold). Same grouping
            # calibration.py/backtest.py already use, kept consistent deliberately.
            "sport": (sample.get("sport_path") or "").split("/")[0].strip() or "Другое",
            "sport_idx": sport_index(sample.get("sport_path")),
            "market_idx": market_family_index(sample.get("factor_id")),
            "team1_idx": team_index(sample.get("team_1")),
            "team2_idx": team_index(sample.get("team_2")),
        }

    @staticmethod
    def _context_tensors(prepared: List[Dict[str, Any]]) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        sport_t = torch.tensor([p.get("sport_idx", 0) for p in prepared], dtype=torch.long)
        market_t = torch.tensor([p.get("market_idx", 0) for p in prepared], dtype=torch.long)
        team1_t = torch.tensor([p.get("team1_idx", 0) for p in prepared], dtype=torch.long)
        team2_t = torch.tensor([p.get("team2_idx", 0) for p in prepared], dtype=torch.long)
        return sport_t, market_t, team1_t, team2_t

    def _forward_metrics(self, prepared: List[Dict[str, Any]]) -> Tuple[float, float]:
        """
        Returns (decision_loss, hit_rate) for a prepared batch in eval mode, no grad.
        Decision loss is MSE of predicted residual (tanh) vs (is_win - 1/coeff).
        Hit rate is how often a +EV verdict (predicted edge >= 0) actually won — the
        number that matters once the head is a bet/no-bet gate, not a 50/50 classifier
        on complementary markets.
        Chunked by BATCH_SIZE: a single tensor of a 280k-row cold-start pool OOMs
        the AI worker right after early-stopping, which then restarts epoch 1 forever.
        """
        if not prepared:
            return float("nan"), float("nan")
        loss_sum = 0.0
        loss_n = 0
        hit_sum = 0.0
        hit_n = 0
        self.pytorch_model.eval()
        with torch.no_grad():
            for i in range(0, len(prepared), BATCH_SIZE):
                chunk = prepared[i:i + BATCH_SIZE]
                seqs = torch.tensor(
                    [_build_sequence(p["step_pairs"]) for p in chunk], dtype=torch.float32
                )
                targets = torch.tensor([p["target"] for p in chunk], dtype=torch.float32)
                coeffs = torch.tensor(
                    [p["coefficient"] for p in chunk], dtype=torch.float32
                )
                sport_t, market_t, team1_t, team2_t = self._context_tensors(chunk)
                logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t)[:, 1]
                pred_edge = torch.tanh(logits)
                true_edge = targets - _market_prob_tensor(coeffs)
                verdict_mask = coeffs < MAX_BET_COEFF
                n_verdict = int(verdict_mask.sum().item())
                if n_verdict:
                    chunk_loss = nn.functional.mse_loss(
                        pred_edge[verdict_mask], true_edge[verdict_mask]
                    ).item()
                    loss_sum += chunk_loss * n_verdict
                    loss_n += n_verdict
                would_bet = (
                    (pred_edge >= 0)
                    & (coeffs >= MIN_BET_COEFF)
                    & (coeffs <= MAX_BET_COEFF)
                )
                n_bet = int(would_bet.sum().item())
                if n_bet:
                    hit_sum += float((targets[would_bet] >= 0.5).sum().item())
                    hit_n += n_bet
        loss = (loss_sum / loss_n) if loss_n else float("nan")
        hit_rate = (hit_sum / hit_n) if hit_n else 0.0
        return loss, hit_rate

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
                seqs = torch.tensor([_build_sequence(c["step_pairs"]) for c in chunk], dtype=torch.float32)
                sport_t, market_t, team1_t, team2_t = self._context_tensors(chunk)
                logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t)
                win_probs = torch.sigmoid(logits[:, 0])
                stake_logits = logits[:, 2]
                coeffs = torch.tensor([c["coefficient"] for c in chunk], dtype=torch.float32)
                wins = torch.tensor([c["target"] for c in chunk], dtype=torch.float32)

                fractions = bankroll.allocate(win_probs, coeffs, stake_logits)
                # Only actually risk money on candidates the model's own verdict says will
                # win — see decision_logit in OddsTrajectoryGRU. The mask is a hard,
                # non-differentiable decision (detached), same as allocate()'s own
                # min-stake/max-positions cuts; gradient into stake_logits still flows
                # through the softmax weights of whichever candidates the mask keeps.
                verdict = (_decision_confidence(logits[:, 1]) >= VALUE_THRESHOLD_FLOOR).float().detach()
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

    def train_online(
        self,
        training_data: List[Dict[str, Any]],
        val_data: Optional[List[Dict[str, Any]]] = None,
        epochs: int = MAX_EPOCHS,
        on_epoch: Any = None,
        chart_val_loss: Optional[float] = None,
        skip_checkpoint_gate: bool = False,
        learning_rate: Optional[float] = None,
        rebalance_classes: bool = True,
        should_abort: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Online-training pass: mini-batch AdamW over a batch of resolved bets, combining
        three loss terms —
          1. BCE-with-logits on the win/loss label for the win-probability head (still
             feeds the LightGBM/PyTorch ensemble blend used for the displayed percentage),
          2. MSE on the *decision* head — predicted residual vs the bookmaker
             (tanh(decision_logit) vs is_win - 1/coeff). This is the bet/no-bet
             verdict: above 0.5 mapped confidence means "the market underprices this",
             not "this line will win". Complementary two-way markets are 50/50 by
             construction, so BCE-on-is_win had no headroom.
          3. a bankroll loss: -log(bank_growth) replayed over rounds of candidates using
             the model's own decision/stake/exposure heads (bankroll.allocate/settle,
             masked by the decision verdict — see _bankroll_pass) — this is what teaches
             the network that betting size matters, not just direction (see plan part C).
             Ruin (bank hits 0) adds a heavy penalty and resets the *virtual* training
             bank used for this loss back to start_balance.
        Runs up to `epochs` passes but keeps the weights from whichever epoch had the
        best held-out (val_data) decision-loss — early-stopping model selection — since
        the hardware budget allows a generous epoch count without every extra epoch
        being pure overfitting. `on_epoch(epoch_index, train_loss, val_loss)` is called
        after every epoch if provided.
        After picking the winning epoch, allocations are replayed on the held-out
        val split (not the train set) and committed to the persistent "training"
        bankroll account, so the logged balance is a generalization check rather
        than an in-sample Kelly compound.
        The pass is then compared to the *incoming* weights on that same val split
        and to `chart_val_loss` (last saved checkpoint). Fail either check → restore,
        file untouched. Next cycle continues from the restored weights on new data.
        `skip_checkpoint_gate` keeps the new weights regardless (cold-start through
        the archive: a later chunk is allowed to raise val_loss without rolling back
        everything learned so far). `learning_rate` temporarily overrides AdamW's
        param-group rate for this pass, then puts LEARNING_RATE back. `rebalance_classes`
        toggles BCE pos_weight; leave it off when the caller already drew a harsh
        win/loss mix, or the weight would cancel that mix back to 50/50.
        """
        empty_metrics = {
            "samples_used": 0, "samples_skipped": len(training_data), "positive_count": 0,
            "negative_count": 0, "epochs_run": 0, "epoch_losses": [], "initial_loss": None,
            "final_loss": None, "train_guess_rate": None, "val_loss": None, "val_guess_rate": None,
            "best_epoch": None, "bankroll": None, "checkpoint_accepted": None,
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

        prepared_val = [p for p in (self._prepare_sample(s) for s in (val_data or [])) if p is not None]

        incoming_state = None
        val_incoming = None
        val_incoming_guess = None
        if prepared_val:
            incoming_state = self._snapshot_train_state()
            val_incoming, val_incoming_guess = self._forward_metrics(prepared_val)

        positive_count = sum(1 for p in prepared if p["target"] == 1.0)
        negative_count = len(prepared) - positive_count
        # pos_weight = n_neg/n_pos undoes a caller-imposed class mix (a 10/90 batch
        # would be trained as if it were 50/50). Cold-start keeps it; online random
        # mix does not.
        if rebalance_classes and positive_count and negative_count:
            pos_weight = torch.tensor(
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
                order = list(range(len(prepared)))
                random.shuffle(order)
                epoch_train_losses = []

                for start in range(0, len(order), BATCH_SIZE):
                    if should_abort is not None and should_abort():
                        aborted = True
                        break
                    batch_idx = order[start:start + BATCH_SIZE]
                    batch = [prepared[i] for i in batch_idx]
                    seqs = torch.tensor([_build_sequence(b["step_pairs"]) for b in batch], dtype=torch.float32)
                    targets = torch.tensor([b["target"] for b in batch], dtype=torch.float32).unsqueeze(1)
                    sport_t, market_t, team1_t, team2_t = self._context_tensors(batch)

                    self.pytorch_optimizer.zero_grad()
                    logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t)
                    win_logits = logits[:, 0:1]
                    bce_loss = bce(win_logits, targets)

                    # Decision-head loss: MSE on residual vs the bookmaker (is_win - 1/coeff),
                    # not BCE on is_win — complementary two-way markets are 50/50 by construction.
                    decision_logits = logits[:, 1]
                    coeffs_t = torch.tensor([b["coefficient"] for b in batch], dtype=torch.float32)
                    pred_edge = torch.tanh(decision_logits)
                    true_edge = targets.squeeze(1) - _market_prob_tensor(coeffs_t)
                    # Longs (>= MAX_BET_COEFF) are a free "will lose" label that drowns the
                    # residual head — keep them on the win-probability BCE, drop them here.
                    verdict_mask = coeffs_t < MAX_BET_COEFF
                    per = (pred_edge - true_edge) ** 2
                    if int(verdict_mask.sum().item()) == 0:
                        decision_loss = per.new_zeros(())
                    else:
                        decision_loss = per[verdict_mask].mean()

                    loss = bce_loss + DECISION_LOSS_WEIGHT * decision_loss

                    bank_pass = self._bankroll_pass(batch, account="training", commit=False)
                    if bank_pass["loss"] is not None:
                        loss = loss + bankroll.BANKROLL_LOSS_WEIGHT * bank_pass["loss"]

                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.pytorch_model.parameters(), GRAD_CLIP_NORM)
                    self.pytorch_optimizer.step()
                    epoch_train_losses.append(float(decision_loss.item()))

                if aborted:
                    break

                train_loss = sum(epoch_train_losses) / max(len(epoch_train_losses), 1)
                epoch_losses.append(train_loss)

                val_loss, val_guess_rate = self._forward_metrics(prepared_val) if prepared_val else (train_loss, None)
                selection_metric = val_loss if prepared_val else train_loss

                logger.info(
                    f"PyTorch online training epoch {epoch_idx}/{epochs} — "
                    f"train_loss: {train_loss:.4f}, val_loss: {val_loss:.4f}"
                )
                if on_epoch is not None:
                    try:
                        on_epoch(epoch_idx, train_loss, val_loss if prepared_val else None)
                    except Exception:
                        pass

                if selection_metric < best_val - 1e-4:
                    best_val = selection_metric
                    best_state = copy.deepcopy(self.pytorch_model.state_dict())
                    best_epoch = epoch_idx
                    epochs_without_improvement = 0
                else:
                    epochs_without_improvement += 1
                    if epochs_without_improvement >= EARLY_STOP_PATIENCE:
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

        final_val_loss, final_val_guess_rate = self._forward_metrics(prepared_val) if prepared_val else (None, None)
        final_train_loss, final_train_guess_rate = self._forward_metrics(prepared)

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
            f"train_loss {final_train_loss:.4f}, val_loss "
            f"{final_val_loss if final_val_loss is not None else float('nan'):.4f}, "
            f"training bankroll {bank_result['bank_start']:.1f} -> {bank_result['bank_end']:.1f}."
        )

        checkpoint_accepted = True
        checkpoint_reject_reason = None
        val_loss_attempted = final_val_loss
        if (
            not skip_checkpoint_gate
            and incoming_state is not None
            and final_val_loss is not None
            and val_incoming is not None
        ):
            if final_val_loss >= val_incoming - 1e-4:
                checkpoint_accepted = False
                checkpoint_reject_reason = "incoming"
                logger.info(
                    f"Online pass did not beat incoming val_loss "
                    f"(attempt {val_loss_attempted:.4f} >= {val_incoming:.4f}); "
                    f"restored previous weights, checkpoint file unchanged."
                )
            elif (
                chart_val_loss is not None
                and final_val_loss >= chart_val_loss - 1e-4
            ):
                checkpoint_accepted = False
                checkpoint_reject_reason = "chart"
                logger.info(
                    f"Online pass beat incoming {val_incoming:.4f} but not the "
                    f"saved checkpoint val_loss {chart_val_loss:.4f} "
                    f"(attempt {val_loss_attempted:.4f}); "
                    f"restored previous weights, checkpoint file unchanged."
                )
            if not checkpoint_accepted:
                self._restore_train_state(*incoming_state)

        if checkpoint_accepted:
            self.save_checkpoints(extra={"best_epoch": best_epoch})

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
