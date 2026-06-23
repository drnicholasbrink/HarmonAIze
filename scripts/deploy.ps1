<#
.SYNOPSIS
    One-command deployment of HarmonAIze to Azure — automates DEPLOY_RUNBOOK.md Phases A-I.

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
    terraform.tfvars — this script reads the resulting outputs to decide which phases to run.

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
    [string]$SubscriptionId
)

$ErrorActionPreference = 'Stop'
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

# ---------------------------------------------------------------- preflight --
Write-Phase 'Preflight'
foreach ($t in 'az', 'terraform') {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { throw "$t not found on PATH" }
}
$acct = az account show 2>$null | ConvertFrom-Json
if (-not $acct) { throw 'Not logged in. Run: az login' }
if ($SubscriptionId) { Invoke-Native { az account set --subscription $SubscriptionId } 'az account set' }
$acct = az account show | ConvertFrom-Json
Write-Info "Subscription: $($acct.name) ($($acct.id))"
Write-Info "User: $($acct.user.name)"
if (-not (Test-Path $TfVarsPath)) {
    throw "terraform.tfvars not found. Copy terraform.tfvars.example to terraform.tfvars and set your options first."
}

Write-Phase 'Register resource providers (idempotent)'
$providers = @('Microsoft.App','Microsoft.ContainerRegistry','Microsoft.DBforPostgreSQL','Microsoft.Cache',
    'Microsoft.KeyVault','Microsoft.OperationalInsights','Microsoft.Network','Microsoft.Storage',
    'Microsoft.ManagedIdentity','Microsoft.Cdn','Microsoft.Compute','Microsoft.CognitiveServices')
foreach ($p in $providers) { az provider register --namespace $p --only-show-errors | Out-Null }
Write-Info "Registered/registering: $($providers.Count) providers"

if (-not $Force) {
    Write-Host "`nThis creates BILLABLE Azure resources in subscription '$($acct.name)'." -ForegroundColor Yellow
    if ((Read-Host "Type 'yes' to proceed") -ne 'yes') { Write-Host 'Aborted.'; exit 1 }
}

Push-Location $TfDir
try {
    # ----------------------------------------------------------- init -------
    Write-Phase 'terraform init'
    Invoke-Native { terraform init -input=false } 'terraform init'

    # ----------------------------------------------------------- Phase A ----
    Write-Phase 'Phase A — create the ACR'
    Invoke-Native { terraform apply -target='module.compute.azurerm_container_registry.acr' -auto-approve -input=false } 'Phase A apply'
    $acrLoginServer = Get-TfOutput 'acr_login_server'
    if (-not $acrLoginServer) { throw 'acr_login_server output is empty after Phase A' }
    $reg = $acrLoginServer.Split('.')[0]
    Write-Info "ACR: $acrLoginServer"

    # ----------------------------------------------------------- Phase B ----
    if ($SkipImageBuild) {
        Write-Phase 'Phase B — SKIPPED (using existing :latest)'
    } else {
        Write-Phase 'Phase B — build + push image (az acr build)'
        Push-Location $HarmonaizeDir
        try {
            az acr build --registry $reg --image "${ImageRepo}:latest" --file $DockerfileRel . 2>&1 | Out-Host
            $buildExit = $LASTEXITCODE
        } finally { Pop-Location }
        if ($buildExit -ne 0) {
            Write-Info "az acr build client exited $buildExit (often the Windows cp1252 log-stream crash; the build runs server-side). Polling the run..."
            $runId = az acr task list-runs --registry $reg --top 1 --query "[0].runId" -o tsv
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
    Write-Phase 'Phase C — Key Vault bootstrap access'
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

    # Clear any stale Front Door ID so Phase D deploys the app WITHOUT a (possibly wrong) lockdown id;
    # the correct id is injected in Phase G from this deployment's own Front Door.
    Set-TfVar 'frontdoor_id' ''

    # ----------------------------------------------------------- Phase D ----
    Write-Phase 'Phase D — full apply'
    Invoke-Native { terraform apply -auto-approve -input=false } 'Phase D apply'

    $rg = Get-TfOutput 'resource_group_name'

    # ----------------------------------------------------------- Phase E ----
    Write-Phase 'Phase E — database migrations Job'
    $job = Get-TfOutput 'migrate_job_name'
    $exec = az containerapp job start -n $job -g $rg --query name -o tsv
    Write-Info "Started migration execution: $exec"
    $deadline = (Get-Date).AddMinutes(30)
    do {
        Start-Sleep -Seconds 12
        $status = az containerapp job execution show -n $job -g $rg --job-execution-name $exec --query 'properties.status' -o tsv 2>$null
        Write-Info "migration: $status"
        if ($status -eq 'Succeeded') { break }
        if ($status -in 'Failed','Degraded','Cancelled') { throw "Migration job ended: $status (check ContainerAppConsoleLogs_CL)" }
    } while ((Get-Date) -lt $deadline)
    if ($status -ne 'Succeeded') { throw 'Migration job timed out after 30 min' }

    # ----------------------------------------------------------- Phase G ----
    $fdId = Get-TfOutput 'frontdoor_id'
    if ($fdId -and $fdId -ne 'null') {
        Write-Phase 'Phase G — Front Door origin lockdown (2nd pass)'
        Set-TfVar 'frontdoor_id' $fdId
        Invoke-Native { terraform apply -auto-approve -input=false } 'Phase G apply'
        $appPrefix = $rg -replace '-rg$',''   # e.g. harmonaize-prod
        $suffix = "r$(Get-Date -Format 'yyyyMMddHHmmss')"
        foreach ($a in 'web','worker','beat','flower') {
            az containerapp update -n "$appPrefix-$a" -g $rg --revision-suffix $suffix --only-show-errors 2>$null | Out-Null
        }
        Write-Info "FRONTDOOR_ID injected and apps rolled: $fdId"
    } else {
        Write-Phase 'Phase G — SKIPPED (deploy_frontdoor not enabled)'
    }

    # ----------------------------------------------------------- Phase H ----
    Write-Phase 'Phase H — verify web'
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
        Write-Phase 'Phase I — verify analysis stack'
        Write-Info 'cloud-init brings the stack up; checking via run-command (allow a few min after first apply)...'
        $ps = az vm run-command invoke -g $rg -n $vm --command-id RunShellScript `
            --scripts "cd /app && docker compose -f docker-compose.analysis.yml ps 2>&1 | tail -6" `
            --query "value[0].message" -o tsv 2>$null
        Write-Host $ps
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
