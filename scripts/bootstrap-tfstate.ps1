<#
.SYNOPSIS
    One-time bootstrap of the Terraform remote state backend (Azure Storage) for HarmonAIze.

.DESCRIPTION
    Terraform state holds generated secrets (DB/Redis/Storage keys, the Django secret) and must not live
    on a laptop or in git. This script provisions a dedicated, hardened state backend and wires it up:

      * a SEPARATE resource group (never the app RG, which `terraform destroy` is allowed to delete),
      * a StorageV2 account: TLS1.2, no public blob access, HTTPS-only,
      * blob versioning + 30-day soft-delete (state history / recovery),
      * a private `tfstate` container,
      * "Storage Blob Data Contributor" for you (and optionally a CI principal) — then shared-key access
        is DISABLED so Terraform authenticates with Azure AD (`use_azuread_auth`), caching no key locally,
      * a CanNotDelete lock on the state resource group,
      * and it fills in storage_account_name in terraform/backend.tf.

    Idempotent: safe to re-run. Run once per subscription, logged in (`az login`) as Owner, or
    Contributor + User Access Administrator. Afterwards, follow the printed next steps to `terraform init`.

.PARAMETER StorageAccountName
    Override the (globally-unique) state account name. Default: harmonaizetfstate<random6>.

.PARAMETER CicdPrincipalId
    Object id of a CI service principal that runs Terraform (optional). Only needed if you run
    `terraform apply` from CI — this repo's GitHub deploy workflow does NOT, so usually leave blank.

.PARAMETER KeepSharedKey
    Leave storage-account shared-key access enabled. Default is to DISABLE it (AAD-only).

.PARAMETER AllowedIp
    Public IP to allow-list on the state account's firewall. Default: this machine's detected public IP.

.PARAMETER OpenNetwork
    Skip the network firewall and leave the state account's public endpoint open (NOT recommended; use
    only if your deployer/CI IP is dynamic and you accept relying on AAD-only access).

.EXAMPLE
    pwsh ./scripts/bootstrap-tfstate.ps1
.EXAMPLE
    pwsh ./scripts/bootstrap-tfstate.ps1 -StorageAccountName harmonaizetfstate01 -Force
#>
[CmdletBinding()]
param(
    [string]$StateResourceGroup = 'harmonaize-tfstate-rg',
    [string]$Location           = 'southafricanorth',
    [string]$StorageAccountName,
    [string]$ContainerName      = 'tfstate',
    [string]$CicdPrincipalId,
    [string]$SubscriptionId,
    [string]$AllowedIp,
    [switch]$OpenNetwork,
    [switch]$KeepSharedKey,
    [switch]$Force
)

# 'Continue' (not 'Stop'): under Windows PowerShell 5.1 a native command writing to stderr becomes a
# terminating error when EAP=Stop. Real failures are caught explicitly via $LASTEXITCODE checks.
$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

$RepoRoot  = Split-Path $PSScriptRoot -Parent
$BackendTf = Join-Path $RepoRoot 'terraform/backend.tf'

