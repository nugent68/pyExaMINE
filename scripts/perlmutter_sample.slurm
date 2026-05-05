#!/bin/bash
# pyExaMINE -- generate surrogate-training data on Perlmutter.
#
# Single Slurm job that fans 6000 short scenario runs across one CPU
# node using GNU parallel. We deliberately avoid Slurm arrays here:
# each scenario is only ~25 s, so per-task Slurm overhead would dominate.
# A 128-core CPU node + parallel -j 128 finishes the full sweep
# (3 minerals * 2000 scenarios) in roughly 20 minutes wall.
#
# Outputs land under $CFS/amsc001/www/surrogate_data/, which is web-
# visible at https://portal.nersc.gov/project/amsc001/surrogate_data/
#
# Submit with:
#   # 1. Generate scenarios on the login node first (cheap; <1 min).
#   #    This step also creates the scenarios/ subdir under CFS.
#   shifter --image=docker:nugent68/pyexamine:latest \
#       --volume=$CFS/amsc001/www/surrogate_data:/data \
#       bash -c "cd /app && uv run python scripts/sample_scenarios.py \
#           --n 2000 --out /data/scenarios"
#
#   # 2. Submit the job (defaults to all 3 minerals, 2000 each):
#   sbatch scripts/perlmutter_sample_array.slurm
#
# Override defaults via --export, e.g.:
#   sbatch --export=MINERALS=lithium,N_PER_MINERAL=500 ...
#
# Resumability: --skip-existing on run_one_scenario means re-submitting
# the same job (or any subset of minerals) only fills gaps; completed
# scenarios are skipped without re-running.
#
#SBATCH --image=docker:nugent68/pyexamine:latest
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=01:00:00
#SBATCH --job-name=pyexamine_sample
#SBATCH --output=logs/sample_%j.log

set -euo pipefail

# Knobs (override at submit time via sbatch --export=).
: "${MINERALS:=lithium nickel platinum}"
: "${N_PER_MINERAL:=2000}"
: "${PARALLELISM:=128}"

DATA_ROOT="${CFS}/amsc001/www/surrogate_data"
mkdir -p "${DATA_ROOT}/runs" logs

echo "=== pyExaMINE training-data generation ==="
echo "Job:           $SLURM_JOB_ID on $(hostname)"
echo "DATA_ROOT:     $DATA_ROOT"
echo "Minerals:      $MINERALS"
echo "N per mineral: $N_PER_MINERAL"
echo "Parallelism:   $PARALLELISM"
echo "Started:       $(date)"
echo

# Per-task wrapper: invoke shifter for one (mineral, index) pair.
# Exported so GNU parallel sees it via env_parallel-style spawn.
run_task() {
    local mineral=$1 idx=$2 data_root=$3
    shifter --module=cpu --volume="${data_root}:/data" \
        bash -c "cd /app && uv run python scripts/run_one_scenario.py \
            --scenarios /data/scenarios/${mineral}.json \
            --index ${idx} \
            --output-root /data/runs \
            --skip-existing"
}
export -f run_task

# Each mineral's 2000 scenarios run as one parallel pipeline. We
# don't interleave minerals: completes one at a time so log output is
# easier to follow and disk I/O stays predictable.
for MINERAL in $MINERALS; do
    SCEN="${DATA_ROOT}/scenarios/${MINERAL}.json"
    if [[ ! -f "$SCEN" ]]; then
        echo "[${MINERAL}] missing scenarios file ${SCEN}; skipping"
        continue
    fi
    mkdir -p "${DATA_ROOT}/runs/${MINERAL}"
    JOBLOG="logs/parallel_${SLURM_JOB_ID}_${MINERAL}.log"
    echo "[${MINERAL}] starting ${N_PER_MINERAL} scenarios at $(date)"
    seq 0 $((N_PER_MINERAL - 1)) | \
        parallel \
            --jobs "${PARALLELISM}" \
            --joblog "${JOBLOG}" \
            --halt=soon,fail=20 \
            --eta \
            run_task "${MINERAL}" {} "${DATA_ROOT}" \
        || echo "[${MINERAL}] some tasks failed; see ${JOBLOG}"
    echo "[${MINERAL}] finished at $(date)"
    echo "[${MINERAL}] joblog: ${JOBLOG}"
done

echo
echo "Finished: $(date)"
echo "Outputs:  ${DATA_ROOT}/runs/"
echo "Web URL:  https://portal.nersc.gov/project/amsc001/surrogate_data/runs/"
