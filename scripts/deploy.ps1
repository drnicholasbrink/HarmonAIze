<#
.SYNOPSIS
    One-command deployment of HarmonAIze to Azure - automates DEPLOY_RUNBOOK.md Phases A-E and G-I.
    (Phase F - setting real secret values - stays a manual day-2 step; see the closing message.)

.DESCRIPTION
    Orchestrates the multi-phase bootstrap that a single `terraform apply` cannot do on its own:
      A. create the ACR            (targeted apply)
      B. build + push the image    (az acr build, cloud-side; no local Docker)
      C. Key Vault bootstrap IP    (allow this machine to write KV secrets)
      D. full apply
      E. run DB migrations Job     (+ poll to Succeeded)
      G. Front Door 2nd pass       (inject FRONTDOOR_ID, roll web revision)  [if deploy_frontdoor]
      H. verify                    (Front Door / web endpoint)
      I. verify analysis stack     (az vm run-command)                       [if deploy_analysis_stack]

    Idempotent: safe to re-run. Dynamic values (image digest, bootstrap IP, frontdoor_id) are written
    back into terraform.tfvars so manual day-2 `terraform apply`s stay consistent.

    Optional tiers (Front Door, analysis VM, Bastion, Azure OpenAI) are driven entirely by your
    terraform.tfvars - this script reads the resulting outputs to decide which phases to run.

.PARAMETER Force
    Skip the confirmation prompt (for CI / unattended runs).

.PARAMETER SkipImageBuild
    Reuse the existing :latest image in the ACR instead of rebuilding (Phase B).

.PARAMETER InVnet
    The runner is inside the VNet (or a peered network) and can reach the private Key Vault directly;
    do not set kv_bootstrap_allowed_ip.

.PARAMETER KvBootstrapIp
    Override the auto-detected public IP used for the Key Vault firewall allow-list (Phase C).

.PARAMETER SubscriptionId
    Azure subscription to target (defaults to the current `az account`).

.PARAMETER SkipPermissionCheck
    Skip the Azure RBAC preflight (use when directory reads are blocked or you deploy with a custom role).

.EXAMPLE
    pwsh ./scripts/deploy.ps1
.EXAMPLE
    pwsh ./scripts/deploy.ps1 -Force -SkipImageBuild
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipImageBuild,
    [switch]$InVnet,
    [string]$KvBootstrapIp,
    [string]$SubscriptionId,
    [switch]$SkipPermissionCheck
)

# 'Continue' (not 'Stop'): under Windows PowerShell 5.1 a native command writing to stderr becomes a
# terminating error when EAP=Stop, which kills the script on benign az/terraform stderr. Real failures
# are still caught explicitly (Invoke-Native checks $LASTEXITCODE; phases throw on bad status/exit).
$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

$RepoRoot       = Split-Path $PSScriptRoot -Parent
$TfDir          = Join-Path $RepoRoot 'terraform'
$HarmonaizeDir  = Join-Path $RepoRoot 'harmonaize'
$TfVarsPath     = Join-Path $TfDir 'terraform.tfvars'
$ImageRepo      = 'harmonaize_production_django'
$DockerfileRel  = 'compose/production/django/Dockerfile'

