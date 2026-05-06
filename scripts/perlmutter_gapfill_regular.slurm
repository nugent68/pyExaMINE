#!/bin/bash
# pyExaMINE -- targeted gap-fill on the regular QoS for the slow-tail
# residual.
#
# Mirror of perlmutter_gapfill.slurm but on regular QoS with a longer
# wall-time cap. Use this when the missing indices are concentrated in
# the high-event-multiplicity stratum -- those scenarios run ~6-8x
# slower than the median and need real budget.
#
# Usage: same login-node prep as perlmutter_gapfill.slurm, then:
#   sbatch --export=ALL,MINERAL=platinum scripts/perlmutter_gapfill_regular.slurm
#
# Throughput sizing: at ~470 s/task on the slowest stratum and 256
# parallel slots (2 nodes x 128), 2000 missing -> ~62 min wall. The
# 2 hr cap leaves headroom for tail variance.
#
#SBATCH --image=docker:nugent68/pyexamine:ed6895c
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=02:00:00
#SBATCH --job-name=pyexamine_gapfill_reg
#SBATCH --output=logs/gapfill_%j.log

set -euo pipefail

: "${MINERAL:?must export MINERAL=lithium|nickel|platinum}"
: "${PARALLELISM:=128}"

DATA_ROOT="${CFS}/amsc001/www/surrogate_data"
MISSING_FILE="${DATA_ROOT}/missing_${MINERAL}.txt"
mkdir -p logs

if [[ ! -s "$MISSING_FILE" ]]; then
    echo "Missing-indices file $MISSING_FILE is empty or absent. Nothing to do."
    exit 0
fi

N_MISSING=$(wc -l < "$MISSING_FILE")
NODES=${SLURM_JOB_NUM_NODES:-2}

echo "=== pyExaMINE gap-fill regular (${MINERAL}) ==="
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
