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


@dataclass
class MineralTrajectoryBundle:
    """Everything needed to predict trajectories for one mineral.

    Wraps :class:`DeepONet` with input/output scaling and the
    per-mineral target_column / horizon metadata.  The ``state_dict`` of
    the underlying torch module is held separately so the bundle
    pickles cleanly without dragging in the live torch graph.
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

    def build_model(self) -> DeepONet:
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
# Inference helper
# ---------------------------------------------------------------------------

@torch.no_grad()
def predict_trajectory(
    bundle: MineralTrajectoryBundle,
    features: np.ndarray,
    timesteps: np.ndarray | None = None,
    device: str | torch.device = "cpu",
) -> np.ndarray:
    """Predict one or many trajectories at the given timesteps.

    Args:
        bundle: trained :class:`MineralTrajectoryBundle`.
        features: (B, F) scenario feature vectors, or (F,) for one.
        timesteps: integer step indices in ``[0, n_steps)`` to
            evaluate at.  Default = full horizon ``arange(n_steps)``.
        device: torch device for inference.

    Returns:
        (B, T) array of predicted target values in raw units (price for
        the v1 model).  If ``features`` was 1D, returned shape is (T,).
    """
    model = bundle.build_model().to(device).eval()
    feats = torch.as_tensor(features, dtype=torch.float32, device=device)
    if feats.dim() == 1:
        feats = feats.unsqueeze(0)
        squeeze = True
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
    y_hat_norm = model(feats_rep, t_rep).reshape(B, T)
    y_hat = bundle.denormalise(y_hat_norm).cpu().numpy()
    return y_hat[0] if squeeze else y_hat
