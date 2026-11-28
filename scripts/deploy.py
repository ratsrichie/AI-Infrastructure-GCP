#!/usr/bin/env python3
"""deploy.py - build the RAG project's GCP infrastructure with one command.

    python scripts/deploy.py all https://github.com/YOU/REPO.git

Subcommands:
    all <repo_url>   network + firewall + secrets + vm (the single command)
    network          enable APIs, create VPC and subnet
    firewall         allow 80/443, SSH only via IAP, deny database/inference ports
    secrets          service account, random passwords in Secret Manager
    vm <repo_url>    static IP, data disk, VM (its startup script does the rest)
    stop / start     stop or start the VM to save GCP credits
    resize           change the VM type, e.g.  resize --machine e2-standard-4
    destroy          delete everything this script created (asks to confirm)
    status           show the VM and its IP

Every step checks whether the resource already exists, so re-running is safe.
Add --dry-run to print the gcloud commands without running anything.
Requires the gcloud CLI (already installed in Cloud Shell) and `gcloud auth login`.
Adapted from the observability project's deploy/gcp_setup.sh.
"""
import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IAP_RANGE = "35.235.240.0/20"  # Google's Identity-Aware Proxy range, used for SSH
SECRET_NAMES = ["dining-db-password", "dining-root-password"]
STARTUP_SCRIPT = Path(__file__).resolve().parent / "startup.sh"


