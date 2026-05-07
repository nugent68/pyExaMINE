"""DeepONet for the trajectory surrogate.

Architecture (Lu, Jin, Pang, Zhang, Karniadakis 2021):

    predicted(scenario, t) = bias + sum_k branch_k(scenario) * trunk_k(t_norm)

The branch network ingests the scenario feature vector
(``ft.encode(scenario)``) and emits ``P`` "basis coefficients".  The
trunk network ingests the normalized timestep ``t / (T - 1)`` and emits
``P`` "basis function values".  Their per-element product is summed to
yield a scalar prediction per (scenario, t) query.

The trunk uses sinusoidal embeddings before the MLP so a small dense
network can resolve fast features in time without needing a large
basis dimension.

Per-mineral instances: feature dimension differs across minerals
(75 for platinum, 95 for lithium, 105 for nickel) so each mineral gets
its own bundle.  The trunk is the same shape per mineral; only the
branch input width changes.

Output normalization: prices span very different scales across minerals
($10k Li/Ni vs $35M Pt) so we wrap the model with a per-mineral
log-target scaler.  Training is on log-prices, predictions are
exp-transformed back at inference.  Lives in
:class:`MineralTrajectoryBundle`.

**Event-phase augmentation** (v2): the v1 model failed on peaks /
recovery / event-onset alignment because the smooth basis can't
represent step-changes at event boundaries.  The cheap fix is to feed
the trunk a few scenario-derived "event phase" features alongside t:

    in_any_event(t)              -- 1 if any event is active at t
    rel_time_first_start(t)      -- (t - first_event_start) / T, clamped
    rel_time_last_end(t)         -- (t - last_event_end) / T, clamped

These are scenario-specific functions of t, which means the trunk is
no longer a pure function of time -- it sees enough about the
scenario's event timeline to switch behaviour at event boundaries
without needing high-frequency Fourier modes.  The branch still
carries the static parameter encoding.  See :class:`DeepONetPhase`.
"""

from __future__ import annotations

import math
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn


def _make_mlp(
    in_dim: int, out_dim: int, hidden: list[int], activation=nn.GELU,
) -> nn.Sequential:
    """Plain feedforward MLP with the given hidden widths."""
    layers: list[nn.Module] = []
    prev = in_dim
    for h in hidden:
        layers.append(nn.Linear(prev, h))
        layers.append(activation())
        prev = h
    layers.append(nn.Linear(prev, out_dim))
    return nn.Sequential(*layers)


class TimeEmbedding(nn.Module):
    """Sinusoidal embedding of t in [0, 1] -> R^D.

    Same trick as Transformer positional encoding but on a continuous
    input rather than integer positions.  Frequencies span four decades
    so a small network can resolve sharp event-onset transitions.
    """

    def __init__(self, dim: int = 64, max_freq: float = 1024.0) -> None:
        super().__init__()
        if dim % 2:
            raise ValueError(f"TimeEmbedding dim must be even, got {dim}")
        half = dim // 2
        # Logarithmically-spaced frequencies between 1 and max_freq.
        freqs = torch.exp(
            torch.linspace(0.0, math.log(max_freq), half)
        ) * math.pi
        self.register_buffer("freqs", freqs)

    def forward(self, t: torch.Tensor) -> torch.Tensor:
        # t shape: (B,) or (B, 1)
        if t.dim() == 1:
            t = t.unsqueeze(-1)
        # (B, half)
        phases = t * self.freqs
        return torch.cat([torch.sin(phases), torch.cos(phases)], dim=-1)


