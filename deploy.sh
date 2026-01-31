#!/bin/bash
set -e

# ============================================================================
# Configuration
# ============================================================================
GCP_PROJECT="gen-lang-client-0011138066"
GCP_ZONE="europe-central2-a"
VM_NAME="frozen-server"
VM_USER="utrobin_serbia_gmail_com"
DEPLOY_PATH="/home/utrobin_serbia_gmail_com/frozen"
SERVICE_NAME="frozen-api"

# ============================================================================
# Deployment Script
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Deploying to ${VM_NAME} ===${NC}"

# Check config
if [ "$GCP_PROJECT" = "your-project-id" ]; then
  echo -e "${RED}Error: Set GCP_PROJECT in deploy.sh${NC}"
  exit 1
fi

# Check gcloud auth
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
  echo -e "${RED}Error: Run 'gcloud auth login'${NC}"
  exit 1
fi

gcloud config set project "$GCP_PROJECT" --quiet

# Sync code
echo -e "${YELLOW}[1/4] Syncing code...${NC}"
tar czf - --no-xattrs \
  --exclude=".git" \
  --exclude=".venv" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".mypy_cache" \
  --exclude=".ruff_cache" \
  --exclude=".ipynb_checkpoints" \
  --exclude="*.ipynb" \
  --exclude=".DS_Store" \
  . | gcloud compute ssh "${VM_USER}@${VM_NAME}" \
  --zone="$GCP_ZONE" \
  --command="mkdir -p ${DEPLOY_PATH} && cd ${DEPLOY_PATH} && tar xzf -"

# Install deps
echo -e "${YELLOW}[2/4] Installing dependencies...${NC}"
gcloud compute ssh "${VM_USER}@${VM_NAME}" \
  --zone="$GCP_ZONE" \
  --command="cd ${DEPLOY_PATH} && ~/.local/bin/uv sync --frozen" \
  --quiet

# Update service
echo -e "${YELLOW}[3/4] Updating service...${NC}"
gcloud compute ssh "${VM_USER}@${VM_NAME}" \
  --zone="$GCP_ZONE" \
  --command="sudo cp ${DEPLOY_PATH}/frozen-api.service /etc/systemd/system/ && sudo systemctl daemon-reload" \
  --quiet

# Restart
echo -e "${YELLOW}[4/4] Restarting...${NC}"
gcloud compute ssh "${VM_USER}@${VM_NAME}" \
  --zone="$GCP_ZONE" \
  --command="sudo systemctl restart ${SERVICE_NAME} && sleep 2 && sudo systemctl status ${SERVICE_NAME} --no-pager -l" \
  --quiet

VM_IP=$(gcloud compute instances describe "$VM_NAME" --zone="$GCP_ZONE" --format="get(networkInterfaces[0].accessConfigs[0].natIP)")

echo ""
echo -e "${GREEN}✓ Deployed to http://${VM_IP}:8000${NC}"
echo ""
echo "Useful commands:"
echo "  Logs:    gcloud compute ssh ${VM_USER}@${VM_NAME} --zone=${GCP_ZONE} --command='sudo journalctl -u ${SERVICE_NAME} -f'"
echo "  Restart: gcloud compute ssh ${VM_USER}@${VM_NAME} --zone=${GCP_ZONE} --command='sudo systemctl restart ${SERVICE_NAME}'"
echo "  SSH:     gcloud compute ssh ${VM_USER}@${VM_NAME} --zone=${GCP_ZONE}"
