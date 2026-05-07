#!/bin/bash
# pyExaMINE -- train DeepONet trajectory surrogates on a single Perlmutter
# 4-GPU node, all three minerals in parallel (one A100 each).
#
# Variant of perlmutter_train_trajectory.slurm sized to use the GPU
# node efficiently:
#   - 1 node x 4 A100s; we drive 3 of them (one per mineral) and leave
#     the fourth idle as a fallback pool.
#   - --ntasks=3 + --gpus-per-task=1 + --cpus-per-task=21 + srun --exact
#     fan-out: each task sees exactly one GPU and ~21 of the 64 cores.
#   - Single Shifter cold-start, single queue wait, ~30 min wall total.
#
# Why not 4 minerals: we only have three. Why not 1 GPU * 3 jobs: see
# perlmutter_train_trajectory.slurm; that pays Shifter cold-start and
# queue wait three times, and debug QoS caps at 2 concurrent so the
# third would serialize anyway.
#
# Default ask: 30 min debug.  Override at submit time:
#   sbatch --time=01:30:00 --qos=regular \
#          --export=ALL,MODE=sweep,EPOCHS=50 \
#          scripts/perlmutter_train_trajectory_packed.slurm
#
# Knobs:
#   DATA_ROOT     /pscratch/sd/n/nugent/pyExaMINE  (override via --export)
#   MODE          sweep | canonical
#   EPOCHS        25 (default; bump if regular QoS / longer time)
#   SUBSAMPLE     100 timesteps per CSV per epoch
#   BATCH_SIZE    1024
#   OUT_SUBDIR    trajectory_models   under DATA_ROOT
#
#SBATCH --image=docker:nugent68/pyexamine:4ceaea4
#SBATCH --account=amsc001
#SBATCH --constraint=gpu
#SBATCH --qos=debug
#SBATCH --nodes=1
#SBATCH --ntasks=3
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-task=21
#SBATCH --time=00:30:00
#SBATCH --job-name=pyexamine_traj_packed
#SBATCH --output=logs/traj_packed_%j.log

set -euo pipefail

: "${DATA_ROOT:=/pscratch/sd/n/nugent/pyExaMINE}"
: "${MODE:=sweep}"                # sweep | canonical
: "${EPOCHS:=25}"
: "${SUBSAMPLE:=100}"
: "${BATCH_SIZE:=1024}"
: "${LR:=1e-3}"
: "${OUT_SUBDIR:=trajectory_models}"
: "${MINERALS:=lithium nickel platinum}"

mkdir -p logs "${DATA_ROOT}/${OUT_SUBDIR}"

if [[ "$MODE" == "canonical" ]]; then
    DATA_FLAGS="--canonical-root /data/ensemble_runs/2050"
elif [[ "$MODE" == "sweep" ]]; then
    DATA_FLAGS="--sweep-runs /data/surrogate_data/runs --sweep-scenarios /data/surrogate_data/scenarios"
else
    echo "Unknown MODE=$MODE (expected canonical|sweep)"
    exit 1
fi

echo "=== pyExaMINE trajectory training (packed, 3 minerals @ 1 A100 each) ==="
echo "Job:           ${SLURM_JOB_ID:-N/A} on $(hostname)"
echo "DATA_ROOT:     $DATA_ROOT"
echo "Mode:          $MODE"
echo "Epochs:        $EPOCHS"
echo "Subsample:     $SUBSAMPLE"
echo "Out:           $DATA_ROOT/$OUT_SUBDIR"
echo "Started:       $(date)"
echo

# Fan out: one srun task per mineral, each pinned to its own A100.
# --exact ensures each srun consumes exactly its share of the parent
# allocation rather than trying to grab the whole node.
for MINERAL in $MINERALS; do
    echo "[$(date +%T)] launching srun for $MINERAL"
    srun --exact --ntasks=1 --gpus=1 --cpus-per-task=21 \
         --output="logs/traj_packed_${SLURM_JOB_ID}_${MINERAL}.log" \
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
             --cache-dir /data/trajectory_cache" &
done

echo "[$(date +%T)] all 3 srun tasks launched; waiting"
wait
echo "[$(date +%T)] all srun tasks completed"

echo
echo "=== outputs ==="
ls -la "${DATA_ROOT}/${OUT_SUBDIR}/" 2>/dev/null

echo
echo "Finished: $(date)"
