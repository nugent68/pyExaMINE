#!/bin/bash
# pyExaMINE -- targeted gap-fill for missing surrogate-training CSVs.
#
# Used after a perlmutter_sample.slurm run (especially debug-QoS gap-
# fills) leaves a small tail of missing CSVs. Reads an explicit list
# of *missing* flat indices instead of iterating 0..N-1 with
# --skip-existing -- so the heavy Shifter-cold-start overhead per
# already-done file is avoided entirely.
#
# Usage (login node):
#   1. Compute the missing-indices file:
#      MINERAL=platinum
#      DATA_ROOT=$CFS/amsc001/www/surrogate_data
#      ls $DATA_ROOT/runs/$MINERAL/ | sed 's/\.csv$//' | sort > /tmp/done.txt
#      N_TOTAL=$(wc -l < $DATA_ROOT/scenarios/${MINERAL}.json)
#      seq -f "%06g" 0 $((N_TOTAL - 1)) | sort > /tmp/all.txt
#      comm -23 /tmp/all.txt /tmp/done.txt | sed 's/^0*//; s/^$/0/' \
#          > $DATA_ROOT/missing_${MINERAL}.txt
#
#   2. Submit:
#      sbatch --export=ALL,MINERAL=platinum scripts/perlmutter_gapfill.slurm
#
# Throughput: pure real runs at ~25-70 s each / 256 parallel slots
# (2 nodes x 128 cores). Typical 5000-missing gap-fill -> ~20 min
# wall, comfortable in the 30-min debug cap.
#
#SBATCH --image=docker:nugent68/pyexamine:2e5a768
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:30:00
#SBATCH --job-name=pyexamine_gapfill
#SBATCH --output=logs/gapfill_%j.log

set -euo pipefail

: "${MINERAL:?must export MINERAL=lithium|nickel|platinum}"
: "${PARALLELISM:=128}"
: "${DATA_ROOT:=${CFS}/amsc001/www/surrogate_data}"
MISSING_FILE="${DATA_ROOT}/missing_${MINERAL}.txt"
mkdir -p logs

if [[ ! -s "$MISSING_FILE" ]]; then
    echo "Missing-indices file $MISSING_FILE is empty or absent. Nothing to do."
    exit 0
fi

N_MISSING=$(wc -l < "$MISSING_FILE")
NODES=${SLURM_JOB_NUM_NODES:-2}

echo "=== pyExaMINE gap-fill (${MINERAL}) ==="
echo "Job:       $SLURM_JOB_ID on $(hostname)"
echo "Missing:   $N_MISSING indices"
echo "Nodes:     $NODES"
echo "Parallel:  $PARALLELISM per node"
echo "Started:   $(date)"
echo

export DATA_ROOT MINERAL PARALLELISM

srun --ntasks="${NODES}" --ntasks-per-node=1 --cpu-bind=none bash <<'NODE_BASH'
    set -euo pipefail
    NODE_ID=${SLURM_PROCID}
    N_NODES=${SLURM_NTASKS}
    MISSING_FILE="${DATA_ROOT}/missing_${MINERAL}.txt"

    run_task() {
        local idx=$1
        shifter --volume="${DATA_ROOT}:/data" \
            bash -c "cd /app && /app/.venv/bin/python scripts/run_one_scenario.py \
                --scenarios /data/scenarios/${MINERAL}.json \
                --index ${idx} \
                --output-root /data/runs"
    }
    export -f run_task

    # Stride-distribute the missing indices: node N takes lines where
    # (line_number - 1) % N_NODES == NODE_ID.
    awk -v node="${NODE_ID}" -v n="${N_NODES}" \
        '(NR-1) % n == node' "$MISSING_FILE" \
      | parallel \
            --jobs "${PARALLELISM}" \
            --joblog "logs/gapfill_${SLURM_JOB_ID}_${MINERAL}_node${NODE_ID}.log" \
            --halt=soon,fail=10 \
            run_task {}
NODE_BASH

echo
echo "Finished: $(date)"
n_now=$(ls "${DATA_ROOT}/runs/${MINERAL}/" | wc -l)
echo "Mineral count now: ${n_now}"
