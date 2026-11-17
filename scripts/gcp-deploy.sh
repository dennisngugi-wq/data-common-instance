#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
PATH="$PATH:$HOME/google-cloud-sdk/bin"
command -v gcloud >/dev/null || { echo "gcloud not found: install the Google Cloud CLI first"; exit 1; }

GCP_PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
GCP_ZONE="${GCP_ZONE:-africa-south1-a}"
GCP_REGION="${GCP_ZONE%-*}"
GCP_VM="${GCP_VM:-datacommon-project}"
GCP_MACHINE="${GCP_MACHINE:-e2-standard-2}"
GCP_DISK_GB="${GCP_DISK_GB:-50}"
REFRESH_AT_UTC="${REFRESH_AT_UTC:-21:00}"
g() { gcloud --project "$GCP_PROJECT" --quiet "$@"; }
on_vm() { g compute ssh "$GCP_VM" --zone "$GCP_ZONE" --command "$1"; }

[ -n "$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)" ] ||
  { echo "Not signed in: run  gcloud auth login"; exit 1; }
[ -n "$GCP_PROJECT" ] ||
  { echo "No project: run  gcloud config set project <project id>  (or set GCP_PROJECT)"; exit 1; }

case "${1:-}" in
  --status)
    on_vm 'cd ~/datacommon-project && echo "Last refresh: $(cat logs/last_refresh.txt 2>/dev/null || echo none yet)" &&
      if [ -f logs/ALERT.txt ]; then echo "ALERT: $(cat logs/ALERT.txt)"; else echo "No alert."; fi &&
      sudo docker ps --filter name=dcproject-scheduler --filter name=dcproject-website --format "{{.Names}}: {{.Status}}"'
    exit 0 ;;
  --logs)
    on_vm 'tail -n 60 ~/datacommon-project/logs/pipeline.log'
    exit 0 ;;
  "") ;;
  *) echo "usage: gcp-deploy.sh [--status | --logs]"; exit 2 ;;
esac

[ -f env.list ] && grep -q '^DC_API_KEY=.' env.list && ! grep -q 'your_key_here' env.list ||
  { echo "env.list with DC_API_KEY missing: cp env.list.example env.list and add your key"; exit 1; }

echo "Project $GCP_PROJECT · zone $GCP_ZONE · VM $GCP_VM ($GCP_MACHINE, ${GCP_DISK_GB} GB)"
g services enable compute.googleapis.com

g compute addresses describe "$GCP_VM-ip" --region "$GCP_REGION" >/dev/null 2>&1 ||
  g compute addresses create "$GCP_VM-ip" --region "$GCP_REGION"
IP="$(g compute addresses describe "$GCP_VM-ip" --region "$GCP_REGION" --format='value(address)')"
SITE_URL="${SITE_URL:-http://$IP}"

g compute firewall-rules describe "$GCP_VM-web" >/dev/null 2>&1 ||
  g compute firewall-rules create "$GCP_VM-web" --allow tcp:80 --target-tags "$GCP_VM-web" --source-ranges 0.0.0.0/0

if ! g compute instances describe "$GCP_VM" --zone "$GCP_ZONE" >/dev/null 2>&1; then
  g compute instances create "$GCP_VM" --zone "$GCP_ZONE" --machine-type "$GCP_MACHINE" \
    --image-family ubuntu-2204-lts --image-project ubuntu-os-cloud --boot-disk-size "${GCP_DISK_GB}GB" \
    --address "$IP" --tags "$GCP_VM-web"
fi

echo -n "Waiting for the VM to accept SSH "
for _ in $(seq 1 30); do
  if on_vm true >/dev/null 2>&1; then echo " ready."; break; fi
  echo -n "."; sleep 10
done

BUNDLE="$(mktemp --suffix=.tgz)"
trap 'rm -f "$BUNDLE"' EXIT
{ git ls-files -co --exclude-standard -z
  printf 'env.list\0'
  [ -d data/inbox ] && find data/inbox -type f -print0
} | sort -zu | tar --null -T - -czf "$BUNDLE"
g compute scp "$BUNDLE" "$GCP_VM:dcproject.tgz" --zone "$GCP_ZONE"
on_vm "mkdir -p ~/datacommon-project && tar -xzf ~/dcproject.tgz -C ~/datacommon-project scripts/gcp-vm-setup.sh &&
  SITE_URL='$SITE_URL' REFRESH_AT_UTC='$REFRESH_AT_UTC' bash ~/datacommon-project/scripts/gcp-vm-setup.sh"

echo
echo "Site: $SITE_URL  (the first load takes 15-30 minutes)"
echo "Refresh: every day at $REFRESH_AT_UTC UTC (00:00 Africa/Nairobi when 21:00), and at every start of the VM"
echo "Check:   bash scripts/gcp-deploy.sh --status     Log: bash scripts/gcp-deploy.sh --logs"
