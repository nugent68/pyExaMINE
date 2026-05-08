#!/bin/bash
# pyExaMINE -- v3 hybrid architecture sweep on Perlmutter CPU nodes.
#
# Why CPU and not GPU: small models (~80k-200k params) hit diminishing
# returns from a GPU's parallelism past ~10 ms per batch.  Per single-
# run wall, one A100 only beats one Milan CPU node by 2-3x.  But for
# *parallel-experiment throughput* the math flips -- 8 CPU nodes can
# host 24 independent training runs simultaneously, vs the 4-A100 GPU
# node's 4.  At NERSC's allocation ratio (1 GPU node-h ~ 8 CPU node-h)
# the budgets match, so the choice is whether you want one fast
# experiment or many parallel ones.  This script is for many.
#
# What gets swept:
#   3 minerals (Li, Ni, Pt) x N variants (one per HYBRID_VARIANTS entry
#   in src/trajectory/hybrid.py) = 24 runs at the default knob set.
#   12 nodes x 2 tasks per node = 24 simultaneous runs.
#
# Why 2 tasks per node and not 3 (and not 1 either):
# A 3-tasks-per-node configuration on AMD Milan (job 52680428)
# saw per-epoch wall of ~175 sec; the same code at 2 tasks per
# node ran at ~45 sec.  Going from 2 -> 3 oversubscribes the OS
# (126 of 128 cores active across 3 procs) and forces
# cross-socket NUMA traffic for at least one task.  2 tasks per
# node lets each task pin to a single 64-core socket cleanly,
# at the cost of needing 12 nodes instead of 8 to host 24 tasks.
#
# Per-run wall on a single CPU node, hybrid 50 epochs:
#   * ~45-60 s/epoch with 64 cores via OMP_NUM_THREADS
#   * 50 epochs * 50s = ~42 min per task
# 1 hr cap leaves ~18 min margin.
#
#SBATCH --image=docker:nugent68/pyexamine:175f086
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=12
#SBATCH --ntasks=24                # 2 trainings per node x 12 nodes
#SBATCH --ntasks-per-node=2
#SBATCH --cpus-per-task=64         # 128 cores / 2 tasks = 64 (one socket each)
#SBATCH --time=01:00:00
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

# Build the run list as a flat array of "mineral:variant" pairs.
# Order matters: srun assigns SLURM_PROCID 0..N-1 to tasks, so the
# wrapper indexes RUN_LIST[SLURM_PROCID] to know what to train.
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

# Wrapper script: each SLURM_PROCID picks its (mineral, variant) and
# runs one shifter -> python.  Avoids srun --multi-prog's brittle
# tokeniser; one bash file, one srun, clean quoting.
WRAPPER="logs/traj_sweep_${SLURM_JOB_ID}_runner.sh"
cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/bash
set -euo pipefail
RUN_LIST=(${RUN_LIST[@]})
pair="\${RUN_LIST[\$SLURM_PROCID]}"
mineral="\${pair%%:*}"
variant="\${pair##*:}"
out_subdir="${OUT_SUBDIR}/\${mineral}_\${variant}"
log="logs/traj_sweep_${SLURM_JOB_ID}_\${mineral}_\${variant}.log"
echo "[\$(date +%T)] task \$SLURM_PROCID on \$(hostname): \${mineral} / \${variant}" | tee "\$log"
shifter --volume="${DATA_ROOT}:/data" \\
    bash -c "cd /app && OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK} MKL_NUM_THREADS=${SLURM_CPUS_PER_TASK} PYTHONUNBUFFERED=1 /app/.venv/bin/python -u scripts/train_trajectory.py \\
        --sweep-runs /data/surrogate_data/runs \\
        --sweep-scenarios /data/surrogate_data/scenarios \\
        --arch hybrid \\
        --hybrid-variant \${variant} \\
        --mineral \${mineral} \\
        --out /data/\${out_subdir} \\
        --epochs ${EPOCHS} \\
        --subsample ${SUBSAMPLE} \\
        --batch-size ${BATCH_SIZE} \\
        --lr ${LR} \\
        --device cpu \\
        --num-workers 0 \\
        --cache-dir /data/trajectory_cache" 2>&1 | tee -a "\$log"
WRAPPER_EOF
chmod +x "$WRAPPER"

echo "=== wrapper written: $WRAPPER ==="
head -8 "$WRAPPER"
echo

# Single srun fans out N_RUNS tasks across the allocation; each task
# runs the wrapper, which inspects SLURM_PROCID to dispatch.
srun --ntasks="${N_RUNS}" --cpus-per-task="${SLURM_CPUS_PER_TASK}" \
     bash "$WRAPPER"

echo
echo "Finished: $(date)"
echo "Outputs (one dir per mineral_variant):"
ls -d "${DATA_ROOT}/${OUT_SUBDIR}/"*/ 2>/dev/null | head
