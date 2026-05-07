#!/bin/bash
# pyExaMINE -- train DeepONet trajectory surrogate on a Perlmutter GPU node.
#
# Default ask: single A100 (one out of four on the node), debug QoS,
# 30 min cap. Override with sbatch --time / --qos / --gpus for the
# full-data run when the 120k sweep lands (~1 hr per mineral on one
# A100, so 3 hr or three jobs in series cleans the lot).
#
# What this script expects on disk:
#   $DATA_ROOT/<canonical-root>          per-step CSVs in either layout
#                                        (canonical ensemble or sweep tree)
#   $DATA_ROOT/<scenarios-root>          for sweep mode only; matching
#                                        scenarios JSON tree
#   $DATA_ROOT/trajectory_models/        outputs land here
#
# Default DATA_ROOT is /pscratch/sd/n/nugent/pyExaMINE -- override at
# submit time:
#   sbatch --export=DATA_ROOT=$CFS/amsc001/www/surrogate_data \
#          scripts/perlmutter_train_trajectory.slurm
#
#SBATCH --image=docker:nugent68/pyexamine:5df34dc
#SBATCH --account=amsc001
#SBATCH --constraint=gpu
#SBATCH --qos=debug
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus=1
#SBATCH --cpus-per-task=32
#SBATCH --time=00:30:00
#SBATCH --job-name=pyexamine_traj
#SBATCH --output=logs/traj_%j.log

set -euo pipefail

# Knobs (override at submit time via sbatch --export=).
: "${DATA_ROOT:=/pscratch/sd/n/nugent/pyExaMINE}"
: "${MODE:=canonical}"               # canonical | sweep
: "${MINERAL:=lithium}"              # for the smoke test; override for full
: "${EPOCHS:=20}"
: "${SUBSAMPLE:=100}"
: "${BATCH_SIZE:=1024}"
: "${LR:=1e-3}"
: "${OUT_SUBDIR:=trajectory_models}"

mkdir -p logs "${DATA_ROOT}/${OUT_SUBDIR}"

echo "=== pyExaMINE trajectory training ==="
echo "Job:           ${SLURM_JOB_ID:-N/A} on $(hostname)"
echo "DATA_ROOT:     $DATA_ROOT"
echo "Mode:          $MODE"
echo "Mineral:       $MINERAL"
echo "Epochs:        $EPOCHS"
echo "Subsample:     $SUBSAMPLE"
echo "Out:           $DATA_ROOT/$OUT_SUBDIR"
echo "Started:       $(date)"
echo

# Build the data-source flags depending on the corpus layout.
if [[ "$MODE" == "canonical" ]]; then
    DATA_FLAGS="--canonical-root /data/ensemble_runs/2050"
elif [[ "$MODE" == "sweep" ]]; then
    DATA_FLAGS="--sweep-runs /data/surrogate_data/runs --sweep-scenarios /data/surrogate_data/scenarios"
else
    echo "Unknown MODE=$MODE (expected canonical|sweep)"
    exit 1
fi

# Bind DATA_ROOT into the container as /data so paths inside match
# what train_trajectory.py expects.  We use the venv's python directly
# (avoiding GPFS lock contention with concurrent uv invocations) and
# pass --device cuda so torch initialises the A100.
shifter --module=gpu --volume="${DATA_ROOT}:/data" \
    bash -c "cd /app && /app/.venv/bin/python scripts/train_trajectory.py \
        ${DATA_FLAGS} \
        --out /data/${OUT_SUBDIR} \
        --mineral ${MINERAL} \
        --epochs ${EPOCHS} \
        --subsample ${SUBSAMPLE} \
        --batch-size ${BATCH_SIZE} \
        --lr ${LR} \
        --device cuda \
        --num-workers 4 \
        --persistent-workers \
        --cache-dir /data/trajectory_cache"

echo
echo "Finished: $(date)"
echo "Output:   ${DATA_ROOT}/${OUT_SUBDIR}/"