# ---------------------------------------------------------------- helpers ----
function Write-Phase($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Info($msg)  { Write-Host "    $msg" -ForegroundColor DarkGray }

function Invoke-Native {
    # Run a native command and throw on non-zero exit.
    param([Parameter(Mandatory)][scriptblock]$Cmd, [string]$What = 'command')
    & $Cmd
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

function Get-TfOutput([string]$name) {
    Push-Location $TfDir
    try { $v = (terraform output -raw $name 2>$null); if ($LASTEXITCODE -ne 0) { return $null }; return $v }
    finally { Pop-Location }
}

function Set-TfVar([string]$key, [string]$value) {
    # Upsert  key = "value"  in terraform.tfvars (string values only).
    $line = "$key = `"$value`""
    $lines = @(Get-Content $TfVarsPath -ErrorAction SilentlyContinue)
    $found = $false
    $out = foreach ($l in $lines) {
        if ($l -match "^\s*$([regex]::Escape($key))\s*=") { $found = $true; $line } else { $l }
    }
    if (-not $found) { $out = @($out) + $line }
    Set-Content -Path $TfVarsPath -Value $out -Encoding UTF8
    Write-Info "tfvars: $key = $value"
}

function Get-TfVarValue([string]$key) {
    # Read a string var's current value from terraform.tfvars (returns '' if unset/blank/commented).
    $pattern = '^\s*' + [regex]::Escape($key) + '\s*=\s*"([^"]*)"\s*$'
    foreach ($l in @(Get-Content $TfVarsPath -ErrorAction SilentlyContinue)) {
        if ($l -match $pattern) { return $Matches[1] }
    }
    return ''
}

function Get-HttpStatus([string]$url) {
    # HEAD a URL and return the status code; works on both Windows PowerShell 5.1 and pwsh 7.
    try {
        return (Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 40 -ErrorAction Stop).StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return "error: $($_.Exception.Message)"
    }
}

function Get-PublicIp {
    foreach ($u in 'https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com') {
        try { return (Invoke-RestMethod -Uri $u -TimeoutSec 15).ToString().Trim() } catch {}
    }
    throw 'Could not determine public IP. Pass -KvBootstrapIp or -InVnet.'
}

function Get-SignedInObjectId {
    # Resolve the signed-in principal's Azure AD object id (works for a user or a service principal).
    param($Account)
    if ($Account.user.type -eq 'servicePrincipal') {
        return (az ad sp show --id $Account.user.name --query id -o tsv 2>$null)
    }
    return (az ad signed-in-user show --query id -o tsv 2>$null)
}

# ---------------------------------------------------------------- preflight --
Write-Phase 'Preflight - required tooling'

# Azure CLI (present + minimum version)
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI (az) not found on PATH. Install: https://learn.microsoft.com/cli/azure/install-azure-cli'
}
$azVer = (az version -o json 2>$null | ConvertFrom-Json).'azure-cli'
$azMin = '2.50.0'
if (-not $azVer) { throw 'Could not determine the Azure CLI version (az version failed).' }
if ([version]$azVer -lt [version]$azMin) { throw "Azure CLI $azVer is below the required $azMin. Run: az upgrade" }
Write-Info "Azure CLI $azVer (>= $azMin)"

# Terraform (present + minimum version - must satisfy providers.tf required_version >= 1.5.0)
if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    throw 'Terraform not found on PATH. Install: https://developer.hashicorp.com/terraform/install'
}
$tfVer = (terraform version -json 2>$null | ConvertFrom-Json).terraform_version
$tfMin = '1.5.0'
if (-not $tfVer) { throw 'Could not determine the Terraform version.' }
if ([version]$tfVer -lt [version]$tfMin) { throw "Terraform $tfVer is below the required $tfMin. Upgrade: https://developer.hashicorp.com/terraform/install" }
Write-Info "Terraform $tfVer (>= $tfMin)"

$acct = az account show 2>$null | ConvertFrom-Json
if (-not $acct) { throw 'Not logged in. Run: az login' }
if ($SubscriptionId) { Invoke-Native { az account set --subscription $SubscriptionId } 'az account set' }
$acct = az account show | ConvertFrom-Json
Write-Info "Subscription: $($acct.name) ($($acct.id))"
Write-Info "User: $($acct.user.name)"
if (-not (Test-Path $TfVarsPath)) {
    throw "terraform.tfvars not found. Copy terraform.tfvars.example to terraform.tfvars and set your options first."
}

Write-Phase 'Preflight - extensions & permissions'
# Ensure the 'containerapp' az extension is installed (the script uses 'az containerapp job ...').
# Silence dynamic-install prompts, then add/upgrade idempotently. Using 'extension add --upgrade'
# (not 'extension show') avoids az erroring to stderr when the extension is absent.
az config set extension.use_dynamic_install=yes_without_prompt --only-show-errors 2>$null | Out-Null
az extension add --name containerapp --upgrade --only-show-errors 2>$null | Out-Null
# Verify the extension actually LOADS. On a 32-bit Azure CLI the install fails (its 'cryptography'
# dependency has no 32-bit wheel, so pip builds from source and fails), and an old-enough build to
# install is incompatible with recent CLI. Fail fast here with guidance instead of dying in Phase E.
cmd /c "az containerapp -h >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
    throw "'az containerapp' is not working (exit $LASTEXITCODE). This is almost always a 32-bit Azure CLI: the containerapp extension's 'cryptography' dependency has no 32-bit wheel. Install the 64-bit Azure CLI (https://aka.ms/installazurecliwindows), then re-run. Verify with: az containerapp -h"
}
Write-Info 'az extension "containerapp": loaded OK.'

# The signed-in principal needs BOTH: create resources (Owner/Contributor) AND create role assignments
# (Owner / User Access Administrator), because the Terraform provisions azurerm_role_assignment resources
# (ACR pull, Key Vault Secrets User). Plain Contributor cannot do the latter, so Phase D would fail partway.
# RBAC detection has false negatives (custom roles, PIM-eligible, group nesting), so a gap warns loudly
# rather than hard-stopping; bypass the whole check with -SkipPermissionCheck.
$permIssue = $false
if ($SkipPermissionCheck) {
    Write-Info 'Permission check skipped (-SkipPermissionCheck).'
} else {
    $principalId = Get-SignedInObjectId -Account $acct
    if (-not $principalId) {
        $permIssue = $true
        Write-Host '    WARNING: could not resolve your Azure AD object id (directory read blocked?).' -ForegroundColor Yellow
        Write-Host '             Ensure you have Owner, or Contributor + User Access Administrator, on the subscription.' -ForegroundColor Yellow
    } else {
        $roleList = @(az role assignment list --assignee $principalId --scope "/subscriptions/$($acct.id)" --include-inherited --include-groups --query "[].roleDefinitionName" -o tsv 2>$null | Where-Object { $_ })
        $roleDisplay = if ($roleList) { [string]::Join(', ', $roleList) } else { '(none found)' }
        Write-Info "Roles on subscription: $roleDisplay"
        $canCreate = @($roleList | Where-Object { $_ -in 'Owner', 'Contributor' }).Count -gt 0
        $canAssign = @($roleList | Where-Object { $_ -in 'Owner', 'User Access Administrator', 'Role Based Access Control Administrator' }).Count -gt 0
        if ($canCreate -and $canAssign) {
            Write-Info 'RBAC OK: can create resources and assign roles.'
        } else {
            $permIssue = $true
            if (-not $canCreate) { Write-Host '    WARNING: no Owner/Contributor on the subscription - Phase D resource creation will likely fail.' -ForegroundColor Yellow }
            if (-not $canAssign) {
                Write-Host '    WARNING: cannot create role assignments (need Owner or User Access Administrator).' -ForegroundColor Yellow
                Write-Host '             Terraform assigns AcrPull + Key Vault Secrets User; with only Contributor, Phase D fails on those.' -ForegroundColor Yellow
            }
        }
    }
}

Write-Phase 'Register resource providers (idempotent)'
$providers = @('Microsoft.App','Microsoft.ContainerRegistry','Microsoft.DBforPostgreSQL','Microsoft.Cache',
    'Microsoft.KeyVault','Microsoft.OperationalInsights','Microsoft.Network','Microsoft.Storage',
    'Microsoft.ManagedIdentity','Microsoft.Cdn','Microsoft.Compute','Microsoft.CognitiveServices')
foreach ($p in $providers) { az provider register --namespace $p --only-show-errors | Out-Null }
# Wait for registration to finish - a provider still "Registering" when Phase D applies fails the apply.
$regDeadline = (Get-Date).AddMinutes(10)
foreach ($p in $providers) {
    $state = $null
    do {
        $state = az provider show --namespace $p --query registrationState -o tsv 2>$null
        if ($state -eq 'Registered') { break }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $regDeadline)
    if ($state -ne 'Registered') { Write-Info "WARNING: provider $p is '$state' (not Registered) after 10 min; Phase D may fail" }
}
Write-Info "Providers registered: $($providers.Count)"

if (-not $Force) {
    Write-Host "`nThis creates BILLABLE Azure resources in subscription '$($acct.name)'." -ForegroundColor Yellow
    if ($permIssue) { Write-Host "Heads-up: a permissions gap was flagged above - Phase D may fail partway. Continue only if you're sure." -ForegroundColor Yellow }
    if ((Read-Host "Type 'yes' to proceed") -ne 'yes') { Write-Host 'Aborted.'; exit 1 }
}

Push-Location $TfDir
try {
    # ----------------------------------------------------------- init -------
    Write-Phase 'terraform init'
    Invoke-Native { terraform init -input=false } 'terraform init'

    # ----------------------------------------------------------- Phase A ----
    Write-Phase 'Phase A - create the ACR'
    Invoke-Native { terraform apply -target='module.compute.azurerm_container_registry.acr' -auto-approve -input=false } 'Phase A apply'
    $acrLoginServer = Get-TfOutput 'acr_login_server'
    if (-not $acrLoginServer) { throw 'acr_login_server output is empty after Phase A' }
    $reg = $acrLoginServer.Split('.')[0]
    Write-Info "ACR: $acrLoginServer"

    # ----------------------------------------------------------- Phase B ----
    if ($SkipImageBuild) {
        Write-Phase 'Phase B - SKIPPED (using existing :latest)'
    } else {
        Write-Phase 'Phase B - build + push image (az acr build)'
        Push-Location $HarmonaizeDir
        try {
            az acr build --registry $reg --image "${ImageRepo}:latest" --file $DockerfileRel . 2>&1 | Out-Host
            $buildExit = $LASTEXITCODE
        } finally { Pop-Location }
        if ($buildExit -ne 0) {
            Write-Info "az acr build client exited $buildExit (often the Windows cp1252 log-stream crash; the build runs server-side). Polling the run..."
            # The build was just triggered, so the most recent run is this one.
            $runId = az acr task list-runs --registry $reg --top 1 --query "[0].runId" -o tsv
            if (-not $runId) { throw "az acr build failed (exit $buildExit) and no ACR run was found to poll" }
            do {
                Start-Sleep -Seconds 15
                $status = az acr task show-run --registry $reg --run-id $runId --query status -o tsv
                Write-Info "ACR run $runId : $status"
            } while ($status -notin 'Succeeded','Failed','Canceled','Error','Timeout')
            if ($status -ne 'Succeeded') { throw "ACR build $runId ended: $status" }
        }
    }
    $digest = az acr repository show --name $reg --image "${ImageRepo}:latest" --query digest -o tsv
    if (-not $digest) { throw "Could not read image digest for ${ImageRepo}:latest (was it built?)" }
    Set-TfVar 'container_app_image' "$acrLoginServer/${ImageRepo}@$digest"
    Write-Info "Image: $acrLoginServer/${ImageRepo}@$digest"

    # ----------------------------------------------------------- Phase C ----
    Write-Phase 'Phase C - Key Vault bootstrap access'
    if ($InVnet) {
        Set-TfVar 'kv_bootstrap_allowed_ip' ''
        Write-Info 'In-VNet runner: KV stays fully private.'
    } else {
        $ip = if ($KvBootstrapIp) { $KvBootstrapIp } else { Get-PublicIp }
        Set-TfVar 'kv_bootstrap_allowed_ip' $ip
        Write-Info "Deployer IP allow-listed on the Key Vault: $ip"
        # If the KV already exists (re-run) with a different allowed IP, open the firewall now so the
        # data-plane secret writes in this apply don't 403 before Terraform reconciles the network ACL.
        $kvHost = (Get-TfOutput 'key_vault_uri')
        if ($kvHost) {
            $kvName = ($kvHost -replace 'https://','').Split('.')[0]
            az keyvault network-rule add --name $kvName --ip-address $ip --only-show-errors 2>$null | Out-Null
        }
    }

    # Avoid carrying a (possibly wrong) origin-lockdown id into Phase D. On a first run or after a prior
    # deployment was destroyed, the frontdoor_id in tfvars is stale and must be cleared so Phase D deploys
    # the app WITHOUT lockdown; Phase G injects the correct id afterward. But on a re-run where the live
    # Front Door already matches tfvars, KEEP it - clearing would briefly drop origin lockdown (app
    # publicly reachable with no X-Azure-FDID check) between Phase D and Phase G for no reason.
    $liveFdId   = Get-TfOutput 'frontdoor_id'
    $tfvarsFdId = Get-TfVarValue 'frontdoor_id'
    if ($liveFdId -and $liveFdId -ne 'null' -and $liveFdId -eq $tfvarsFdId) {
        Write-Info "frontdoor_id already matches the live deployment ($liveFdId); keeping lockdown through Phase D."
    } else {
        Set-TfVar 'frontdoor_id' ''
        Write-Info 'Cleared stale/empty frontdoor_id; Phase G will inject the correct one.'
    }

    # ----------------------------------------------------------- Phase D ----
    Write-Phase 'Phase D - full apply'
    Invoke-Native { terraform apply -auto-approve -input=false } 'Phase D apply'

    $rg = Get-TfOutput 'resource_group_name'

    # ----------------------------------------------------------- Phase E ----
    Write-Phase 'Phase E - database migrations Job'
    $job = Get-TfOutput 'migrate_job_name'
    if (-not $job) { throw 'migrate_job_name output is empty - Phase D may not have created the migration Job' }
    $exec = az containerapp job start -n $job -g $rg --query name -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $exec) { throw "Failed to start migration Job '$job' (az exit $LASTEXITCODE)" }
    Write-Info "Started migration execution: $exec"
    $deadline = (Get-Date).AddMinutes(30)
    $status = $null
    do {
        Start-Sleep -Seconds 12
        $status = az containerapp job execution show -n $job -g $rg --job-execution-name $exec --query 'properties.status' -o tsv 2>$null
        if ($status) { Write-Info "migration: $status" } else { Write-Info 'migration: (status not available yet)' }
        if ($status -eq 'Succeeded') { break }
        if ($status -in 'Failed','Degraded','Cancelled') { throw "Migration job ended: $status (check ContainerAppConsoleLogs_CL)" }
    } while ((Get-Date) -lt $deadline)
    if ($status -ne 'Succeeded') { throw 'Migration job timed out after 30 min' }

    # ----------------------------------------------------------- Phase G ----
    $fdId = Get-TfOutput 'frontdoor_id'
    if ($fdId -and $fdId -ne 'null') {
        Write-Phase 'Phase G - Front Door origin lockdown (2nd pass)'
        Set-TfVar 'frontdoor_id' $fdId
        Invoke-Native { terraform apply -auto-approve -input=false } 'Phase G apply'
        $appPrefix = $rg -replace '-rg$',''   # e.g. harmonaize-prod
        $suffix = "r$(Get-Date -Format 'yyyyMMddHHmmss')"
        foreach ($a in 'web','worker','beat','flower') {
            az containerapp update -n "$appPrefix-$a" -g $rg --revision-suffix $suffix --only-show-errors 2>$null | Out-Null
        }
        Write-Info "FRONTDOOR_ID injected and apps rolled: $fdId"
    } else {
        Write-Phase 'Phase G - SKIPPED (deploy_frontdoor not enabled)'
    }

    # ----------------------------------------------------------- Phase H ----
    Write-Phase 'Phase H - verify web'
    $fdHost = Get-TfOutput 'frontdoor_endpoint_hostname'
    $webFqdn = Get-TfOutput 'web_app_fqdn'
    if ($fdHost -and $fdHost -ne 'null') {
        $code = Get-HttpStatus "https://$fdHost/"
        Write-Info "Front Door https://$fdHost/ -> $code  (may be 404/503 for a few min while it propagates)"
    } else {
        Write-Info "Private deployment (no Front Door). Web FQDN (VNet-only): $webFqdn"
    }

    # ----------------------------------------------------------- Phase I ----
    $vm = Get-TfOutput 'analysis_vm_name'
    if ($vm -and $vm -ne 'null') {
        Write-Phase 'Phase I - verify analysis stack'
        Write-Info 'cloud-init brings the stack up over several minutes; polling via run-command (empty output is expected on a fresh deploy)...'
        $ps = ''
        for ($i = 1; $i -le 5; $i++) {
            $ps = az vm run-command invoke -g $rg -n $vm --command-id RunShellScript `
                --scripts "cd /app 2>/dev/null && docker compose -f docker-compose.analysis.yml ps 2>&1 | tail -6" `
                --query "value[0].message" -o tsv 2>$null
            if ($ps -match 'armadillo') { break }
            Write-Info "analysis stack not ready yet (attempt $i/5); waiting 30s..."
            Start-Sleep -Seconds 30
        }
        Write-Host $ps
        if ($ps -notmatch 'armadillo') {
            Write-Info 'Analysis stack not confirmed up yet - recheck later per ARMADILLO_PLAN.md section 1.1 (not a deploy failure).'
        }
    }

    # ----------------------------------------------------------- summary ----
    Write-Phase 'Done'
    Write-Host "Resource group : $rg"
    if ($fdHost -and $fdHost -ne 'null') { Write-Host "Frontend URL   : https://$fdHost/" -ForegroundColor Green }
    else { Write-Host "Web FQDN       : $webFqdn (VNet-only)" }
    Write-Host "Key Vault      : $(Get-TfOutput 'key_vault_uri')"
    Write-Host "`nDay-2: set real secrets (sendgrid/openai/sentry) with 'az keyvault secret set' and roll a revision (runbook Phase F)."
    Write-Host "Teardown: pwsh ./scripts/destroy.ps1"
}
finally { Pop-Location }
