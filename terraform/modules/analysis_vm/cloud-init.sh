#!/bin/bash
set -e

# Log everything to syslog and /var/log/cloud-init-output.log
exec > >(tee -a /var/log/cloud-init-output.log | logger -t user-data -s 2>/dev/console) 2>&1

echo "Starting cloud-init script..."

# --- Resilience helpers -------------------------------------------------------
# Managed-identity RBAC (Key Vault Secrets User, Storage Blob Data Reader) can take a
# few minutes to propagate after the VM is created. Retry transient az failures instead
# of letting `set -e` abort the whole bootstrap on the first miss.
retry() {
  local max="$${RETRY_MAX:-30}" delay="$${RETRY_DELAY:-10}" n=1
  until "$@"; do
    if [ "$n" -ge "$max" ]; then
      echo "ERROR: command failed after $${n} attempts: $*" >&2
      return 1
    fi
    echo "Attempt $${n}/$${max} failed; retrying in $${delay}s: $*" >&2
    n=$((n + 1)); sleep "$delay"
  done
}

fetch_secret() {
  # Echo a Key Vault secret value, retrying until RBAC has propagated.
  local name="$1" val="" i
  for i in $(seq 1 "$${RETRY_MAX:-30}"); do
    if val=$(az keyvault secret show --name "$name" --vault-name "${key_vault_name}" --query value -o tsv 2>/dev/null); then
      printf '%s' "$val"; return 0
    fi
    echo "Key Vault secret '$${name}' not ready (attempt $${i}); retrying in 10s..." >&2
    sleep 10
  done
  echo "ERROR: could not fetch Key Vault secret '$${name}' after retries" >&2
  return 1
}
# -----------------------------------------------------------------------------

# 1. Mount data disk (Premium SSD, LUN 10)
# Wait for the disk to be attached
until [ -b /dev/disk/azure/scsi1/lun10 ]; do
  echo "Waiting for data disk..."
  sleep 2
done

disk_dev="/dev/disk/azure/scsi1/lun10"
if ! blkid "$disk_dev" | grep -q "ext4"; then
  echo "Formatting disk $disk_dev..."
  mkfs.ext4 -F "$disk_dev"
fi

echo "Mounting disk..."
mkdir -p /data-disk
mount "$disk_dev" /data-disk

# Add to fstab if not already present
if ! grep -q "/data-disk" /etc/fstab; then
  UUID=$(blkid -o value -s UUID "$disk_dev")
  echo "UUID=$UUID /data-disk ext4 defaults,nofail 0 2" >> /etc/fstab
fi

# Create directory structures
mkdir -p /app/config
mkdir -p /app/keycloak/realms
mkdir -p /data-disk/system
mkdir -p /data-disk/armadillo
mkdir -p /data-disk/keycloak-db
chmod -R 777 /data-disk

# Create symlink for data directory if not exists
if [ ! -L /app/data ]; then
  ln -s /data-disk /app/data
fi

# 2. Install Docker and Docker Compose
echo "Installing Docker..."
apt-get update
apt-get install -y apt-transport-https ca-certificates curl software-properties-common gnupg lsb-release

mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker

# 3. Install Azure CLI
echo "Installing Azure CLI..."
curl -sL https://aka.ms/InstallAzureCLIDeb | bash

# Log in with VM's managed identity (retry: MSI/RBAC may not be ready immediately).
# Newer az CLI requires --client-id for a user-assigned identity (--username was removed).
echo "Logging in to Azure..."
retry az login --identity --client-id "${user_assigned_client_id}"

# Fetch config files from Storage Account (retry: Storage Blob Data Reader RBAC propagation)
echo "Downloading configurations from Azure Storage..."
retry az storage blob download --account-name "${storage_account_name}" --container-name "analysis-config" --name "docker-compose.analysis.yml" --file "/app/docker-compose.analysis.yml" --auth-mode login
retry az storage blob download --account-name "${storage_account_name}" --container-name "analysis-config" --name "application.yml" --file "/app/config/application.yml" --auth-mode login
retry az storage blob download --account-name "${storage_account_name}" --container-name "analysis-config" --name "Armadillo.json" --file "/app/keycloak/realms/Armadillo.json" --auth-mode login
retry az storage blob download --account-name "${storage_account_name}" --container-name "analysis-config" --name "profiles.json" --file "/app/data/system/profiles.json" --auth-mode login

# Fetch secrets from Key Vault (retry: Key Vault Secrets User RBAC propagation)
echo "Fetching secrets from Key Vault..."
ARMADILLO_ADMIN_PASSWORD=$(fetch_secret "armadillo-admin-password")
KEYCLOAK_ADMIN_PASSWORD=$(fetch_secret "keycloak-admin-password")
KEYCLOAK_DB_PASSWORD=$(fetch_secret "keycloak-db-password")
ROCK_ADMIN_PASSWORD=$(fetch_secret "rock-admin-password")
ROCK_USER_PASSWORD=$(fetch_secret "rock-user-password")
ARMADILLO_OIDC_CLIENT_SECRET=$(fetch_secret "armadillo-oidc-client-secret")

# 4. Replace placeholders in Armadillo.json
echo "Replacing placeholders in Armadillo.json..."
sed -i "s|ARMADILLO_CANONICAL_URL_PLACEHOLDER|http://armadillo.harmonaize.internal:8080|g" /app/keycloak/realms/Armadillo.json
sed -i "s|ARMADILLO_OIDC_CLIENT_SECRET_PLACEHOLDER|$ARMADILLO_OIDC_CLIENT_SECRET|g" /app/keycloak/realms/Armadillo.json

# 5. Write .env file
echo "Writing .env file..."
cat << EOF > /app/.env
ARMADILLO_ADMIN_PASSWORD=$ARMADILLO_ADMIN_PASSWORD
ARMADILLO_OIDC_CLIENT_SECRET=$ARMADILLO_OIDC_CLIENT_SECRET
ROCK_ADMIN_PASSWORD=$ROCK_ADMIN_PASSWORD
ROCK_USER_PASSWORD=$ROCK_USER_PASSWORD
KEYCLOAK_ADMIN_PASSWORD=$KEYCLOAK_ADMIN_PASSWORD
KEYCLOAK_DB_PASSWORD=$KEYCLOAK_DB_PASSWORD
EOF

# 6. Start Docker Compose
echo "Starting containers..."
cd /app
docker compose -f docker-compose.analysis.yml up -d

echo "Cloud-init finished successfully!"
