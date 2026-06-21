# HarmonAIze — Azure Deployment Guide (az CLI)

The definitive runbook for deploying HarmonAIze to Azure with the Terraform in this directory. Follow it
top to bottom. Companion docs:

> Commands are **PowerShell**. `az` / `terraform` / `docker` invocations are identical across shells;
> only variable capture differs. Replace placeholders in `<…>`. Defaults assume `prefix=harmonaize`,
> `environment=prod`, `location=southafricanorth` → resource group `harmonaize-prod-rg`.

---

## What gets deployed

| Tier | Resources |
| :--- | :--- |
| **Core (always)** | Resource group, VNet + subnets + NSGs + private DNS zones, Key Vault (private), PostgreSQL Flexible Server 16 (private, pgvector, optional HA), Azure Managed Redis (private, provisioned via the azapi provider), Storage account + `media` container (private), ACR, Log Analytics, Container Apps environment + **web / worker / beat / flower** apps and a **migrate** job |
| **Public edge (opt-in)** | `deploy_frontdoor = true` → Azure **Front Door Standard + WAF**; flips the Container Apps environment to public and fronts the web app |
| **Federated analysis (opt-in)** | `deploy_analysis_stack = true` → Armadillo / DataSHIELD / Keycloak VM tier in a dedicated `analysis` subnet |

The core stack is **private by default** — until you enable Front Door, the web app is reachable only
inside the VNet.

---

## Order of operations

```
1. Tooling + Azure auth + register providers
2. Remote state backend
3. terraform.tfvars (choose the optional tiers here)
4. init + validate
5. Phase A — create the ACR
6. Phase B — build + push the app image
7. Phase C — choose the private-bootstrap path
8. Phase D — full apply
9. Phase E — run the migration Job
10. Phase F — set real secret values
11. Phase G — expose publicly via Front Door (if deploy_frontdoor)
12. Phase H — verify
13. Phase I — federated-analysis tier (if deploy_analysis_stack)
14. Day-2 operations / Teardown / Troubleshooting
```

---

## 1. Tooling and Azure auth

```powershell
terraform version      # >= 1.5  (init installs azurerm + azapi + tls + random + time)
az version             # 2.60+
docker version         # optional — only for the local-Docker image path (Phase B); az acr build needs no Docker

az login
az account set --subscription "<SUBSCRIPTION_ID>"
```

You need **Owner**, or **Contributor + User Access Administrator**, on the target scope — the `security`
and `compute` modules create RBAC role assignments.

Register the resource providers once per subscription:

```powershell
@(
  'Microsoft.App','Microsoft.ContainerRegistry','Microsoft.DBforPostgreSQL',
  'Microsoft.Cache','Microsoft.CognitiveServices','Microsoft.KeyVault',
  'Microsoft.OperationalInsights','Microsoft.Network','Microsoft.Storage',
  'Microsoft.ManagedIdentity','Microsoft.Cdn','Microsoft.Compute'
) | ForEach-Object { az provider register --namespace $_ }
```

(`Microsoft.Cdn` is for Front Door, `Microsoft.Compute` for the analysis VM, `Microsoft.CognitiveServices`
only if `deploy_azure_openai = true`.)

---

## 2. Remote state backend

State holds generated secrets — keep it off your laptop and out of git.

```powershell
az group create --name harmonaize-tfstate-rg --location southafricanorth
$STATE = "harmonaizetfstate$(Get-Random -Maximum 99999)"
az storage account create --name $STATE --resource-group harmonaize-tfstate-rg --sku Standard_LRS --encryption-services blob
az storage container create --name tfstate --account-name $STATE
$STATE   # note this name
```

Create `terraform/backend.tf`:

```hcl
terraform {
  backend "azurerm" {
    resource_group_name  = "harmonaize-tfstate-rg"
    storage_account_name = "<STATE_ACCOUNT_NAME>"
    container_name       = "tfstate"
    key                  = "harmonaize.prod.tfstate"
  }
}
```

---

## 3. Variables

