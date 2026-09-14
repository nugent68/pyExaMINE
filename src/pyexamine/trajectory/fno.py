"""Fourier Neural Operator trajectory surrogate.

The hybrid + DeepONet models we've been training are *evaluation-style*
architectures: branch network encodes the scenario, trunk network embeds
the timestep, output at step t is a function of (branch_out, trunk(t)).
Every output step is predicted independently from the scenario inputs.
This is fundamentally a parameter-to-pointwise-function map.

The 10x architecture sweep showed all 8 hybrid variants converge to the
same val_mse, and a v2 (richer features) experiment moved val_mse by
0.0008 (noise).  Both gave strong evidence the bottleneck is not data
and not features but the *model class* -- specifically, the inability
to propagate information across time during the forward pass.

FNO (Li et al. 2020, "Fourier Neural Operator for Parametric Partial
Differential Equations") attacks this directly.  Each Fourier block
takes a function-on-grid, FFTs it to spectral coefficients, multiplies
by learnable complex weights on the *lowest* ``modes`` Fourier modes,
inverse-FFTs back, and adds a pointwise residual.  Because FFT mixes
information globally across time, every output step is influenced by
every input step.  The model can learn temporal couplings the DeepONet
fundamentally cannot.

For our problem (scenario -> 1352-step price trajectory), the lift is:
broadcast scenario features to all 1352 steps, concatenate a normalized
time coordinate, project to ``width`` channels, run ``num_layers`` FNO
blocks with GELU, project back to a scalar per timestep.

Subsample considerations: FNO is a function-on-uniform-grid operator,
so unlike DeepONet/hybrid we cannot easily train on random per-record
subsamples.  Training requires the full 1352-step grid.  Memory budget
is the constraint: at width=32 a (batch=32, 1352, 32) tensor is ~5 MB
per layer of activations, comfortable on an A100.
"""

from __future__ import annotations

import math
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