class Deployer:
    def __init__(self, args):
        self.dry = args.dry_run
        self.gcloud = shutil.which("gcloud") or "gcloud"  # finds gcloud.cmd on Windows
        self.project = args.project or os.environ.get("PROJECT") or self._config_project()
        if not self.project:
            sys.exit("error: no project set. Use --project ID or: gcloud config set project ID")
        self.region, self.zone = args.region, args.zone
        self.machine = args.machine or "e2-small"
        self.machine_arg = args.machine
        self.assume_yes = args.yes
        self.disk_gb, self.db_lv_size = args.data_disk_gb, args.db_lv_size

        name = "rag"
        self.vpc, self.subnet = f"{name}-vpc", f"{name}-subnet"
        self.subnet_range = "10.10.0.0/24"
        self.tag = f"{name}-vm"
        self.sa_name = f"{name}-vm"
        self.sa_email = f"{self.sa_name}@{self.project}.iam.gserviceaccount.com"
        self.vm, self.addr, self.disk = f"{name}-vm", f"{name}-ip", f"{name}-data"
        self.name = name

    # ---------- helpers ----------
    def _config_project(self):
        try:
            out = subprocess.run([self.gcloud, "config", "get-value", "project"],
                                 capture_output=True, text=True)
            return out.stdout.strip()
        except OSError:
            return ""

    @staticmethod
    def info(msg):
        print(f"==> {msg}")

    def run(self, *cmd, stdin=None):
        """Run a gcloud command (or just print it in dry-run mode)."""
        full = [self.gcloud, *cmd, "--project", self.project]
        if self.dry:
            note = "   (random value piped in on stdin)" if stdin is not None else ""
            print("+ " + " ".join(full) + note)
            return
        subprocess.run(full, input=stdin, text=True, check=True)

    def exists(self, *describe_cmd):
        """True if a 'gcloud ... describe' succeeds. Always False in dry-run so every step prints."""
        if self.dry:
            return False
        result = subprocess.run([self.gcloud, *describe_cmd, "--project", self.project],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0

    # ---------- steps ----------
    def network(self):
        self.info(f"Enabling APIs in project {self.project}")
        self.run("services", "enable", "compute.googleapis.com", "secretmanager.googleapis.com",
                 "iam.googleapis.com", "iap.googleapis.com")

        if self.exists("compute", "networks", "describe", self.vpc):
            self.info(f"VPC {self.vpc} already exists")
        else:
            self.info(f"Creating VPC {self.vpc}")
            self.run("compute", "networks", "create", self.vpc, "--subnet-mode=custom")

        if self.exists("compute", "networks", "subnets", "describe", self.subnet,
                       "--region", self.region):
            self.info(f"Subnet {self.subnet} already exists")
        else:
            self.info(f"Creating subnet {self.subnet} ({self.subnet_range})")
            self.run("compute", "networks", "subnets", "create", self.subnet,
                     f"--network={self.vpc}", f"--region={self.region}",
                     f"--range={self.subnet_range}")

    def _fw_rule(self, name, priority, action, rules, source):
        if self.exists("compute", "firewall-rules", "describe", name):
            self.info(f"Firewall rule {name} already exists")
            return
        self.info(f"Creating firewall rule {name} ({action} {rules} from {source})")
        self.run("compute", "firewall-rules", "create", name, f"--network={self.vpc}",
                 "--direction=INGRESS", f"--priority={priority}", f"--action={action}",
                 f"--rules={rules}", f"--source-ranges={source}", f"--target-tags={self.tag}")

    def firewall(self):
        # Only the load balancer ports reach the internet. SSH goes through IAP.
        # The database and inference ports are denied explicitly (documented intent).
        self._fw_rule(f"{self.name}-allow-web", 1000, "ALLOW", "tcp:80,tcp:443", "0.0.0.0/0")
        self._fw_rule(f"{self.name}-allow-iap-ssh", 1000, "ALLOW", "tcp:22", IAP_RANGE)
        self._fw_rule(f"{self.name}-deny-db-inference", 900, "DENY",
                      "tcp:3306,tcp:5432,tcp:11434", "0.0.0.0/0")

    def secrets(self):
        if self.exists("iam", "service-accounts", "describe", self.sa_email):
            self.info(f"Service account {self.sa_name} already exists")
        else:
            self.info(f"Creating service account {self.sa_name}")
            self.run("iam", "service-accounts", "create", self.sa_name,
                     "--display-name=RAG VM service account")

        for secret in SECRET_NAMES:
            if self.exists("secrets", "describe", secret):
                self.info(f"Secret {secret} already exists")
            else:
                self.info(f"Creating secret {secret} with a random value")
                # The password only ever lives in memory and in Secret Manager.
                self.run("secrets", "create", secret, "--replication-policy=automatic",
                         "--data-file=-", stdin=secrets.token_hex(24))
            # Least privilege: the VM may read only these secrets.
            self.run("secrets", "add-iam-policy-binding", secret,
                     f"--member=serviceAccount:{self.sa_email}",
                     "--role=roles/secretmanager.secretAccessor", "--quiet")

        for role in ("roles/logging.logWriter", "roles/monitoring.metricWriter"):
            self.run("projects", "add-iam-policy-binding", self.project,
                     f"--member=serviceAccount:{self.sa_email}", f"--role={role}", "--quiet")

    def _startup_file(self):
        """Copy startup.sh with Unix line endings (Windows git can add CRLF, which breaks bash)."""
        text = STARTUP_SCRIPT.read_text().replace("\r\n", "\n")
        tmp = Path(tempfile.mkdtemp()) / "startup.sh"
        tmp.write_text(text, newline="\n")
        return tmp

    def create_vm(self, repo_url):
        if not repo_url or not repo_url.startswith("https://"):
            sys.exit("error: give the team repo's public HTTPS URL, e.g. https://github.com/YOU/REPO.git")

        if self.exists("compute", "addresses", "describe", self.addr, "--region", self.region):
            self.info(f"Static IP {self.addr} already exists")
        else:
            self.info(f"Reserving static IP {self.addr}")
            self.run("compute", "addresses", "create", self.addr, f"--region={self.region}")

        if self.exists("compute", "disks", "describe", self.disk, "--zone", self.zone):
            self.info(f"Data disk {self.disk} already exists")
        else:
            self.info(f"Creating {self.disk_gb}GB data disk {self.disk} (becomes the LVM volume group)")
            self.run("compute", "disks", "create", self.disk, f"--size={self.disk_gb}GB",
                     "--type=pd-balanced", f"--zone={self.zone}")

        if self.exists("compute", "instances", "describe", self.vm, "--zone", self.zone):
            self.info(f"VM {self.vm} already exists")
        else:
            self.info(f"Creating VM {self.vm} ({self.machine})")
            startup = STARTUP_SCRIPT if self.dry else self._startup_file()
            self.run("compute", "instances", "create", self.vm, f"--zone={self.zone}",
                     f"--machine-type={self.machine}", f"--subnet={self.subnet}",
                     f"--address={self.addr}", "--image-family=debian-12",
                     "--image-project=debian-cloud", "--boot-disk-size=20GB",
                     f"--disk=name={self.disk},device-name=data,mode=rw,boot=no",
                     f"--service-account={self.sa_email}", "--scopes=cloud-platform",
                     f"--tags={self.tag}",
                     f"--metadata=repo-url={repo_url},project-id={self.project},"
                     f"db-lv-size={self.db_lv_size}",
                     f"--metadata-from-file=startup-script={startup}")

        if not self.dry:
            ip = subprocess.run(
                [self.gcloud, "compute", "addresses", "describe", self.addr,
                 f"--region={self.region}", "--project", self.project, "--format=value(address)"],
                capture_output=True, text=True).stdout.strip()
            print(f"\nVM {self.vm} is up. Static IP: {ip}")
            print("The startup script needs a few minutes. Watch it with:")
            print(f"  gcloud compute ssh {self.vm} --zone {self.zone} --tunnel-through-iap "
                  f"--command 'sudo tail -f /var/log/rag-startup.log'")

    def stop(self):
        self.info(f"Stopping {self.vm} (disks and IP keep a small charge; compute stops)")
        self.run("compute", "instances", "stop", self.vm, f"--zone={self.zone}")

    def start(self):
        self.info(f"Starting {self.vm}; the startup script re-checks everything on boot")
        self.run("compute", "instances", "start", self.vm, f"--zone={self.zone}")

    def resize(self):
        if not self.machine_arg:
            sys.exit("error: say which type, e.g.  resize --machine e2-standard-4")
        self.info(f"Resizing {self.vm} to {self.machine} (the VM restarts, so it is briefly offline)")
        self.run("compute", "instances", "stop", self.vm, f"--zone={self.zone}")
        self.run("compute", "instances", "set-machine-type", self.vm, f"--zone={self.zone}",
                 f"--machine-type={self.machine}")
        self.run("compute", "instances", "start", self.vm, f"--zone={self.zone}")
        self.info("Done. Stop the VM again when you finish testing to save credits.")

    def _delete(self, label, describe_cmd, delete_cmd):
        """Delete one resource if it exists."""
        if self.dry or self.exists(*describe_cmd):
            self.info(f"Deleting {label}")
            self.run(*delete_cmd, "--quiet")
        else:
            self.info(f"{label} not found, skipping")

    def destroy(self):
        print("This deletes the VM, its data disk (the database and models!), the static IP,")
        print(f"the firewall rules, the secrets, the service account and the VPC in {self.project}.")
        if not self.dry and not self.assume_yes:
            answer = input(f"Type the project ID ({self.project}) to confirm: ").strip()
            if answer != self.project:
                sys.exit("Cancelled, nothing deleted.")

        z, r = f"--zone={self.zone}", f"--region={self.region}"
        # Order matters: the VM must go before its disk, and the network goes last.
        self._delete(f"VM {self.vm}", ("compute", "instances", "describe", self.vm, z),
                     ("compute", "instances", "delete", self.vm, z))
        self._delete(f"data disk {self.disk}", ("compute", "disks", "describe", self.disk, z),
                     ("compute", "disks", "delete", self.disk, z))
        self._delete(f"static IP {self.addr}",
                     ("compute", "addresses", "describe", self.addr, r),
                     ("compute", "addresses", "delete", self.addr, r))
        for rule in ("allow-web", "allow-iap-ssh", "deny-db-inference"):
            name = f"{self.name}-{rule}"
            self._delete(f"firewall rule {name}",
                         ("compute", "firewall-rules", "describe", name),
                         ("compute", "firewall-rules", "delete", name))
        for secret in SECRET_NAMES:
            self._delete(f"secret {secret}", ("secrets", "describe", secret),
                         ("secrets", "delete", secret))
        if self.exists("iam", "service-accounts", "describe", self.sa_email) or self.dry:
            for role in ("roles/logging.logWriter", "roles/monitoring.metricWriter"):
                self.run("projects", "remove-iam-policy-binding", self.project,
                         f"--member=serviceAccount:{self.sa_email}", f"--role={role}", "--quiet")
        self._delete(f"service account {self.sa_name}",
                     ("iam", "service-accounts", "describe", self.sa_email),
                     ("iam", "service-accounts", "delete", self.sa_email))
        self._delete(f"subnet {self.subnet}",
                     ("compute", "networks", "subnets", "describe", self.subnet, r),
                     ("compute", "networks", "subnets", "delete", self.subnet, r))
        self._delete(f"VPC {self.vpc}", ("compute", "networks", "describe", self.vpc),
                     ("compute", "networks", "delete", self.vpc))
        self.info("Everything removed. Rebuild with: python3 scripts/deploy.py all <repo_url>")

    def status(self):
        self.run("compute", "instances", "list", f"--filter=name={self.vm}",
                 "--format=table(name,zone.basename(),machineType.basename(),status,"
                 "networkInterfaces[0].accessConfigs[0].natIP)")


def main():
    p = argparse.ArgumentParser(description="Build the RAG project's GCP infrastructure.")
    p.add_argument("command", choices=["all", "network", "firewall", "secrets", "vm",
                                       "stop", "start", "resize", "destroy", "status"])
    p.add_argument("repo_url", nargs="?", help="public HTTPS URL of the team repo (all, vm)")
    p.add_argument("--project", help="GCP project ID (default: gcloud's current project)")
    p.add_argument("--region", default="us-central1")
    p.add_argument("--zone", default="us-central1-a")
    p.add_argument("--machine", help="VM type (default e2-small to save credits); required for resize")
    p.add_argument("--data-disk-gb", type=int, default=30)
    p.add_argument("--db-lv-size", default="5G", help="size of the database logical volume")
    p.add_argument("--yes", action="store_true", help="destroy without asking for confirmation")
    p.add_argument("--dry-run", action="store_true", help="print gcloud commands, change nothing")
    args = p.parse_args()

    d = Deployer(args)
    try:
        if args.command == "all":
            d.network()
            d.firewall()
            d.secrets()
            d.create_vm(args.repo_url)
        elif args.command == "vm":
            d.create_vm(args.repo_url)
        else:
            getattr(d, args.command)()
    except subprocess.CalledProcessError as err:
        sys.exit(f"error: a gcloud command failed (exit code {err.returncode}); fix it and re-run, "
                 "completed steps are skipped")


if __name__ == "__main__":
    main()