```powershell
Copy-Item terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Edit `terraform/terraform.tfvars`. Key settings:

```hcl
allowed_hosts = "harmonaize.org"        # your real domain(s); avoid "*"

# Application secrets — leave blank to seed a placeholder and set the real value in Key Vault later
# (Phase F). Generated/infra secrets (DB password, Redis/Storage keys, SECRET_KEY) are automatic.
sendgrid_api_key = ""
openai_api_key   = ""
sentry_dsn       = ""
# google_geocoding_api_key / gemini_api_key / mapbox_access_token — optional

# Public access via Front Door + WAF
deploy_frontdoor        = true                 # set false to stay private (VNet-only)
frontdoor_custom_domain = "harmonaize.org"     # optional; blank = use the *.azurefd.net hostname
# frontdoor_id          = ""                   # leave blank now; set in Phase G (second apply)

# Optional federated-analysis tier
deploy_analysis_stack = false                  # true to deploy the Armadillo/DataSHIELD VM
# analyst_source_cidrs = ["<vpn-or-office-cidr>"]   # required if the above is true
```

`container_app_image` is set in Phase A. `terraform.tfvars` is gitignored — never commit real secrets.

### Cost-optimized profile for a short evaluation run

For a throwaway 2-day evaluation (then `terraform destroy`), the overrides below cut the core cost to
roughly **$8–12 for 48 h** instead of ~$30. **Not for production** — no DB HA, single-node cache,
local-only storage redundancy.

```hcl
# --- Cheap test profile: replace the sizing block in terraform.tfvars ---
enable_postgres_ha = false                 # no standby replica — the single biggest saving (~$10)
db_sku_name        = "B_Standard_B1ms"     # Burstable 1 vCore / 2 GiB (vs GP 2 vCore). Burstable does
db_storage_mb      = 32768                 #   not support HA, so HA must be off (above). 32 GB = minimum.

redis_sku_name = "Balanced_B0"             # smallest Azure Managed Redis (classic Basic/Standard/Enterprise are retired)

acr_sku                  = "Basic"         # ~$5/mo vs ~$20/mo Standard
storage_replication_type = "LRS"           # local redundancy (vs GRS)