function Write-Phase($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Info($m)  { Write-Host "    $m" -ForegroundColor DarkGray }

function Get-SignedInObjectId($Account) {
    # Resolve the signed-in principal's Azure AD object id (works for a user or a service principal).
    if ($Account.user.type -eq 'servicePrincipal') {
        return (az ad sp show --id $Account.user.name --query id -o tsv 2>$null)
    }
    return (az ad signed-in-user show --query id -o tsv 2>$null)
}

function New-StateName {
    $chars = '0123456789abcdefghijklmnopqrstuvwxyz'
    $rand = -join (1..6 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    return "harmonaizetfstate$rand"
}

function Get-PublicIp {
    foreach ($u in 'https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com') {
        try { return (Invoke-RestMethod -Uri $u -TimeoutSec 15).ToString().Trim() } catch {}
    }
    throw 'Could not determine public IP.'
}

# ---------------------------------------------------------------- preflight --
Write-Phase 'Preflight'
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI (az) not found on PATH. Install: https://learn.microsoft.com/cli/azure/install-azure-cli'
}
$acct = az account show 2>$null | ConvertFrom-Json
if (-not $acct) { throw 'Not logged in. Run: az login' }
if ($SubscriptionId) {
    az account set --subscription $SubscriptionId
    if ($LASTEXITCODE -ne 0) { throw 'az account set failed' }
    $acct = az account show | ConvertFrom-Json
}
Write-Info "Subscription: $($acct.name) ($($acct.id))"
Write-Info "User:         $($acct.user.name)"
if (-not (Test-Path $BackendTf)) { throw "Expected terraform/backend.tf at $BackendTf (it ships with this script)." }

# Resolve / validate the globally-unique account name.
if (-not $StorageAccountName) {
    $attempt = 0
    do {
        $StorageAccountName = New-StateName
        $avail = az storage account check-name --name $StorageAccountName --query nameAvailable -o tsv 2>$null
        $attempt++
    } until ($avail -eq 'true' -or $attempt -ge 10)
    if ($avail -ne 'true') { throw 'Could not find an available storage account name after 10 tries.' }
}
if ($StorageAccountName -notmatch '^[a-z0-9]{3,24}$') {
    throw "Invalid storage account name '$StorageAccountName' (must be 3-24 lowercase alphanumerics)."
}
Write-Info "State account: $StorageAccountName"
Write-Info "State RG:      $StateResourceGroup ($Location)"

if (-not $Force) {
    Write-Host "`nThis creates a BILLABLE storage account + resource group in '$($acct.name)'." -ForegroundColor Yellow
    if ((Read-Host "Type 'yes' to proceed") -ne 'yes') { Write-Host 'Aborted.'; exit 1 }
}

# ---------------------------------------------------------------- resource group
Write-Phase 'Resource group'
az group create --name $StateResourceGroup --location $Location -o none
if ($LASTEXITCODE -ne 0) { throw 'az group create failed' }
Write-Info 'OK.'

# ---------------------------------------------------------------- storage account
Write-Phase 'Storage account'
$saId = az storage account show -n $StorageAccountName -g $StateResourceGroup --query id -o tsv 2>$null
if (-not $saId) {
    az storage account create -n $StorageAccountName -g $StateResourceGroup -l $Location `
        --sku Standard_GRS --kind StorageV2 --min-tls-version TLS1_2 `
        --allow-blob-public-access false --https-only true -o none
    if ($LASTEXITCODE -ne 0) { throw 'az storage account create failed' }
    $saId = az storage account show -n $StorageAccountName -g $StateResourceGroup --query id -o tsv
    Write-Info 'Created.'
} else {
    Write-Info 'Already exists - reusing.'
}

# ---------------------------------------------------------------- data protection
Write-Phase 'Versioning + soft delete'
az storage account blob-service-properties update --account-name $StorageAccountName -g $StateResourceGroup `
    --enable-versioning true --enable-delete-retention true --delete-retention-days 30 `
    --enable-container-delete-retention true --container-delete-retention-days 30 -o none
if ($LASTEXITCODE -ne 0) { throw 'Failed to set blob data-protection properties' }
Write-Info 'Blob versioning + 30-day soft delete enabled.'

# ---------------------------------------------------------------- container
# Create with the account key while shared-key is available (no RBAC-propagation wait). Check via AAD
# first so re-runs (after shared-key is disabled) short-circuit instead of failing.
Write-Phase 'State container'
$has = az storage container exists --name $ContainerName --account-name $StorageAccountName --auth-mode login --query exists -o tsv 2>$null
if ($has -ne 'true') {
    az storage account update -n $StorageAccountName -g $StateResourceGroup --allow-shared-key-access true -o none | Out-Null
    $key = az storage account keys list -g $StateResourceGroup -n $StorageAccountName --query "[0].value" -o tsv
    az storage container create --name $ContainerName --account-name $StorageAccountName --account-key $key -o none
    if ($LASTEXITCODE -ne 0) { throw 'az storage container create failed' }
    Write-Info "Created container '$ContainerName'."
} else {
    Write-Info "Container '$ContainerName' already exists."
}

# ---------------------------------------------------------------- RBAC (data plane)
# Grant the data role explicitly and verify it - a swallowed failure here surfaces later as a confusing
# `terraform init` 403, so fail loudly (you need Owner or User Access Administrator to assign roles).
Write-Phase 'RBAC - Storage Blob Data Contributor'
$me = Get-SignedInObjectId -Account $acct
if (-not $me) {
    throw 'Could not resolve your Azure AD object id (directory read blocked?). Assign "Storage Blob Data Contributor" on the state account before running terraform init.'
}
$pt = if ($acct.user.type -eq 'servicePrincipal') { 'ServicePrincipal' } else { 'User' }
$have = az role assignment list --assignee $me --scope $saId --role 'Storage Blob Data Contributor' --query "[0].id" -o tsv 2>$null
if ($have) {
    Write-Info "Already granted to you ($me)."
} else {
    az role assignment create --assignee-object-id $me --assignee-principal-type $pt `
        --role 'Storage Blob Data Contributor' --scope $saId -o none
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to grant 'Storage Blob Data Contributor' to you on the state account. You need Owner or User Access Administrator to assign roles; terraform init will 403 without this."
    }
    Write-Info "Granted to you ($me)."
}
if ($CicdPrincipalId) {
    $haveCi = az role assignment list --assignee $CicdPrincipalId --scope $saId --role 'Storage Blob Data Contributor' --query "[0].id" -o tsv 2>$null
    if ($haveCi) {
        Write-Info "Already granted to CI principal ($CicdPrincipalId)."
    } else {
        az role assignment create --assignee-object-id $CicdPrincipalId --assignee-principal-type ServicePrincipal `
            --role 'Storage Blob Data Contributor' --scope $saId -o none
        if ($LASTEXITCODE -ne 0) { Write-Host "    WARNING: could not grant the data role to CI principal $CicdPrincipalId (continuing; the deploy itself doesn't need it)." -ForegroundColor Yellow }
        else { Write-Info "Granted to CI principal ($CicdPrincipalId)." }
    }
}

