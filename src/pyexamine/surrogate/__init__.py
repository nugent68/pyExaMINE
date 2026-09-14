"""ML surrogate for pyExaMINE.

Encodes a scenario dict into a fixed-dimension feature vector, samples
diverse scenarios for training data, extracts scalar / trajectory targets
from a model_data.csv, and trains / serves the surrogate models.

Phase 1 (current): scalar GBT. See features.py + sampling.py for the
data-generation pipeline.
"""

from .features import (
    K_MAX_EMBARGOES,
    K_MAX_CHOKEPOINTS,
    COUNTRIES_BY_MINERAL,
    CHOKEPOINTS,
    CONFIG_KNOBS,
    DEFAULT_CONFIG_KNOBS,
    encode,
    encode_batch,
    feature_dim,
    feature_names,
    support_check,
)

__all__ = [
    "K_MAX_EMBARGOES",
    "K_MAX_CHOKEPOINTS",
    "COUNTRIES_BY_MINERAL",
    "CHOKEPOINTS",
    "CONFIG_KNOBS",
    "DEFAULT_CONFIG_KNOBS",
    "encode",
    "encode_batch",
    "feature_dim",
    "feature_names",
    "support_check",
]
