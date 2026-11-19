#!/usr/bin/env bash
# Read-only runtime smoke check after docker compose up -d --wait.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
actual=$(docker compose exec -T mariadb sh -c 'export MYSQL_PWD="$(cat /run/secrets/dining_db_password)"; exec mariadb -N -B -udining_app flpoly_dining -e "SELECT (SELECT COUNT(*) FROM restaurants), (SELECT COUNT(*) FROM menu_items), (SELECT COUNT(*) FROM rag_chunks);"')
expected=$'4\t521\t527'
if [[ "$actual" != "$expected" ]]; then
  printf 'FAIL: expected 4 restaurants, 521 listings, 527 chunks; got %s\n' "$actual" >&2
  exit 1
fi
printf 'PASS: 4 restaurants, 521 menu listings, 527 RAG chunks.\n'
