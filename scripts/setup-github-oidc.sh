#!/usr/bin/env bash
#
# One-time setup of a GitHub Actions OIDC deploy identity for HarmonAIze.
#
# Creates an Entra app registration + service principal, a federated credential scoped to
# this repo/branch (so GitHub can log in to Azure with NO stored secret), and — unless
# --no-roles — grants it AcrPush on the registry and Contributor on the resource group.
#
# Run it once, logged in (`az login`) as an Owner or User Access Administrator of the
# subscription, AFTER `terraform apply` has created the ACR/resource group.
#
# Usage:
#   scripts/setup-github-oidc.sh [options]
#     --repo ORG/REPO     GitHub repo (default: derived from `git remote origin`)
#     --branch NAME       Branch the workflow runs on (default: main)
#     --rg NAME           Resource group (default: harmonaize-prod-rg)
#     --app-name NAME     Entra app display name (default: harmonaize-github-deploy)
#     --no-roles          Don't assign roles; print cicd_principal_id for Terraform instead
#     --set-gh-vars       Push the GitHub Actions variables automatically via the `gh` CLI
#
set -euo pipefail

REPO=""
BRANCH="main"
RG="harmonaize-prod-rg"
APP_NAME="harmonaize-github-deploy"
ASSIGN_ROLES=1
SET_GH_VARS=0

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --rg) RG="$2"; shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    --no-roles) ASSIGN_ROLES=0; shift ;;
    --set-gh-vars) SET_GH_VARS=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage 1 ;;
  esac
done

# Derive ORG/REPO from the git remote when not given explicitly.
if [ -z "$REPO" ]; then
  origin=$(git config --get remote.origin.url 2>/dev/null || true)
  REPO=$(printf '%s' "$origin" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
fi
[ -n "$REPO" ] || { echo "Could not determine --repo (ORG/REPO)." >&2; exit 1; }

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

echo "Repo:          $REPO"
echo "Branch:        $BRANCH"
echo "Subscription:  $SUB_ID"
echo "Resource grp:  $RG"
echo "App name:      $APP_NAME"
echo

# 1. App registration (reuse if it already exists).
APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -z "$APP_ID" ]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
  echo "Created app registration:  $APP_ID"
else
  echo "Reusing app registration:  $APP_ID"
fi

# 2. Service principal for the app (reuse if it already exists).
SP_OBJECT_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv)
if [ -z "$SP_OBJECT_ID" ]; then
  SP_OBJECT_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
  echo "Created service principal:  $SP_OBJECT_ID"
else
  echo "Reusing service principal:  $SP_OBJECT_ID"
fi

# 3. Federated credential: lets GitHub Actions on refs/heads/<branch> exchange its OIDC
#    token for an Azure token. The subject MUST match the workflow's trigger (branch ref).
FC_NAME="github-${BRANCH}"
SUBJECT="repo:${REPO}:ref:refs/heads/${BRANCH}"
if az ad app federated-credential list --id "$APP_ID" --query "[?name=='$FC_NAME'] | [0].name" -o tsv | grep -q .; then
  echo "Federated credential '$FC_NAME' already exists."
else
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"$FC_NAME\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" -o none
  echo "Created federated credential: $SUBJECT"
fi

# 4. Role assignments. --assignee-object-id avoids AAD-replication races on a new SP.
if [ "$ASSIGN_ROLES" -eq 1 ]; then
  ACR_ID=$(az acr list -g "$RG" --query "[0].id" -o tsv)
  [ -n "$ACR_ID" ] || { echo "No ACR found in $RG — run terraform apply first." >&2; exit 1; }
  echo "Assigning AcrPush on the registry ..."
  az role assignment create --assignee-object-id "$SP_OBJECT_ID" --assignee-principal-type ServicePrincipal \
    --role AcrPush --scope "$ACR_ID" -o none 2>/dev/null || echo "  (already assigned)"
  echo "Assigning Contributor on $RG ..."
  az role assignment create --assignee-object-id "$SP_OBJECT_ID" --assignee-principal-type ServicePrincipal \
    --role Contributor --scope "/subscriptions/$SUB_ID/resourceGroups/$RG" -o none 2>/dev/null || echo "  (already assigned)"
else
  echo "Skipping role assignments (--no-roles). Add this to terraform.tfvars and re-apply:"
  echo "    cicd_principal_id = \"$SP_OBJECT_ID\""
fi

ACR_LOGIN_SERVER=$(az acr list -g "$RG" --query "[0].loginServer" -o tsv 2>/dev/null || echo "")

echo
echo "================= GitHub repository variables ================="
echo "AZURE_CLIENT_ID        = $APP_ID"
echo "AZURE_TENANT_ID        = $TENANT_ID"
echo "AZURE_SUBSCRIPTION_ID  = $SUB_ID"
echo "RESOURCE_GROUP         = $RG"
echo "ACR_LOGIN_SERVER       = $ACR_LOGIN_SERVER"
echo "=============================================================="
echo "(terraform cicd_principal_id, if using --no-roles): $SP_OBJECT_ID"

if [ "$SET_GH_VARS" -eq 1 ]; then
  if command -v gh >/dev/null 2>&1; then
    echo
    echo "Setting GitHub Actions variables via gh ..."
    gh variable set AZURE_CLIENT_ID       --repo "$REPO" --body "$APP_ID"
    gh variable set AZURE_TENANT_ID       --repo "$REPO" --body "$TENANT_ID"
    gh variable set AZURE_SUBSCRIPTION_ID --repo "$REPO" --body "$SUB_ID"
    gh variable set RESOURCE_GROUP        --repo "$REPO" --body "$RG"
    [ -n "$ACR_LOGIN_SERVER" ] && gh variable set ACR_LOGIN_SERVER --repo "$REPO" --body "$ACR_LOGIN_SERVER"
    echo "Done."
  else
    echo "gh CLI not found — set the variables above manually:"
    echo "  GitHub repo → Settings → Secrets and variables → Actions → Variables."
  fi
fi
