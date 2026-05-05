#!/usr/bin/env python3
"""Predict scalar targets for one (or several) scenarios via the surrogate.

Reads scenario JSON from a file or stdin, prints prediction dicts.

Examples:

    # Single scenario from a file:
    uv run python scripts/predict_surrogate.py \
        --models surrogate_models/ \
        --scenario chile_li.json

    # Inline JSON (single scenario):
    echo '{"mineral":"lithium",
           "embargoes":[{"country":"Chile","start_step":676,"duration":52}]}' \
      | uv run python scripts/predict_surrogate.py --models surrogate_models/

    # JSON list of scenarios (batch):
    uv run python scripts/predict_surrogate.py \
        --models surrogate_models/ --scenario scenarios/lithium.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.surrogate import predict as pred         # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--models", type=Path, required=True,
                   help="Directory holding <mineral>_scalar.pkl bundles.")
    p.add_argument("--scenario", type=Path,
                   help="Path to a JSON file. If omitted, JSON is read from stdin.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    models = pred.load_models(args.models)
    if not models:
        print(f"No trained models found under {args.models}", file=sys.stderr)
        return 1

    if args.scenario:
        with args.scenario.open() as f:
            payload = json.load(f)
    else:
        payload = json.load(sys.stdin)

    if isinstance(payload, list):
        results = pred.predict_batch(payload, models)
        for i, res in enumerate(results):
            print(json.dumps({"scenario_index": i, **res}, indent=1))
    else:
        print(json.dumps(pred.predict(payload, models), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
