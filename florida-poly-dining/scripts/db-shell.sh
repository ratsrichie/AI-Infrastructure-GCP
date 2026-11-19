#!/usr/bin/env bash
# Uses the password file inside the container, without printing it or putting it in argv.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
docker compose exec mariadb sh -c 'export MYSQL_PWD="$(cat /run/secrets/dining_db_password)"; exec mariadb --default-character-set=utf8mb4 -udining_app flpoly_dining'