class SpectralConv1d(nn.Module):
    """1D spectral convolution: FFT -> mode mixing -> IFFT.

    Multiplies the lowest ``modes`` Fourier coefficients by a learnable
    complex weight tensor.  Higher modes are zeroed out, which acts as
    a learned low-pass + spectral filter.

    Weights are stored as a real ``(in_channels, out_channels, modes, 2)``
    tensor and viewed as complex in the forward pass.  This is the
    historical pattern; works with torch's autograd without needing
    ``torch.cfloat`` parameters (which have intermittent support across
    versions).
    """

    def __init__(self, in_channels: int, out_channels: int, modes: int) -> None:
        super().__init__()
        self.in_channels = int(in_channels)
        self.out_channels = int(out_channels)
        self.modes = int(modes)
        # Scaled Glorot-style init for complex weights.
        scale = 1.0 / math.sqrt(in_channels * out_channels)
        self.weight = nn.Parameter(
            scale * torch.randn(in_channels, out_channels, modes, 2)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """``x`` shape: ``(batch, in_channels, length)``."""
        B, C, L = x.shape
        x_ft = torch.fft.rfft(x, dim=-1)             # (B, C, L//2 + 1) complex
        modes = min(self.modes, x_ft.size(-1))

        w = torch.view_as_complex(self.weight)        # (in, out, modes)
        # einsum: mix in_channels into out_channels at each retained mode.
        out_ft = torch.zeros(
            B, self.out_channels, x_ft.size(-1),
            dtype=x_ft.dtype, device=x.device,
        )
        out_ft[..., :modes] = torch.einsum(
            "bcm,com->bom", x_ft[..., :modes], w[..., :modes],
        )
        return torch.fft.irfft(out_ft, n=L, dim=-1)


class FNOBlock1d(nn.Module):
    """Single FNO block: spectral conv + pointwise residual + activation."""

    def __init__(self, width: int, modes: int) -> None:
        super().__init__()
        self.spec = SpectralConv1d(width, width, modes)
        self.local = nn.Conv1d(width, width, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return F.gelu(self.spec(x) + self.local(x))


# ---------------------------------------------------------------------------
# Full model
# ---------------------------------------------------------------------------


class FNOTrajectoryModel(nn.Module):
    """Scenario-features -> 1352-step price trajectory.

    Lift the (batch, feature_dim) scenario tensor to a function on
    [0, 1] sampled at ``n_steps`` points by broadcasting and
    concatenating a normalized time coordinate.  Project to ``width``
    channels, run ``num_layers`` FNO blocks, project to a scalar per
    timestep.

    The signature is ``forward(x, t_norm)`` to match the DeepONet
    interface used by the train loop, but ``t_norm`` is only consulted
    for its second-dimension length (the train script enforces
    ``subsample=0`` -> ``len(t_norm) == n_steps`` for FNO bundles).
    """

    def __init__(
        self,
        feature_dim: int,
        width: int = 32,
        modes: int = 16,
        num_layers: int = 4,
        n_steps: int = 1352,
        proj_hidden: int = 128,
    ) -> None:
        super().__init__()
        self.feature_dim = int(feature_dim)
        self.width = int(width)
        self.modes = int(modes)
        self.num_layers = int(num_layers)
        self.n_steps = int(n_steps)
        self.proj_hidden = int(proj_hidden)

        # Lift (feature_dim + 1 time coord) -> width channels.
        self.lift = nn.Linear(self.feature_dim + 1, self.width)

        self.blocks = nn.ModuleList([
            FNOBlock1d(self.width, self.modes) for _ in range(self.num_layers)
        ])

        # Pointwise MLP projection back to a scalar.
        self.proj1 = nn.Conv1d(self.width, self.proj_hidden, kernel_size=1)
        self.proj2 = nn.Conv1d(self.proj_hidden, 1, kernel_size=1)

        # Time grid in [0, 1].  Registered as buffer so it moves with .to().
        time_grid = torch.linspace(0.0, 1.0, self.n_steps).unsqueeze(-1)
        self.register_buffer("time_grid", time_grid)   # (n_steps, 1)

    def forward(
        self,
        x: torch.Tensor,
        t_norm: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """``x`` shape: ``(batch, feature_dim)``.

        ``t_norm`` is accepted for interface compatibility with DeepONet
        but only the second-dim length matters.  Returns
        ``(batch, n_out)`` where ``n_out`` defaults to ``n_steps``.
        """
        B = x.size(0)
        n_out = self.n_steps if t_norm is None else t_norm.size(-1)
        if n_out != self.n_steps:
            raise ValueError(
                f"FNO forward got t_norm with {n_out} steps but model "
                f"was built for {self.n_steps}.  FNO trains on the full "
                f"uniform grid; use --subsample 0 for FNO bundles."
            )

        # Broadcast features to (B, T, F) and concat time coord.
        feat_t = x.unsqueeze(1).expand(B, self.n_steps, -1)         # (B, T, F)
        time_t = self.time_grid.unsqueeze(0).expand(B, -1, -1)      # (B, T, 1)
        h = torch.cat([feat_t, time_t], dim=-1)                     # (B, T, F+1)

        # Lift channels, permute to (B, width, T) for Conv1d / FFT.
        h = self.lift(h).permute(0, 2, 1)                           # (B, W, T)

        for block in self.blocks:
            h = block(h)                                            # (B, W, T)

        h = F.gelu(self.proj1(h))                                   # (B, P, T)
        h = self.proj2(h)                                           # (B, 1, T)
        return h.squeeze(1)                                         # (B, T)


# ---------------------------------------------------------------------------
# Variant registry
# ---------------------------------------------------------------------------


def default_fno_config(mineral: str, feature_dim: int) -> dict:
    """Canonical FNO configuration.

    width / modes / num_layers chosen to land at ~100-200k params (in
    line with the hybrid bundles).  ``modes=16`` keeps the lowest 16
    Fourier coefficients out of ``1352 // 2 + 1 = 677``; that's about
    2.4% of the spectrum.  Higher modes were not helpful in early
    smoke tests and quickly inflate parameter count.
    """
    return {
        "feature_dim": int(feature_dim),
        "width": 32,
        "modes": 16,
        "num_layers": 4,
        "n_steps": 1352,
        "proj_hidden": 128,
    }


#: Named hyperparam variants for sweep-time A/B testing.  Mirrors the
#: ``HYBRID_VARIANTS`` pattern in :mod:`trajectory.hybrid`.
FNO_VARIANTS: dict[str, dict] = {
    "default": {},
    "wide_64":      {"width": 64},
    "wide_96":      {"width": 96},
    "modes_32":     {"modes": 32},
    "modes_64":     {"modes": 64},
    "deep_6":       {"num_layers": 6},
    "deep_8":       {"num_layers": 8},
    "wide_modes":   {"width": 64, "modes": 32},
}


def fno_config_for_variant(
    mineral: str, feature_dim: int, variant: str,
) -> dict:
    if variant not in FNO_VARIANTS:
        raise ValueError(
            f"unknown FNO variant '{variant}' "
            f"(known: {sorted(FNO_VARIANTS)})"
        )
    cfg = default_fno_config(mineral, feature_dim=feature_dim)
    cfg.update(FNO_VARIANTS[variant])
    return cfg