# ---------------------------------------------------------------- harden (AAD-only)
if (-not $KeepSharedKey) {
    Write-Phase 'Disable shared-key access (AAD-only)'
    az storage account update -n $StorageAccountName -g $StateResourceGroup --allow-shared-key-access false -o none
    if ($LASTEXITCODE -ne 0) { Write-Host '    WARNING: could not disable shared-key access; do it manually.' -ForegroundColor Yellow }
    else { Write-Info 'Shared-key access disabled; Terraform uses use_azuread_auth.' }
}

# ---------------------------------------------------------------- network lockdown
# Data access is AAD-gated (shared key off, anon off); this adds the network layer by firewalling the
# public endpoint to the deployer's IP. A true private endpoint needs the app VNet (created later by the
# main apply), so we firewall the public endpoint here; deploy.ps1 re-asserts the current IP on every run.
# The container was already created above (over the still-open endpoint), so this doesn't block bootstrap.
if ($OpenNetwork) {
    Write-Host '    -OpenNetwork: leaving the state account public endpoint open (NOT recommended).' -ForegroundColor Yellow
} else {
    Write-Phase 'Network lockdown'
    if (-not $AllowedIp) {
        try { $AllowedIp = Get-PublicIp } catch { throw "Could not auto-detect your public IP for the state-account firewall. Pass -AllowedIp <ip> or -OpenNetwork. ($_)" }
    }
    az storage account network-rule add -g $StateResourceGroup --account-name $StorageAccountName --ip-address $AllowedIp -o none 2>$null | Out-Null
    az storage account update -n $StorageAccountName -g $StateResourceGroup --default-action Deny --bypass AzureServices -o none
    if ($LASTEXITCODE -ne 0) { Write-Host '    WARNING: could not set default-action Deny; the state account may remain open. Verify manually.' -ForegroundColor Yellow }
    else { Write-Info "Public endpoint firewalled to $AllowedIp (default-action Deny, bypass AzureServices)." }
}

# ---------------------------------------------------------------- delete lock
Write-Phase 'Delete lock'
az lock create --name protect-tfstate --lock-type CanNotDelete --resource-group $StateResourceGroup -o none 2>$null
Write-Info 'CanNotDelete lock on the state resource group.'

# ---------------------------------------------------------------- wire backend.tf
Write-Phase 'Wire up terraform/backend.tf'
$content = Get-Content $BackendTf -Raw
$patched = $content -replace '(storage_account_name\s*=\s*")[^"]*(")', "`${1}$StorageAccountName`${2}"
Set-Content -Path $BackendTf -Value $patched -Encoding UTF8 -NoNewline
Write-Info "storage_account_name = $StorageAccountName"

# ---------------------------------------------------------------- next steps
Write-Phase 'Done - next steps'
Write-Host "State backend ready: $StorageAccountName / $ContainerName (key: harmonaize.prod.tfstate)"
if (-not $OpenNetwork) { Write-Host "Network: public endpoint firewalled to $AllowedIp (deploy.ps1 re-asserts your IP each run)." }
Write-Host ''
Write-Host 'Initialise Terraform against it:' -ForegroundColor Green
Write-Host '    cd terraform'
Write-Host '    Remove-Item terraform.tfstate, terraform.tfstate.backup -ErrorAction SilentlyContinue   # destroyed estate; nothing to migrate'
Write-Host '    terraform init'
Write-Host ''
Write-Host 'A one-off 403 on init means AAD role propagation is still settling - wait ~2-5 min and retry.'
Write-Host 'Commit terraform/backend.tf so the team shares this backend.'
