#!/usr/bin/env bash
# Runs on EVERY boot of the VM (GCE startup-script). Idempotent: each step checks
# the current state first and only builds what is missing, so a reboot or a
# re-run never breaks or duplicates anything.
set -euo pipefail
exec >> /var/log/rag-startup.log 2>&1
echo "=== startup $(date -Is) ==="

MD="http://metadata.google.internal/computeMetadata/v1"
meta() { curl -sf -H "Metadata-Flavor: Google" "$MD/$1"; }

PROJECT_ID="$(meta instance/attributes/project-id)"
REPO_URL="$(meta instance/attributes/repo-url)"
DB_LV_SIZE="$(meta instance/attributes/db-lv-size)"

DISK=/dev/disk/by-id/google-data
VG=rag_vg
APP_DIR=/opt/rag

# ---------- 1. Packages and Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg lvm2 git jq
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
fi
command -v lvs >/dev/null 2>&1 || { apt-get update -y; apt-get install -y lvm2; }
command -v jq  >/dev/null 2>&1 || { apt-get update -y; apt-get install -y jq git; }

# ---------- 2. LVM: two logical volumes on the data disk ----------
# db     -> /data/db  (database files)
# models -> /models   (model weights)
# Part of the volume group is left free so LVM snapshots can be taken.
if ! pvs "$DISK" >/dev/null 2>&1; then
  pvcreate "$DISK"
fi
if ! vgs "$VG" >/dev/null 2>&1; then
  vgcreate "$VG" "$DISK"
fi
if ! lvs "$VG/db" >/dev/null 2>&1; then
  lvcreate -L "$DB_LV_SIZE" -n db "$VG"
fi
if ! lvs "$VG/models" >/dev/null 2>&1; then
  lvcreate -l 60%FREE -n models "$VG"
fi

# Format only if there is no filesystem yet (never wipe existing data).
for lv in db models; do
  if ! blkid "/dev/$VG/$lv" >/dev/null 2>&1; then
    mkfs.ext4 -L "rag_$lv" "/dev/$VG/$lv"
  fi
done

mkdir -p /data/db /models
# Persistent mounts across reboots; add each line only once.
grep -q "/dev/$VG/db " /etc/fstab     || echo "/dev/$VG/db /data/db ext4 defaults,nofail 0 2" >> /etc/fstab
grep -q "/dev/$VG/models " /etc/fstab || echo "/dev/$VG/models /models ext4 defaults,nofail 0 2" >> /etc/fstab
mountpoint -q /data/db || mount /data/db
mountpoint -q /models  || mount /models

# A fresh ext4 volume contains lost+found, which would make MariaDB think its
# data directory is not empty. So the database uses a subfolder of the volume.
mkdir -p /data/db/mariadb
chown 999:999 /data/db/mariadb   # uid of the mysql user inside the mariadb image

# ---------- 3. Application code ----------
if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only || echo "pull failed, continuing with existing code"
fi
cd "$APP_DIR"

# ---------- 4. Secrets from Secret Manager (never stored in git) ----------
TOKEN="$(meta instance/service-accounts/default/token | jq -r .access_token)"
fetch_secret() {
  curl -sf -H "Authorization: Bearer $TOKEN" \
    "https://secretmanager.googleapis.com/v1/projects/$PROJECT_ID/secrets/$1/versions/latest:access" \
    | jq -r .payload.data | base64 -d
}
mkdir -p secrets init
umask 077
fetch_secret dining-db-password   > secrets/db_password.txt
fetch_secret dining-root-password > secrets/root_password.txt
umask 022

# ---------- 5. Start containers (a no-op if they are already running) ----------
export DINING_DB_PATH=/data/db/mariadb
docker compose up -d --wait
echo "=== startup finished $(date -Is) ==="