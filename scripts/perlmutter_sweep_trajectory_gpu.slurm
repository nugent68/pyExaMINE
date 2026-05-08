#!/bin/bash
# pyExaMINE -- v3 hybrid architecture sweep on Perlmutter GPU nodes.
#
# GPU companion to perlmutter_sweep_trajectory_cpu.slurm.  Same 24-run
# matrix (3 minerals x 8 hyperparam variants) but each task gets its
# own A100 instead of one CPU socket.
#
# Why GPU instead of CPU here:
# Per-epoch wall on the full 32k-record corpus is ~165 s on a 64-core
# Milan socket (job 52696084 confirmed) versus ~44 s on one A100.
# 50 epochs = 37 min on A100 vs 138 min on CPU.  At our model size
# (~80-200k params) the GPU wins on wall time and total compute even
# at NERSC's 3-4x charge ratio for GPU node-hours.
#
# Layout: 6 GPU nodes (4 A100s each = 24 GPUs) host all 24 tasks
# concurrently in a single batch.  Per-task: 1 GPU + 32 cores via
# --gpus-per-task=1 and --cpus-per-task=32.  Each task pins to one
# A100 via Slurm's CUDA_VISIBLE_DEVICES handling.
#
# Per-task wall: ~40 min (37 min training + 3 min Shifter cold-start).
# 1 hr cap leaves ~20 min margin.
#
# Submit:
#   sbatch scripts/perlmutter_sweep_trajectory_gpu.slurm
#
# Override variant set or compute knobs at submit time:
#   sbatch --export=ALL,EPOCHS=25,VARIANTS="default impulse_256x2" \
#          scripts/perlmutter_sweep_trajectory_gpu.slurm
#
#SBATCH --image=docker:nugent68/pyexamine:175f086
#SBATCH --account=amsc001
#SBATCH --constraint=gpu
#SBATCH --qos=regular
#SBATCH --nodes=6
#SBATCH --ntasks=24                # 4 GPUs per node x 6 nodes
#SBATCH --ntasks-per-node=4
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-task=32         # 32 of 128 cores per task; spare for OS
#SBATCH --time=01:00:00
#SBATCH --job-name=pyexamine_traj_sweep_gpu
#SBATCH --output=logs/traj_sweep_gpu_%j.log

set -euo pipefail

: "${DATA_ROOT:=/pscratch/sd/n/nugent/pyExaMINE}"
: "${EPOCHS:=50}"
: "${SUBSAMPLE:=100}"
: "${BATCH_SIZE:=1024}"
: "${LR:=1e-3}"
: "${OUT_SUBDIR:=trajectory_sweep_gpu}"
: "${MINERALS:=lithium nickel platinum}"

# Variant list -- mirrors the keys of HYBRID_VARIANTS in
# src/trajectory/hybrid.py.
: "${VARIANTS:=default impulse_256x2 impulse_512x2 impulse_256x3 embed_32 embed_64 baseline_basis_128 all_larger}"

mkdir -p logs "${DATA_ROOT}/${OUT_SUBDIR}"

# Build the run list as a flat array of "mineral:variant" pairs.
RUN_LIST=()
for V in $VARIANTS; do
    for M in $MINERALS; do
        RUN_LIST+=("${M}:${V}")
    done
done
N_RUNS=${#RUN_LIST[@]}

echo "=== pyExaMINE trajectory v3 architecture sweep (GPU) ==="
echo "Job:           ${SLURM_JOB_ID:-N/A} on $(hostname)"
echo "DATA_ROOT:     $DATA_ROOT"
echo "Out:           $DATA_ROOT/$OUT_SUBDIR/<mineral>_<variant>/"
echo "Epochs:        $EPOCHS    Subsample: $SUBSAMPLE    Batch: $BATCH_SIZE"
echo "Total runs:    $N_RUNS    (${SLURM_NTASKS} parallel tasks on ${SLURM_JOB_NUM_NODES} GPU nodes)"
echo "Variants:      $VARIANTS"
echo "Started:       $(date)"
echo

# Wrapper script: each SLURM_PROCID picks its (mineral, variant) and
# runs one shifter + python on its assigned A100.
WRAPPER="logs/traj_sweep_gpu_${SLURM_JOB_ID}_runner.sh"
cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/bash
set -euo pipefail
RUN_LIST=(${RUN_LIST[@]})
pair="\${RUN_LIST[\$SLURM_PROCID]}"
mineral="\${pair%%:*}"
variant="\${pair##*:}"
out_subdir="${OUT_SUBDIR}/\${mineral}_\${variant}"
log="logs/traj_sweep_gpu_${SLURM_JOB_ID}_\${mineral}_\${variant}.log"
echo "[\$(date +%T)] task \$SLURM_PROCID on \$(hostname) (CUDA_VISIBLE_DEVICES=\$CUDA_VISIBLE_DEVICES): \${mineral} / \${variant}" | tee "\$log"
shifter --module=gpu --volume="${DATA_ROOT}:/data" \\
    bash -c "cd /app && PYTHONUNBUFFERED=1 /app/.venv/bin/python -u scripts/train_trajectory.py \\
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
        --device cuda \\
        --num-workers 4 \\
        --persistent-workers \\
        --cache-dir /data/trajectory_cache" 2>&1 | tee -a "\$log"
WRAPPER_EOF
chmod +x "$WRAPPER"

echo "=== wrapper written: $WRAPPER ==="
head -8 "$WRAPPER"
echo

# Single srun fans out N_RUNS tasks across the GPU allocation.  Each
# task gets exactly one GPU via Slurm's --gpus-per-task accounting.
srun --ntasks="${N_RUNS}" --gpus-per-task=1 --cpus-per-task="${SLURM_CPUS_PER_TASK}" \
     bash "$WRAPPER"

echo
echo "Finished: $(date)"
echo "Outputs (one dir per mineral_variant):"
ls -d "${DATA_ROOT}/${OUT_SUBDIR}/"*/ 2>/dev/null | head
