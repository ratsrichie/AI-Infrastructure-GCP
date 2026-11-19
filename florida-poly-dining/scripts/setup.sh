#!/usr/bin/env bash
# Generate local secrets only if absent. On GCP, supply these from Secret Manager.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
umask 077
mkdir -p secrets "${DINING_DB_PATH:-./data/db}"
for secret in secrets/db_password.txt secrets/root_password.txt; do
  if [[ ! -e "$secret" ]]; then
    openssl rand -hex 32 > "$secret"
  elif [[ ! -s "$secret" ]]; then
    printf 'Secret file exists but is empty: %s\n' "$secret" >&2
    exit 1
  fi
done
printf 'Storage directory and password files are ready.\n'
