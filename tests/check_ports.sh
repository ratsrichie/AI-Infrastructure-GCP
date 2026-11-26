#!/usr/bin/env bash
# Network security proof: run from OUTSIDE the VPC.
# Usage: bash tests/check_ports.sh <external-ip>
# PASS = 80 reachable, database and inference ports blocked. Output is also saved to tests/results/.
set -u
IP="${1:?usage: check_ports.sh <external-ip>}"
mkdir -p "$(dirname "$0")/results"
OUT="$(dirname "$0")/results/ports_$(date +%Y%m%d_%H%M%S).log"

check() {  # port, expected (open|closed)
  if timeout 4 bash -c "exec 3<>/dev/tcp/$IP/$1" 2>/dev/null; then state=open; else state=closed; fi
  if [ "$state" = "$2" ]; then r=PASS; else r=FAIL; fi
  echo "$r  port $1 is $state (expected $2)"
}

{
  echo "Port check against $IP at $(date -Is)"
  check 80 open
  check 3306 closed    # MariaDB
  check 5432 closed    # PostgreSQL, in case a team member uses it
  check 11434 closed   # Ollama
  check 22 closed      # SSH only via IAP
} | tee "$OUT"
echo "saved to $OUT"