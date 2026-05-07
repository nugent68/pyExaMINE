"""Hybrid trajectory surrogate: smooth baseline + event-impulse decomposition.

Motivation: the v2 eval (deeponet_phase) showed targeted but incomplete
gains over v1 -- peak_price R^2 moved from 0.37 -> 0.51 on lithium, and
recovery_time on nickel recovered from -0.68 to +0.29.  The remaining
gap to the GBT scalar surrogate (peak R^2 ~ 0.95 across all minerals)
is the part of the response that's structurally event-driven and
poorly served by *any* DeepONet variant whose basis is a smooth
function of t.

This module decomposes the prediction into two components with
matched inductive biases:

    predicted(t; scenario) = baseline(t; knobs)               # smooth
                           + sum_{e in events} impulse(t - t_e; e)  # localised

* :class:`HybridTrajectoryModel` -- the torch module.
* :func:`encode_baseline_knobs(scenario)` -- 6-dim continuous-knob
  vector for the baseline branch.
* :func:`encode_events(scenario, mineral, ...)` -- per-event integer ids
  + (start, duration, active) tensors for the impulse path.

Per-mineral instantiation (matching v1/v2): each mineral gets its own
event-vocab (number of producing countries varies) so the embedding
table size differs.  See :func:`event_vocab_size`.
"""

from __future__ import annotations

import math
from typing import Sequence

import torch
import torch.nn as nn

from src.surrogate import features as ft
from .deeponet import (
    DeepONet,
    TimeEmbedding,
    _make_mlp,
    MAX_EVENT_SLOTS,
)


#: Maximum number of simultaneous events the hybrid model represents
#: (5 embargoes + 3 chokepoints = 8, matching :data:`features.K_MAX_EMBARGOES`
#: and :data:`features.K_MAX_CHOKEPOINTS`).  Imported from deeponet for
#: a single source of truth.
MAX_EVENTS: int = MAX_EVENT_SLOTS


def event_vocab_size(mineral: str) -> int:
    """Per-mineral event-identity vocab.

    Layout: 0 = NONE/inactive, then one slot per producing country,
    then one slot per chokepoint.
    """
    return 1 + len(ft.COUNTRIES_BY_MINERAL[mineral]) + len(ft.CHOKEPOINTS)


# ---------------------------------------------------------------------------
# Scenario -> tensor helpers
# ---------------------------------------------------------------------------

def encode_baseline_knobs(scenario: dict) -> torch.Tensor:
    """Just the 6 continuous-knob values (config_overrides), padded to defaults.

    The baseline branch sees no event information; events go entirely
    through the impulse path.  This forces the smooth/sharp split that
    motivates the architecture.
    """
    cfg = scenario.get("config_overrides", {}) or {}
    parts = [
        float(cfg.get(k, ft.DEFAULT_CONFIG_KNOBS.get(k, 0.0)))
        for k, _, _ in ft.CONFIG_KNOBS
    ]
    return torch.tensor(parts, dtype=torch.float32)


def baseline_feat_dim() -> int:
    """Number of continuous-knob features the baseline branch ingests."""
    return len(ft.CONFIG_KNOBS)