deploy_frontdoor      = false              # stay private; reach the app from inside the VNet
deploy_analysis_stack = false              # skip the priciest add-on
```

> For **repeated** test cycles, also set `environment = "dev"` — this turns Key Vault purge protection
> **off**, so `az keyvault purge` works immediately after destroy and names are reusable without the
> 7-day soft-delete wait. It renames resources to `harmonaize-dev-*`; the `$RG` / `terraform output`
> captures in this guide adapt automatically, but substitute `dev` for `prod` in any literal `az`
> examples. Add `deploy_frontdoor = true` (+~$2–3) only if you need to exercise the public/registration path.

---

## 4. Init and validate

```powershell
cd terraform
terraform init
terraform fmt -recursive
terraform validate
```

---

## 5. Phase A — create the ACR

The Container Apps pull an image from an ACR this config creates, so stand up the registry first:

```powershell
terraform apply -target="module.compute.azurerm_container_registry.acr"
$ACR = terraform output -raw acr_login_server
$ACR
```

---

## 6. Phase B — build and push the image

The build context is the `harmonaize` dir. The Front Door origin-lockdown middleware
(`config/middleware.py`, wired in `production.py` with `USE_X_FORWARDED_HOST`) is already in the app, so
the image is Front-Door-ready — no pre-build change needed.

**Recommended — `az acr build` (builds in the cloud; no local Docker):**

```powershell
$REG = $ACR.Split('.')[0]
cd harmonaize    # az acr build resolves --file relative to the build context (cwd)
az acr build --registry $REG --image harmonaize_production_django:latest --file compose/production/django/Dockerfile .
cd ..
```

> **Windows gotcha:** `az acr build`'s live log stream can crash the CLI with a `cp1252`
> `UnicodeEncodeError` (the `--%` / `PYTHONUTF8=1` workarounds don't reliably help). The **build keeps
> running server-side** — do **not** rebuild. Watch it instead:
> `az acr task list-runs -r $REG -o table`, then `az acr task show-run --run-id <id> -r $REG --query status -o tsv`
> until `Succeeded`.

**Alternative — local Docker (from the repo root):**

```powershell
$REG = $ACR.Split('.')[0]
az acr login --name $REG
docker build -f harmonaize/compose/production/django/Dockerfile -t "$ACR/harmonaize_production_django:latest" harmonaize
docker push "$ACR/harmonaize_production_django:latest"
```

Pin the **digest** (not `:latest`) in `terraform/terraform.tfvars` — `:latest` is mutable, so a new
revision may not re-pull a fresh push, and you can accidentally deploy a stale digest:

```powershell
az acr repository show -n $REG --image harmonaize_production_django:latest --query digest -o tsv   # sha256:...
```
```hcl
container_app_image = "<acr_login_server>/harmonaize_production_django@sha256:<digest>"
```

---

## 7. Phase C — choose the private-bootstrap path

The **Key Vault** is created with public network access **off**, so its **secrets** are written over the
data plane — the machine running Terraform needs a network path to the vault. (The Storage account keeps
its public endpoint enabled, so the **`media` container** and analysis-config blobs upload fine from any
runner; only the Key Vault needs special handling.) Pick one:

- **Jumpbox in the VNet (clean / production).** After the VNet exists, run Phases D–F from a small VM in
  the VNet (or a peered network), with `kv_bootstrap_allowed_ip` left blank. Faithful to the private design.
- **Firewalled public access (quick bring-up).** Set `kv_bootstrap_allowed_ip` in `terraform.tfvars` to the
  public IP of the machine running Terraform. This enables the vault's public endpoint but locks it with a
  default-Deny `network_acls` rule to that single IP — no need to hand-edit module source. Find your IP with
  `(Invoke-RestMethod -Uri 'https://api.ipify.org')`. After the deploy settles you may blank it out and
  re-apply to return the vault to fully private (the apps keep working via the private endpoint).

  ```hcl
  kv_bootstrap_allowed_ip = "203.0.113.7"   # your current public IP
  ```

This also applies to Phase F (setting secrets via `az`): do it from the same allowed IP / network.

---

## 8. Phase D — full apply

```powershell
cd terraform
terraform plan -out tfplan
terraform apply tfplan
```

This stands up the core stack and, if enabled, Front Door and the analysis VM. (RBAC propagation is
handled in-config with a 60-second wait, so first-apply secret writes don't race.)

---

## 9. Phase E — run database migrations

Migrations run as a dedicated Job (not on web start-up). Run it after every apply/release:

```powershell
$RG  = terraform output -raw resource_group_name
$JOB = terraform output -raw migrate_job_name
az containerapp job start -n $JOB -g $RG
az containerapp job execution list -n $JOB -g $RG -o table   # wait for Succeeded
```

The Job first creates the pgvector extension (`CREATE EXTENSION IF NOT EXISTS vector`), then runs
`manage.py migrate --noinput && manage.py init_climate_services`. On the very first deploy the web app
errors until this completes — expected. If it ends **Failed**, read the logs from Log Analytics; the
`az containerapp job logs` command needs the `log-analytics` CLI extension, so if that extension is
broken, query `ContainerAppConsoleLogs_CL` directly (e.g. via `az rest` against the workspace).

---

## 10. Phase F — set real secret values

Only the operator-supplied secrets carry a `REPLACE-IN-KEY-VAULT` placeholder when left blank; the
generated ones already hold real values. Set the real values from a network with access to the private KV
(Phase C):

```powershell
$KV = ((terraform output -raw key_vault_uri) -replace 'https://','').Split('.')[0]
az keyvault secret set --vault-name $KV --name sendgrid-api-key --value "SG.xxxx"
az keyvault secret set --vault-name $KV --name openai-api-key   --value "sk-..."
az keyvault secret set --vault-name $KV --name sentry-dsn       --value "https://<key>@<org>.ingest.sentry.io/<id>"
# google-geocoding-api-key / gemini-api-key / mapbox-access-token as needed
```

Terraform ignores later drift on these (`ignore_changes`), so manual rotations stick. Roll a revision so
the apps pick them up:

```powershell
az containerapp update -n harmonaize-prod-web -g $RG --revision-suffix "r$(Get-Date -UFormat %s)"
```

---

## 11. Phase G — expose publicly via Front Door  *(only if `deploy_frontdoor = true`)*

Front Door needs the web app's FQDN as its origin, and the app needs Front Door's ID to lock the origin —
a chicken/egg resolved with a **second apply**:

```powershell
# Front Door + the public env already came up in Phase D. Read its ID and public hostname:
terraform output -raw frontdoor_id                 # GUID = the X-Azure-FDID value
terraform output -raw frontdoor_endpoint_hostname  # harmonaize-prod-web-xxxx.z01.azurefd.net