class DeepONet(nn.Module):
    """Branch * trunk DeepONet over a learned ``basis_dim``-dim basis."""

    def __init__(
        self,
        feature_dim: int,
        basis_dim: int = 64,
        branch_hidden: list[int] | None = None,
        trunk_hidden: list[int] | None = None,
        time_embed_dim: int = 64,
    ) -> None:
        super().__init__()
        branch_hidden = branch_hidden or [256, 256]
        trunk_hidden = trunk_hidden or [128, 128]
        self.basis_dim = basis_dim
        self.branch = _make_mlp(feature_dim, basis_dim, branch_hidden)
        self.time_embed = TimeEmbedding(time_embed_dim)
        self.trunk = _make_mlp(time_embed_dim, basis_dim, trunk_hidden)
        # Scalar bias absorbed into the model so we can learn it
        # alongside everything else.
        self.bias = nn.Parameter(torch.zeros(1))

    def forward(self, features: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        """Predict y(scenario, t).

        Args:
            features: (B, F) scenario feature vectors.
            t: (B,) normalized timesteps in [0, 1].

        Returns:
            (B,) predicted scalar.
        """
        b = self.branch(features)               # (B, P)
        emb = self.time_embed(t)                # (B, D)
        u = self.trunk(emb)                     # (B, P)
        return (b * u).sum(dim=-1) + self.bias


#: Number of scenario-derived event-phase features fed to the trunk
#: in :class:`DeepONetPhase`.  See :func:`compute_phase_features`.
PHASE_FEATURE_DIM: int = 3


def compute_phase_features(
    t_norm: torch.Tensor,
    event_starts_norm: torch.Tensor,    # (..., max_events)
    event_ends_norm: torch.Tensor,      # (..., max_events)
    event_active: torch.Tensor,         # (..., max_events) bool
) -> torch.Tensor:
    """Compute (in_any_event, rel_first_start, rel_last_end) given t.

    Inputs are batched along leading dims; ``max_events`` is the slot
    count over which we aggregate (we keep both embargoes and
    chokepoint-crises in a single flattened slot list).  Slots not
    in use have ``event_active=False`` and are ignored in the
    aggregations.

    Returns: ``(..., 3)`` float32 tensor with the three phase scalars.
    """
    # Broadcast t_norm to (..., 1) so it lines up with the event tensors.
    t = t_norm.unsqueeze(-1)
    big = torch.full_like(event_starts_norm, 1e6)
    starts = torch.where(event_active, event_starts_norm, big)
    ends = torch.where(event_active, event_ends_norm, -big)

    # in_any_event: are we inside *any* active slot at time t?
    in_event_per_slot = event_active & (t >= event_starts_norm) & (t < event_ends_norm)
    in_any = in_event_per_slot.any(dim=-1).float()

    # Earliest start (only over active slots; defaults to 0 when none).
    first_start = starts.min(dim=-1).values
    has_any = event_active.any(dim=-1)
    first_start = torch.where(has_any, first_start, torch.zeros_like(first_start))
    rel_first_start = torch.clamp(t.squeeze(-1) - first_start, min=-1.0, max=1.0)

    # Latest end (only over active slots; defaults to 0 when none).
    last_end = ends.max(dim=-1).values
    last_end = torch.where(has_any, last_end, torch.zeros_like(last_end))
    rel_last_end = torch.clamp(t.squeeze(-1) - last_end, min=-1.0, max=1.0)

    return torch.stack([in_any, rel_first_start, rel_last_end], dim=-1)


class DeepONetPhase(nn.Module):
    """DeepONet with event-phase features injected into the trunk.

    Identical to :class:`DeepONet` except the trunk MLP ingests
    ``time_embed_dim + PHASE_FEATURE_DIM`` inputs.  At inference the
    caller is responsible for supplying the (per-(scenario, t)) phase
    features alongside ``t`` -- the dataset and prediction helpers
    in this module take care of that automatically.
    """

    def __init__(
        self,
        feature_dim: int,
        basis_dim: int = 64,
        branch_hidden: list[int] | None = None,
        trunk_hidden: list[int] | None = None,
        time_embed_dim: int = 64,
    ) -> None:
        super().__init__()
        branch_hidden = branch_hidden or [256, 256]
        trunk_hidden = trunk_hidden or [128, 128]
        self.basis_dim = basis_dim
        self.branch = _make_mlp(feature_dim, basis_dim, branch_hidden)
        self.time_embed = TimeEmbedding(time_embed_dim)
        self.trunk = _make_mlp(
            time_embed_dim + PHASE_FEATURE_DIM, basis_dim, trunk_hidden,
        )
        self.bias = nn.Parameter(torch.zeros(1))

    def forward(
        self,
        features: torch.Tensor,
        t: torch.Tensor,
        phase: torch.Tensor,
    ) -> torch.Tensor:
        """Predict y(scenario, t) with event-phase conditioning.

        Args:
            features: (B, F) scenario feature vectors.
            t: (B,) normalized timesteps in [0, 1].
            phase: (B, PHASE_FEATURE_DIM) per-(scenario, t) phase features.

        Returns:
            (B,) predicted scalar.
        """
        b = self.branch(features)                   # (B, P)
        emb = self.time_embed(t)                    # (B, D)
        u = self.trunk(torch.cat([emb, phase], dim=-1))  # (B, P)
        return (b * u).sum(dim=-1) + self.bias


# ---------------------------------------------------------------------------
# Bundle (mirror of MineralModelBundle in train_scalar.py)
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryMetrics:
    """Held-out test-set metrics for one trajectory model."""
    n_train_csvs: int
    n_val_csvs: int
    n_test_csvs: int
    train_loss: float
    val_loss: float
    test_loss: float
    test_rmse: float                # in price units (de-logged)
    test_mae: float                 # in price units
    test_relative_rmse: float       # rmse / mean(|y|)


#: Architecture variants the bundle understands.  ``"deeponet"`` is
#: the v1 trunk-of-time-only model; ``"deeponet_phase"`` adds the
#: scenario-derived event-phase features.
ARCH_DEEPONET: str = "deeponet"
ARCH_DEEPONET_PHASE: str = "deeponet_phase"


@dataclass
class MineralTrajectoryBundle:
    """Everything needed to predict trajectories for one mineral.

    Wraps a torch model with input/output scaling and the per-mineral
    target_column / horizon metadata.  The ``state_dict`` of the
    underlying torch module is held separately so the bundle pickles
    cleanly without dragging in the live torch graph.

    The ``arch`` field selects between :class:`DeepONet` and
    :class:`DeepONetPhase`; older bundles pickled before the field
    existed default to ``"deeponet"``.
    """
    mineral: str
    feature_dim: int
    basis_dim: int
    branch_hidden: list[int]
    trunk_hidden: list[int]
    time_embed_dim: int
    target_column: str
    n_steps: int
    log_target: bool
    target_log_mean: float          # log-target normalization
    target_log_std: float
    state_dict: dict[str, torch.Tensor] = field(default_factory=dict)
    metrics: TrajectoryMetrics | None = None
    seed: int = 0
    arch: str = ARCH_DEEPONET

    def is_phase(self) -> bool:
        return getattr(self, "arch", ARCH_DEEPONET) == ARCH_DEEPONET_PHASE

    def build_model(self) -> nn.Module:
        if self.is_phase():
            m: nn.Module = DeepONetPhase(
                feature_dim=self.feature_dim,
                basis_dim=self.basis_dim,
                branch_hidden=list(self.branch_hidden),
                trunk_hidden=list(self.trunk_hidden),
                time_embed_dim=self.time_embed_dim,
            )
        else:
            m = DeepONet(
                feature_dim=self.feature_dim,
                basis_dim=self.basis_dim,
                branch_hidden=list(self.branch_hidden),
                trunk_hidden=list(self.trunk_hidden),
                time_embed_dim=self.time_embed_dim,
            )
        if self.state_dict:
            m.load_state_dict(self.state_dict)
        return m

    def normalise(self, y: torch.Tensor) -> torch.Tensor:
        """Map raw target values to the model's training space."""
        if self.log_target:
            y = torch.log(torch.clamp(y, min=1e-9))
        return (y - self.target_log_mean) / max(1e-9, self.target_log_std)

    def denormalise(self, y_hat: torch.Tensor) -> torch.Tensor:
        """Map a model prediction back to raw target units."""
        y_hat = y_hat * self.target_log_std + self.target_log_mean
        if self.log_target:
            y_hat = torch.exp(y_hat)
        return y_hat


def save_bundle(bundle: MineralTrajectoryBundle, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        pickle.dump(bundle, f)


def load_bundle(path: Path) -> MineralTrajectoryBundle:
    with path.open("rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# Scenario -> event-tensor helpers (for the phase variant)
# ---------------------------------------------------------------------------

#: Maximum event slots consumed by :class:`DeepONetPhase`.  Matches the
#: cap in :mod:`surrogate.features` (5 embargoes + 3 chokepoints =
#: 8 simultaneous events).  Scenarios with more events are truncated.
MAX_EVENT_SLOTS: int = 8


def scenario_event_tensors(
    scenario: dict, n_steps: int,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Return per-scenario (starts_norm, ends_norm, active) tensors.

    Each shape: ``(MAX_EVENT_SLOTS,)``.  ``starts_norm`` and
    ``ends_norm`` are normalized by ``n_steps`` (so values lie in
    ``[0, ~1.5]`` -- end can exceed 1 if the simulation cuts off mid-
    embargo).  ``active`` is a bool mask of which slots are populated.
    """
    starts: list[float] = []
    ends: list[float] = []
    horizon = max(1, n_steps - 1)
    for e in (scenario.get("embargoes") or []):
        s = float(e["start_step"]); d = float(e["duration"])
        starts.append(s / horizon); ends.append((s + d) / horizon)
    for c in (scenario.get("chokepoint_crises") or []):
        s = float(c["start_step"]); d = float(c["duration"])
        starts.append(s / horizon); ends.append((s + d) / horizon)
    n = min(len(starts), MAX_EVENT_SLOTS)
    out_starts = torch.zeros(MAX_EVENT_SLOTS, dtype=torch.float32)
    out_ends = torch.zeros(MAX_EVENT_SLOTS, dtype=torch.float32)
    out_active = torch.zeros(MAX_EVENT_SLOTS, dtype=torch.bool)
    if n > 0:
        out_starts[:n] = torch.tensor(starts[:n], dtype=torch.float32)
        out_ends[:n] = torch.tensor(ends[:n], dtype=torch.float32)
        out_active[:n] = True
    return out_starts, out_ends, out_active


# ---------------------------------------------------------------------------
# Inference helper
# ---------------------------------------------------------------------------

@torch.no_grad()
def predict_trajectory(
    bundle: MineralTrajectoryBundle,
    features: np.ndarray,
    timesteps: np.ndarray | None = None,
    device: str | torch.device = "cpu",
    scenarios: list[dict] | None = None,
) -> np.ndarray:
    """Predict one or many trajectories at the given timesteps.

    Args:
        bundle: trained :class:`MineralTrajectoryBundle`.
        features: (B, F) scenario feature vectors, or (F,) for one.
        timesteps: integer step indices in ``[0, n_steps)`` to
            evaluate at.  Default = full horizon ``arange(n_steps)``.
        device: torch device for inference.
        scenarios: required for phase bundles -- a list of length B
            of scenario dicts (in the schema ``ft.encode`` consumes)
            so the phase features can be derived.  Ignored for
            v1 ``"deeponet"`` bundles.

    Returns:
        (B, T) array of predicted target values in raw units (price for
        the v1 model).  If ``features`` was 1D, returned shape is (T,).
    """
    model = bundle.build_model().to(device).eval()
    feats = torch.as_tensor(features, dtype=torch.float32, device=device)
    if feats.dim() == 1:
        feats = feats.unsqueeze(0)
        squeeze = True
        if scenarios is not None and not isinstance(scenarios, list):
            scenarios = [scenarios]
    else:
        squeeze = False
    B, F = feats.shape
    if F != bundle.feature_dim:
        raise ValueError(
            f"feature width mismatch: got {F}, bundle expects {bundle.feature_dim}"
        )

    steps = (
        torch.arange(bundle.n_steps, device=device)
        if timesteps is None
        else torch.as_tensor(timesteps, dtype=torch.long, device=device)
    )
    T = steps.shape[0]
    horizon = max(1, bundle.n_steps - 1)
    t_norm = steps.to(torch.float32) / horizon

    # Vectorise over (B, T).
    feats_rep = feats.unsqueeze(1).expand(B, T, F).reshape(B * T, F)
    t_rep = t_norm.unsqueeze(0).expand(B, T).reshape(B * T)

    if bundle.is_phase():
        if scenarios is None or len(scenarios) != B:
            raise ValueError(
                "phase bundles need a `scenarios=` list of length B"
            )
        # (B, MAX_EVENT_SLOTS) per-event scenarios -> (B*T, 3) phase.
        starts, ends, active = zip(*[
            scenario_event_tensors(s, bundle.n_steps) for s in scenarios
        ])
        starts_t = torch.stack(list(starts)).to(device)         # (B, S)
        ends_t = torch.stack(list(ends)).to(device)
        active_t = torch.stack(list(active)).to(device)
        starts_rep = starts_t.unsqueeze(1).expand(B, T, MAX_EVENT_SLOTS).reshape(B * T, -1)
        ends_rep = ends_t.unsqueeze(1).expand(B, T, MAX_EVENT_SLOTS).reshape(B * T, -1)
        active_rep = active_t.unsqueeze(1).expand(B, T, MAX_EVENT_SLOTS).reshape(B * T, -1)
        phase = compute_phase_features(t_rep, starts_rep, ends_rep, active_rep)
        y_hat_norm = model(feats_rep, t_rep, phase).reshape(B, T)
    else:
        y_hat_norm = model(feats_rep, t_rep).reshape(B, T)
    y_hat = bundle.denormalise(y_hat_norm).cpu().numpy()
    return y_hat[0] if squeeze else y_hat
