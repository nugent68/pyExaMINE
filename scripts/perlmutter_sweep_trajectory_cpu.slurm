#!/bin/bash
# pyExaMINE -- v3 hybrid architecture sweep on Perlmutter CPU nodes.
#
# Why CPU and not GPU: GBT-class small models (~80k-200k params) hit
# diminishing returns from a GPU's parallelism past ~10 ms per batch.
# Per single-run wall, one A100 only beats one Milan CPU node by 2-3x.
# But for *parallel-experiment throughput* the math flips -- 8 CPU
# nodes can host 24 independent training runs simultaneously, vs the
# 4-A100 GPU node's 4.  At NERSC's allocation ratio (1 GPU node-hour ~
# 8 CPU node-hours) the budgets match, so the choice is whether you
# want one fast experiment or many parallel ones.  This script is for
# many.
#
# What gets swept:
#   3 minerals (Li, Ni, Pt) x N variants (one per HYBRID_VARIANTS entry
#   in src/trajectory/hybrid.py).  At MAX_PER_NODE=3 concurrent
#   trainings per node (with OMP_NUM_THREADS = 128/3 ~= 42 each),
#   8 nodes hold up to 24 simultaneous runs, finishing in roughly the
#   wall time of one (mineral, variant) pair.
#
# Per-run wall on a single CPU node, hybrid 50 epochs (rough):
#   * ~2-3 min/epoch threaded across 42 cores
#   * 50 epochs * 2.5 min = ~2 hours
# So 1 hr is a debugging cap; the real ask is 2-3 hrs.
#
# Default ask: 8 nodes / 2.5 hr / regular_milan_ss11 / single submission.
#
#SBATCH --image=docker:nugent68/pyexamine:37d8827
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=8
#SBATCH --ntasks=24                # 3 trainings per node x 8 nodes
#SBATCH --ntasks-per-node=3
#SBATCH --cpus-per-task=42         # 128 cores / 3 tasks ~= 42
#SBATCH --time=02:30:00
#SBATCH --job-name=pyexamine_traj_sweep
#SBATCH --output=logs/traj_sweep_%j.log

set -euo pipefail

: "${DATA_ROOT:=/pscratch/sd/n/nugent/pyExaMINE}"
: "${EPOCHS:=50}"
: "${SUBSAMPLE:=100}"
: "${BATCH_SIZE:=1024}"
: "${LR:=1e-3}"
: "${OUT_SUBDIR:=trajectory_sweep}"
: "${MINERALS:=lithium nickel platinum}"

# Variant list -- mirrors the keys of HYBRID_VARIANTS in
# src/trajectory/hybrid.py.  Override at submit time with
# --export=VARIANTS="default impulse_256x2".
: "${VARIANTS:=default impulse_256x2 impulse_512x2 impulse_256x3 embed_32 embed_64 baseline_basis_128 all_larger}"

mkdir -p logs "${DATA_ROOT}/${OUT_SUBDIR}"

# Build the run list: one (mineral, variant) per cell.  Total runs =
# |MINERALS| x |VARIANTS|.
RUN_LIST=()
for V in $VARIANTS; do
    for M in $MINERALS; do
        RUN_LIST+=("${M}:${V}")
    done
done
N_RUNS=${#RUN_LIST[@]}

echo "=== pyExaMINE trajectory v3 architecture sweep ==="
echo "Job:           ${SLURM_JOB_ID:-N/A} on $(hostname)"
echo "DATA_ROOT:     $DATA_ROOT"
echo "Out:           $DATA_ROOT/$OUT_SUBDIR/<mineral>_<variant>/"
echo "Epochs:        $EPOCHS    Subsample: $SUBSAMPLE    Batch: $BATCH_SIZE"
echo "Total runs:    $N_RUNS    (${SLURM_NTASKS} parallel tasks)"
echo "Variants:      $VARIANTS"
echo "Started:       $(date)"
echo

# The whole sweep dispatches as one srun with N_RUNS tasks.  We rely
# on Slurm + srun --multi-prog to launch one shifter per task with
# the right (mineral, variant) pair.  Build a multi-prog file on the
# fly.

MULTI_PROG="logs/traj_sweep_${SLURM_JOB_ID}_multi_prog.txt"
: > "$MULTI_PROG"
for i in "${!RUN_LIST[@]}"; do
    pair="${RUN_LIST[$i]}"
    mineral="${pair%%:*}"
    variant="${pair##*:}"
    out_subdir="${OUT_SUBDIR}/${mineral}_${variant}"
    # Multi-prog format: "<task_id> <executable> <args...>".  We
    # invoke shifter directly; bash -c lets us assemble the python
    # command line cleanly.
    cat >> "$MULTI_PROG" <<EOF
${i} bash -c "shifter --volume=${DATA_ROOT}:/data bash -c 'cd /app && OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK} MKL_NUM_THREADS=${SLURM_CPUS_PER_TASK} /app/.venv/bin/python scripts/train_trajectory.py --sweep-runs /data/surrogate_data/runs --sweep-scenarios /data/surrogate_data/scenarios --arch hybrid --hybrid-variant ${variant} --mineral ${mineral} --out /data/${out_subdir} --epochs ${EPOCHS} --subsample ${SUBSAMPLE} --batch-size ${BATCH_SIZE} --lr ${LR} --device cpu --num-workers 0 --cache-dir /data/trajectory_cache 2>&1 | tee logs/traj_sweep_${SLURM_JOB_ID}_${mineral}_${variant}.log'"
EOF
done

echo "=== multi-prog file: $MULTI_PROG ==="
head -3 "$MULTI_PROG"
echo "(... ${N_RUNS} total entries)"
echo

# Single srun with --multi-prog dispatches all tasks; Slurm assigns
# them across the 8 nodes 3-per-node automatically.
srun --ntasks="${N_RUNS}" --cpus-per-task="${SLURM_CPUS_PER_TASK}" \
     --multi-prog "$MULTI_PROG"

echo
echo "Finished: $(date)"
echo "Outputs (one dir per mineral_variant):"
ls -d "${DATA_ROOT}/${OUT_SUBDIR}/"*/ 2>/dev/null | head