# Put the GUID in terraform.tfvars, then re-apply so the app receives FRONTDOOR_ID:
#   frontdoor_id = "<the GUID>"
terraform apply
az containerapp update -n harmonaize-prod-web -g $RG --revision-suffix "r$(Get-Date -UFormat %s)"
```

After this, the `FrontDoorIDMiddleware` (already in the image) rejects any request that didn't come
through Front Door, closing the direct-to-origin path.

> **Give it time.** Front Door route/origin changes take several minutes (+ a health-probe cycle) to
> propagate globally — the endpoint can return **404/503** until then (not an error; re-test). With **no
> custom domain**, the Front Door module forwards the Container Apps FQDN as the origin host header (ACA
> ingress 404s any other Host) and the app reads the real public host from `X-Forwarded-Host`; the compute
> module auto-adds `.azurefd.net` to `ALLOWED_HOSTS`. With a **custom domain**, ensure it is in `allowed_hosts`.

**Custom domain** (if `frontdoor_custom_domain` is set): create the DNS records to validate and route —

```powershell
terraform output -raw custom_domain_validation_token   # value for the TXT record
```
- TXT: `_dnsauth.<your-domain>` = the validation token
- CNAME: `<your-domain>` → the `frontdoor_endpoint_hostname` (apex domains: use an ALIAS/ANAME or Azure DNS alias)

Validation + managed-cert issuance can take 10–30+ min.

> **WAF scope:** Standard ships custom rules (this module enforces per-IP rate limiting). The managed
> OWASP/bot rulesets require the **Premium** SKU — see [`modules/frontdoor/README.md`](./modules/frontdoor/README.md).

---

## 12. Phase H — verify

```powershell
az containerapp list -g $RG -o table
az containerapp revision list -n harmonaize-prod-web -g $RG -o table
az containerapp logs show -n harmonaize-prod-web -g $RG --tail 100
```

Expect: web / worker / beat / flower **Running**; web logs show gunicorn on `:5000`; the migrate execution
**Succeeded**.

- **Public (Front Door):** browse `https://<frontdoor_endpoint_hostname>/` (or your custom domain) — the
  registration page loads through the WAF. A direct request to the Container Apps FQDN returns **403**.
- **Private (no Front Door):** the web FQDN is a private VIP — reach it from inside the VNet only.

---

## 13. Phase I — federated-analysis tier  *(only if `deploy_analysis_stack = true`)*

The Armadillo / DataSHIELD / Keycloak VM tier is created by Phase D when the flag is on (it adds the
`analysis` subnet, the VM + data disk, and the Keycloak/Armadillo/Rock secrets in Key Vault). Set
`analyst_source_cidrs` to the IP ranges of the analysts who need to reach Armadillo/Keycloak. Then:

```powershell
az vm start -n harmonaize-prod-analysis-vm -g $RG    # only if deallocated for cost

# The VM brings the stack up automatically from cloud-init. Verify over the control plane
# (no inbound SSH needed):
az vm run-command invoke -g $RG -n harmonaize-prod-analysis-vm --command-id RunShellScript `
  --scripts "cd /app && docker compose -f docker-compose.analysis.yml ps" --query "value[0].message" -o tsv
