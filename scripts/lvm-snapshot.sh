#!/usr/bin/env bash
# Takes an LVM snapshot of the database volume.
# Usage:  sudo bash scripts/lvm-snapshot.sh        create a snapshot
#         sudo bash scripts/lvm-snapshot.sh remove  delete the newest snapshot
# The snapshot is crash-consistent: InnoDB recovers from it like after a power cut.
set -euo pipefail
VG=rag_vg
if [ "${1:-}" = "remove" ]; then
  snap="$(lvs --noheadings -o lv_name "$VG" | tr -d ' ' | grep '^db_snap_' | sort | tail -1)"
  [ -n "$snap" ] && lvremove -f "$VG/$snap"
  exit 0
fi
name="db_snap_$(date +%Y%m%d_%H%M%S)"
lvcreate --snapshot --size 2G --name "$name" "$VG/db"
echo "--- logical volumes after snapshot ---"
lvs "$VG"