<#
.SYNOPSIS
    Tear down the HarmonAIze stack (terraform destroy) — companion to deploy.ps1.

.DESCRIPTION
    Adds this machine's public IP to the (private) Key Vault firewall first, so Terraform can delete
    the KV secrets over the data plane without a 403, then runs `terraform destroy`.

    The Key Vault is left SOFT-DELETED when purge protection is on (prod): it can't be force-purged and
    auto-purges after the retention window. The random name suffix means this never blocks a re-deploy.

.PARAMETER Force
    Skip the confirmation prompt.

.PARAMETER SubscriptionId
    Azure subscription to target (defaults to the current `az account`).

.EXAMPLE
    pwsh ./scripts/destroy.ps1
#>
[CmdletBinding()]
param([switch]$Force, [string]$SubscriptionId)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path $PSScriptRoot -Parent
$TfDir    = Join-Path $RepoRoot 'terraform'

function Get-PublicIp {
    foreach ($u in 'https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com') {
        try { return (Invoke-RestMethod -Uri $u -TimeoutSec 15).ToString().Trim() } catch {}
    }
    return $null
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'az not found on PATH' }
if (-not (az account show 2>$null)) { throw 'Not logged in. Run: az login' }
if ($SubscriptionId) { az account set --subscription $SubscriptionId; if ($LASTEXITCODE) { throw 'az account set failed' } }

Push-Location $TfDir
try {
    $rg = terraform output -raw resource_group_name 2>$null
    if (-not $rg) { Write-Host 'No Terraform state / nothing to destroy.'; exit 0 }

    if (-not $Force) {
        Write-Host "This will DESTROY resource group '$rg' and everything Terraform manages in it." -ForegroundColor Red
        if ((Read-Host "Type 'destroy' to proceed") -ne 'destroy') { Write-Host 'Aborted.'; exit 1 }
    }

    # Allow this machine to reach the Key Vault data plane so secret deletes succeed.
    $kvUri = terraform output -raw key_vault_uri 2>$null
    if ($kvUri) {
        $kvName = ($kvUri -replace 'https://','').Split('.')[0]
        $ip = Get-PublicIp
        if ($ip) {
            Write-Host "Allow-listing $ip on Key Vault $kvName for secret deletion..."
            az keyvault network-rule add --name $kvName --ip-address $ip --only-show-errors 2>$null | Out-Null
        }
    }

    Write-Host "Running terraform destroy (this can take 20-40 min)..." -ForegroundColor Yellow
    terraform destroy -auto-approve -input=false
    if ($LASTEXITCODE -ne 0) { throw "terraform destroy failed (exit $LASTEXITCODE)" }

    Write-Host "`nDone. Resource group '$rg' destroyed." -ForegroundColor Green
    Write-Host "Note: the Key Vault is soft-deleted (purge protection). It auto-purges after its retention window;"
    Write-Host "      re-deploys are unaffected (the vault name carries a random suffix)."
}
finally { Pop-Location }