# Expect: armadillo + rserver Up; keycloak + keycloak-db Up (healthy). Armadillo serves on :8080, Keycloak on :8081.
```

> If cloud-init failed, read `/var/log/cloud-init-output.log` via the same `run-command`. For an
> interactive shell set `deploy_bastion = true` and connect using the key in Key Vault
> (`analysis-vm-ssh-private-key`). After editing any analysis config, re-apply (the blobs now carry
> `content_md5`, so changes re-upload) and recreate the stack. See [`ARMADILLO_PLAN.md`](./ARMADILLO_PLAN.md).

End-to-end check: trigger a project export from the app → confirm the object appears in Armadillo →
have an analyst run a DataSHIELD call. Architecture, the §0.1 decisions, and the backup/deallocate model
are documented in [`ARMADILLO_PLAN.md`](./ARMADILLO_PLAN.md).

---

## 14. Day-2 operations

**Ship a new image version:**
```powershell
az acr build --registry $REG --image harmonaize_production_django:<tag> --file compose/production/django/Dockerfile harmonaize
# pin the new digest in container_app_image (tfvars), then:
terraform apply
az containerapp job start -n "$(terraform output -raw migrate_job_name)" -g $RG   # run migrations
```

**Tune autoscaling / sizing:** change `worker_target_queue_length`, `max_replicas`, `db_sku_name`,
`redis_sku_name` (Azure Managed Redis SKU, e.g. `Balanced_B0`), etc., then `terraform apply`.

**Teardown:**
```powershell
terraform destroy
az keyvault purge --name $KV     # KV soft-delete is 7 days; purge to reuse the name
```
Storage/ACR names carry a random suffix, so re-applies get fresh names automatically.

---

## 15. Troubleshooting

| Symptom | Likely cause | Action |
| :--- | :--- | :--- |
| Apply fails writing KV secrets / `media` container | Private KV/Storage, runner outside the VNet | Phase C (jumpbox or temporary public access) |
| `web_app_fqdn` not reachable from the internet | `deploy_frontdoor = false` → environment is internal | Use Front Door (Phase G), or reach it from inside the VNet |
| Front Door up, but the origin FQDN is still reachable directly | `frontdoor_id` not set (Phase G pass 2 skipped) | Set `frontdoor_id` in tfvars, re-apply, roll a web revision |
| All Front Door traffic returns 403 | `frontdoor_id` mismatch (stale GUID) | Re-read `terraform output -raw frontdoor_id`, update tfvars, re-apply, roll a revision |
| Front Door returns 404/503 right after Phase G | route/origin still propagating | Wait ~10 min; it self-resolves once the health probe passes |
| `az acr build` exits with a `cp1252` `UnicodeEncodeError` | Windows console can't encode the streamed build log | Cosmetic — the build runs server-side; poll `az acr task show-run` (Phase B) |
| migrate Job fails `type "vector" does not exist` | pgvector extension not created | Handled by the Job command + the `VectorExtension()` migration; rerun the Job |
| Registration POST returns CSRF 403 | Public domain not trusted | Ensure your domain is in `allowed_hosts`; `CSRF_TRUSTED_ORIGINS` defaults to `https://harmonaize.org` (override via `DJANGO_CSRF_TRUSTED_ORIGINS`) |
| Custom domain stuck "Pending validation" | DNS not in place | Add the TXT + CNAME records (Phase G); allow time to propagate |
| Analysis VM containers not healthy | cloud-init / image pull / secret fetch | `az vm run-command invoke` → `docker compose ps` + `/var/log/cloud-init-output.log`; see [`ARMADILLO_PLAN.md`](./ARMADILLO_PLAN.md) |
| Keycloak container unhealthy / Armadillo won't start | (regression) Keycloak health on port 9000 or `--http-enabled`/`oidc-permission-enabled` missing | Verify against the committed `docker-compose.analysis.yml` / `application.yml` |
| web returns errors right after first apply | migrations not yet run | Run the migration Job (Phase E) and wait for **Succeeded** |
