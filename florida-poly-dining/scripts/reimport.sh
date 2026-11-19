#!/usr/bin/env bash
# Reimport this snapshot without removing existing database storage.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
docker compose exec -T mariadb sh -c 'export MYSQL_PWD="$(cat /run/secrets/dining_db_password)"; exec mariadb --default-character-set=utf8mb4 -udining_app flpoly_dining' < init/01_dining.sql
