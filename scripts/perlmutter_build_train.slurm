#!/bin/bash
# pyExaMINE -- end-to-end Phase-2 surrogate build + train.
#
# Runs scripts/build_dataset.py for all three minerals, then
# scripts/train_surrogate.py to fit the per-target mean+std boosters
# from the resulting ensemble parquets. Single CPU process, shared
# QoS -- the build is I/O bound (~48 min for 120k CSVs) and the
# training is compact (~5-10 min total over 3 minerals at K=20).
#
# Usage:
#   sbatch scripts/perlmutter_build_train.slurm
#
# Outputs (all under $CFS/amsc001/www/surrogate_data):
#   datasets/<mineral>.parquet    -- ensemble-aggregated rows
#   datasets/index.json
#   surrogate_models/<mineral>_scalar.pkl
#   surrogate_models/<mineral>_scalar.metrics.json
#
#SBATCH --image=docker:nugent68/pyexamine:f6bdbdf
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=shared
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=02:00:00
#SBATCH --job-name=pyexamine_build_train
#SBATCH --output=logs/build_train_%j.log

set -euo pipefail

DATA_ROOT="${CFS}/amsc001/www/surrogate_data"
mkdir -p logs

echo "=== pyExaMINE build + train ==="
echo "Job:     $SLURM_JOB_ID on $(hostname)"
echo "Started: $(date)"
echo

echo "--- build_dataset.py (all minerals) ---"
time shifter --volume="${DATA_ROOT}:/data" \
    bash -c "cd /app && /app/.venv/bin/python scripts/build_dataset.py \
        --runs /data/runs \
        --scenarios /data/scenarios \
        --out /data/datasets"

echo
echo "--- train_surrogate.py (all minerals) ---"
time shifter --volume="${DATA_ROOT}:/data" \
    bash -c "cd /app && /app/.venv/bin/python scripts/train_surrogate.py \
        --datasets /data/datasets \
        --out /data/surrogate_models"

echo
echo "=== outputs ==="
ls -la "${DATA_ROOT}/datasets/"
echo
ls -la "${DATA_ROOT}/surrogate_models/"
echo
echo "Finished: $(date)"