def encode_events(
    scenario: dict, mineral: str, n_steps: int = ft.DEFAULT_N_STEPS,
    max_events: int = MAX_EVENTS,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """Pull the scenario's events into per-slot tensors.

    Returns ``(ids, starts_norm, log_durations, active)``, each shape
    ``(max_events,)``.  ``ids[i]`` indexes into a per-mineral vocab:
        0                         -> inactive / unknown
        1..len(countries)         -> embargo on country i
        1+C..1+C+len(chokepoints) -> chokepoint crisis at chokepoint i
    Inactive slots have ids[i]=0 and active[i]=False; downstream code
    multiplies the impulse contribution by active to mask them out.
    """
    countries = ft.COUNTRIES_BY_MINERAL[mineral]
    chokepoints = ft.CHOKEPOINTS
    horizon = max(1, int(n_steps) - 1)

    ids: list[int] = []
    starts: list[float] = []
    durations: list[float] = []
    for e in (scenario.get("embargoes") or []):
        country = e.get("country")
        if country in countries:
            id_ = 1 + countries.index(country)
        else:
            id_ = 0  # unknown -> NONE sentinel
        ids.append(id_)
        starts.append(float(e["start_step"]) / horizon)
        durations.append(math.log1p(max(0.0, float(e["duration"]))))
    for c in (scenario.get("chokepoint_crises") or []):
        cp = c.get("chokepoint")
        if cp in chokepoints:
            id_ = 1 + len(countries) + chokepoints.index(cp)
        else:
            id_ = 0
        ids.append(id_)
        starts.append(float(c["start_step"]) / horizon)
        durations.append(math.log1p(max(0.0, float(c["duration"]))))

    n = min(len(ids), max_events)
    out_ids = torch.zeros(max_events, dtype=torch.long)
    out_starts = torch.zeros(max_events, dtype=torch.float32)
    out_durations = torch.zeros(max_events, dtype=torch.float32)
    out_active = torch.zeros(max_events, dtype=torch.bool)
    if n > 0:
        out_ids[:n] = torch.tensor(ids[:n], dtype=torch.long)
        out_starts[:n] = torch.tensor(starts[:n], dtype=torch.float32)
        out_durations[:n] = torch.tensor(durations[:n], dtype=torch.float32)
        out_active[:n] = True
    return out_ids, out_starts, out_durations, out_active


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class HybridTrajectoryModel(nn.Module):
    """Smooth-baseline + per-event impulse-response decomposition.

    Two parameter-sharing decisions worth flagging:

    * The **baseline** is a small DeepONet over (continuous knobs, t).
      It carries no event information, so it can only produce smooth
      trajectories -- a good inductive bias for the "no shock"
      part of the response.

    * The **impulse network** is a single shared MLP applied to every
      event slot, fed (event_embedding, delta_t, log_duration) and
      returning a scalar perturbation.  Per-event impulses are summed
      (after masking inactive slots) and added to the baseline.  This
      shares parameters across event types, which is sample-efficient:
      training on one Chile-Lithium embargo informs the model's
      response shape for *any* country embargo.  The event embedding
      gives it room to learn type-specific differences.
    """

    def __init__(
        self,
        mineral: str,
        baseline_feat_dim_: int = 6,
        baseline_basis_dim: int = 64,
        baseline_branch_hidden: list[int] | None = None,
        baseline_trunk_hidden: list[int] | None = None,
        baseline_time_embed_dim: int = 64,
        event_embed_dim: int = 16,
        event_time_embed_dim: int = 32,
        impulse_hidden: list[int] | None = None,
    ) -> None:
        super().__init__()
        self.mineral = mineral
        self.vocab_size = event_vocab_size(mineral)

        # ----- baseline path -----
        self.baseline = DeepONet(
            feature_dim=baseline_feat_dim_,
            basis_dim=baseline_basis_dim,
            branch_hidden=baseline_branch_hidden or [128, 128],
            trunk_hidden=baseline_trunk_hidden or [128, 128],
            time_embed_dim=baseline_time_embed_dim,
        )

        # ----- impulse path -----
        # Embed event identity (country/chokepoint) into a small vector.
        self.event_embed = nn.Embedding(self.vocab_size, event_embed_dim)
        # Sinusoidal embedding for delta_t = t - event_start (signed,
        # in roughly [-1, 1] -- we still get useful frequencies).
        self.dt_embed = TimeEmbedding(event_time_embed_dim)
        # Impulse MLP input: event_embed + dt_embed + log_duration scalar.
        impulse_in = event_embed_dim + event_time_embed_dim + 1
        self.impulse_mlp = _make_mlp(
            impulse_in, 1, impulse_hidden or [128, 128],
        )

    def forward(
        self,
        baseline_feats: torch.Tensor,    # (B, F_b)
        t_norm: torch.Tensor,            # (B,)
        event_ids: torch.Tensor,         # (B, MAX_EVENTS) long
        event_starts: torch.Tensor,      # (B, MAX_EVENTS) float
        event_log_durations: torch.Tensor,   # (B, MAX_EVENTS) float
        event_active: torch.Tensor,      # (B, MAX_EVENTS) bool
    ) -> torch.Tensor:
        """Predict y(scenario, t) as baseline + sum-of-impulses."""
        B = baseline_feats.shape[0]
        baseline_out = self.baseline(baseline_feats, t_norm)   # (B,)

        # Per-event impulse contribution.  We compute (B, MAX_EVENTS)
        # delta_t and log_dur tensors, embed event ids, time-embed dt,
        # concat, and feed through the shared impulse MLP.
        embed = self.event_embed(event_ids)                  # (B, ME, D_e)
        # Broadcast t_norm over event slots, subtract per-slot start.
        dt = t_norm.unsqueeze(-1) - event_starts             # (B, ME)
        # Clamp dt to a sensible range so the time embedding doesn't
        # extrapolate wildly when scenario starts are at horizon edge.
        dt_clamped = torch.clamp(dt, min=-1.5, max=1.5)
        # Re-shape for time embedding (which expects a 1-D batch).
        dt_emb = self.dt_embed(dt_clamped.reshape(-1)).reshape(
            B, MAX_EVENTS, -1
        )                                                    # (B, ME, D_t)
        log_dur = event_log_durations.unsqueeze(-1)          # (B, ME, 1)

        impulse_in = torch.cat([embed, dt_emb, log_dur], dim=-1)  # (B, ME, D)
        # Apply MLP slot-wise.  _make_mlp builds a torch.nn.Sequential
        # which works fine on (B*ME, D) -> (B*ME, 1).
        flat = impulse_in.reshape(B * MAX_EVENTS, -1)
        impulse_per = self.impulse_mlp(flat).reshape(B, MAX_EVENTS)
        # Mask inactive slots so they contribute zero.
        impulse_per = impulse_per * event_active.float()
        impulse_sum = impulse_per.sum(dim=-1)                # (B,)
        return baseline_out + impulse_sum


# ---------------------------------------------------------------------------
# Convenience: per-mineral hyperparam set
# ---------------------------------------------------------------------------

def default_hybrid_config(mineral: str) -> dict:
    """Reasonable starting hyperparameters for the hybrid model."""
    return dict(
        mineral=mineral,
        baseline_feat_dim_=baseline_feat_dim(),
        baseline_basis_dim=64,
        baseline_branch_hidden=[128, 128],
        baseline_trunk_hidden=[128, 128],
        baseline_time_embed_dim=64,
        event_embed_dim=16,
        event_time_embed_dim=32,
        impulse_hidden=[128, 128],
    )


#: Named architecture variants for the v3 hybrid sweep.  Each variant
#: returns ``default_hybrid_config(mineral)`` overlaid with the listed
#: deltas, so adding a new variant only needs the diff.  The registry
#: is consumed by ``--hybrid-variant`` in ``scripts/train_trajectory.py``
#: and the ``scripts/perlmutter_sweep_trajectory_cpu.slurm`` fan-out.
HYBRID_VARIANTS: dict[str, dict] = {
    # Baseline (matches default_hybrid_config exactly).
    "default": {},

    # ----- impulse-MLP capacity dial -----
    "impulse_256x2": dict(impulse_hidden=[256, 256]),
    "impulse_512x2": dict(impulse_hidden=[512, 512]),
    "impulse_256x3": dict(impulse_hidden=[256, 256, 256]),

    # ----- event-embedding capacity -----
    "embed_32":      dict(event_embed_dim=32),
    "embed_64":      dict(event_embed_dim=64),

    # ----- baseline DeepONet capacity -----
    "baseline_basis_128": dict(baseline_basis_dim=128),

    # ----- everything bigger -----
    "all_larger": dict(
        impulse_hidden=[256, 256],
        event_embed_dim=32,
        baseline_basis_dim=128,
    ),
}


def hybrid_config_for_variant(mineral: str, variant: str) -> dict:
    """Return the resolved hyperparam dict for ``--hybrid-variant``."""
    if variant not in HYBRID_VARIANTS:
        raise ValueError(
            f"Unknown hybrid variant '{variant}'. "
            f"Known: {sorted(HYBRID_VARIANTS)}"
        )
    cfg = default_hybrid_config(mineral)
    cfg.update(HYBRID_VARIANTS[variant])
    return cfg
