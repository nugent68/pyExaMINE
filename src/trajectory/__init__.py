"""Trajectory surrogate -- per-step price prediction from scenario parameters.

While ``surrogate.train_scalar`` (Phase-2 / point) and
``surrogate.quantile`` (CQR) predict a handful of *scalar* aggregates per
scenario (mean_price, peak_price, recovered probability, ...), the
trajectory surrogate aims to predict the full 1352-step price series.

Phase 3 / v1: DeepONet (Lu et al. 2021).  Branch network conditions on
the scenario feature vector; trunk network embeds the normalized
timestep; output is the dot product of the two over a learned basis.
This is the canonical operator-learning setup for parameter -> function
maps and the right place to start before reaching for the heavier
hybrid (smooth-baseline + event-impulse) decomposition.
"""

from __future__ import annotations
