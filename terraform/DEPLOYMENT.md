# HarmonAIze — Azure Deployment Walkthrough

This is the step-by-step runbook for deploying HarmonAIze to Azure with the Terraform in
this directory. For the *why* (architecture rationale, ADRs, cost phasing), see
[`../AZURE_BLUEPRINT.md`](../AZURE_BLUEPRINT.md). This document is the *how*.

> **Status:** `terraform validate` passes (Terraform 1.x, azurerm ~> 3.100). The config has
> **not** yet been applied to a live subscription. Read §6 (Known gaps) before your first apply —
> two of them affect whether the very first `apply` succeeds.

---

## 1. What gets deployed

One resource group (`harmonaize-prod-rg` by default) in **South Africa North**, containing:

| Module | Azure resources |
| :--- | :--- |
| `networking` | VNet `10.0.0.0/16`; subnets for Container Apps (`/23`, delegated), database (delegated), private endpoints, plus legacy aks/appgw subnets; NSGs; Private DNS zones for Postgres, Key Vault, Redis, Blob, File, OpenAI |
| `security` | Key Vault (RBAC, private endpoint, public access off) holding `postgres-password`, `django-secret-key`, and the 4 app secrets |
| `database` | PostgreSQL Flexible Server 16 (private, `pgvector` enabled, optional Zone-Redundant HA) + `harmonaize` database |
| `cache` | Azure Cache for Redis (private endpoint, TLS-only) |
| `storage` | Storage account + `media` blob container (private endpoint, public access off) |
| `cognitive` | **Optional** (`deploy_azure_openai = true`) Azure OpenAI in **Sweden Central** + model deployments. **Off by default** — the app uses the public OpenAI SDK against `openai_base_url` (OpenAI or any 3rd-party OpenAI-compatible endpoint). |
| `compute` | ACR, user-assigned managed identity, Log Analytics, Container Apps environment (VNet-injected), and 4 Container Apps: **web**, **worker** (KEDA Redis scaler, scale-to-zero), **beat**, **flower** |

Data flow and component reasoning: see the blueprint. Quick mental model: **Front Door → web
(ACA) → Postgres/Redis/Blob (all private)**, with **worker** pulled by Redis queue depth and
**Azure OpenAI** reached over Private Link.

---

## 2. Prerequisites

Install locally (or on your deploy runner):

- **Terraform** ≥ 1.5 — `terraform version`
- **Azure CLI** — `az version`
- **Docker** — to build and push the application image
- An **Azure subscription** and an identity with rights to create resources **and role
  assignments**. Creating the RBAC grants in the `security`/`compute` modules requires
  **`Owner`** or (`Contributor` **+** `User Access Administrator`) on the target scope.

Register the resource providers once per subscription:

```bash
az login
az account set --subscription "<SUBSCRIPTION_ID>"
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.DBforPostgreSQL
az provider register --namespace Microsoft.Cache
az provider register --namespace Microsoft.CognitiveServices
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.Network
az provider register --namespace Microsoft.Storage
```

> **Azure OpenAI is off by default** (`deploy_azure_openai = false`) — the app talks to OpenAI or a
> 3rd-party OpenAI-compatible endpoint via the public SDK, so `Microsoft.CognitiveServices` and an
> approved Azure OpenAI subscription are only needed if you explicitly set `deploy_azure_openai = true`.

---

## 3. One-time setup

### 3.1 Remote state (recommended)

Terraform state contains generated secrets (DB password, access keys). Keep it off local disk and
out of git. Create a backend storage account:

```bash
az group create --name harmonaize-tfstate-rg --location southafricanorth
az storage account create --name harmonaizetfstate$RANDOM --resource-group harmonaize-tfstate-rg --sku Standard_LRS --encryption-services blob
# note the chosen name, then:
az storage container create --name tfstate --account-name <STATE_ACCOUNT_NAME>
```

Create `backend.tf` in this directory:

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

(If you skip this, Terraform uses a local `terraform.tfstate` — fine for a trial, **not** for a
team or production.)

### 3.2 Variables

Copy the example and fill it in:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Set the application secrets (or leave blank and set them in Key Vault later — see §5 for the full
list and §6.3 for the commands) and any non-default sizing. **`terraform.tfvars` is gitignored —
never commit real secret values.**

---

## 4. Deploy

> Because the Container Apps reference an image in the ACR that this same Terraform creates, do a
> **two-phase apply**: stand up the registry, push the image, then apply the rest. Otherwise the
> first app revisions come up unhealthy (nothing to pull).

### Phase 0 — init & sanity

```bash
terraform init          # add -reconfigure if you just added backend.tf
terraform fmt -recursive
terraform validate
```

### Phase 1 — create the registry first

```bash
terraform apply -target=module.compute.azurerm_container_registry.acr
ACR_LOGIN_SERVER=$(terraform output -raw acr_login_server)
echo "$ACR_LOGIN_SERVER"
```

### Phase 2 — build & push the application image

The image is built from `harmonaize/compose/production/django/Dockerfile` (gunicorn on `:5000`).

```bash
az acr login --name "${ACR_LOGIN_SERVER%%.*}"

# build from the harmonaize app directory (the Dockerfile context)
docker build -f harmonaize/compose/production/django/Dockerfile -t "$ACR_LOGIN_SERVER/harmonaize_production_django:latest" harmonaize

docker push "$ACR_LOGIN_SERVER/harmonaize_production_django:latest"
```

Set `container_app_image` in `terraform.tfvars` to the pushed reference:

```hcl
container_app_image = "<acr_login_server>/harmonaize_production_django:latest"
```

### Phase 3 — apply everything

```bash
terraform plan -out tfplan
terraform apply tfplan
```

`terraform apply` will, among other things, run the container's `/start` script on the **web**
app, which executes `collectstatic` + `migrate` + `init_climate_services` before launching
gunicorn — so the database schema is created automatically on first boot.

---

## 5. Key Vault secrets

Every application secret lives in **Azure Key Vault** and is consumed by the Container Apps through
the app's **user-assigned managed identity** (role *Key Vault Secrets User*) as a versionless secret
reference. No secret value is ever placed in a plain Container App env var, in Terraform state as a
container input, or in git. The app reaches Key Vault over a **private endpoint**.

### 5.1 Auto-managed (generated by Terraform — you do **not** set these)

| Key Vault secret | App env var | Source |
| :--- | :--- | :--- |
| `postgres-password` | — | `random_password` (24 chars) |
| `django-secret-key` | `DJANGO_SECRET_KEY` | `random_password` (50 chars) |
| `database-url` | `DATABASE_URL` | composed `postgres://…@…:5432/…?sslmode=require` |
| `redis-url` | `REDIS_URL`, `CELERY_BROKER_URL` | composed `rediss://:<key>@…:6380/0` |
| `redis-password` | — (KEDA scaler auth) | Redis primary access key |
| `storage-account-key` | `DJANGO_AZURE_ACCOUNT_KEY` | Storage account primary key |
| `analysis-deidentification-salt` | `ANALYSIS_DEIDENTIFICATION_SALT` | auto-generated random (override with `analysis_deidentification_salt`) |

### 5.2 You supply (via `terraform.tfvars` on first apply, or set in Key Vault later)

| Key Vault secret | App env var | tfvars variable | Notes |
| :--- | :--- | :--- | :--- |
| `django-admin-url` | `DJANGO_ADMIN_URL` | `django_admin_url` | Admin path; recommended |
| `sendgrid-api-key` | `SENDGRID_API_KEY` | `sendgrid_api_key` | Required for outbound email |
| `sentry-dsn` | `SENTRY_DSN` | `sentry_dsn` | Optional — blank disables Sentry |
| `openai-api-key` | `OPENAI_API_KEY` | `openai_api_key` | Embeddings; any OpenAI-compatible provider |
| `google-geocoding-api-key` | `GOOGLE_GEOCODING_API_KEY` | `google_geocoding_api_key` | Optional (geolocation) |
| `gemini-api-key` | `GEMINI_API_KEY` | `gemini_api_key` | Optional (LLM geolocation) |
| `mapbox-access-token` | `MAPBOX_ACCESS_TOKEN` | `mapbox_access_token` | Optional (geolocation) |

Externally-supplied secrets carry `lifecycle { ignore_changes = [value] }`, so you may rotate them
directly in Key Vault and Terraform will not revert them.

### 5.3 Secrets NOT wired automatically (handle manually only if used)

- **`GOOGLE_APPLICATION_CREDENTIALS`** — Google Earth Engine needs a *service-account JSON file*, not
  a string value. Only required for GEE climate ingestion. Store the JSON as a Key Vault secret and
  mount it as a file via a Container App secret volume, then point `GOOGLE_APPLICATION_CREDENTIALS`
  at the mounted path.
- **`ANALYSIS_ARMADILLO_PASSWORD`, `ANALYSIS_KEYCLOAK_ADMIN_PASSWORD`** — only for the optional,
  separately-deployed federated-analysis stack (Armadillo / DataSHIELD / Keycloak), which is out of
  scope for this Terraform (blueprint ADR-3). Their in-code defaults are `admin`; never ship those.

---

## 6. Post-deploy

### 6.1 Read the outputs

```bash
terraform output
# web_app_fqdn, postgres_fqdn, redis_hostname, key_vault_uri, openai_endpoint,
# storage_account_name, acr_login_server, container_app_environment_id
```

### 6.2 Verify the apps

```bash
az containerapp list -g harmonaize-prod-rg -o table
az containerapp revision list -n harmonaize-prod-web -g harmonaize-prod-rg -o table
az containerapp logs show -n harmonaize-prod-web -g harmonaize-prod-rg --tail 100
```

### 6.3 Set real secret values (if you seeded them blank)

Only the §5.2 "you supply" secrets — the §5.1 auto-managed ones already hold real values.

```bash
KV=$(terraform output -raw key_vault_uri | sed 's#https://##;s#/##')
az keyvault secret set --vault-name "$KV" --name django-admin-url         --value "manage-<random>/"
az keyvault secret set --vault-name "$KV" --name sendgrid-api-key         --value "SG.xxxx"
az keyvault secret set --vault-name "$KV" --name sentry-dsn               --value "https://...@sentry.io/..."
az keyvault secret set --vault-name "$KV" --name openai-api-key           --value "sk-..."
az keyvault secret set --vault-name "$KV" --name google-geocoding-api-key --value "<key>"   # optional
az keyvault secret set --vault-name "$KV" --name gemini-api-key           --value "<key>"   # optional
az keyvault secret set --vault-name "$KV" --name mapbox-access-token      --value "<token>" # optional
```

Terraform **ignores** later drift on these (`ignore_changes = [value]`), so manual rotation
won't be reverted. Roll a new revision so the apps pick up new values:

```bash
az containerapp update -n harmonaize-prod-web -g harmonaize-prod-rg --revision-suffix r$(date +%s)
```

---

## 7. Known gaps — read before first apply

1. **Private Key Vault / Storage bootstrap (blocks first apply from outside the VNet).**
   Key Vault and the Storage account are created with `public_network_access_enabled = false`.
   Creating the KV secrets and the `media` container happens over their **data planes**, so a
   runner with no network path into the VNet will fail those steps. Choose one:
   - **Recommended:** run Terraform from a self-hosted agent / jumpbox VM *inside* the VNet (or a
     peered network).
   - **Quick bring-up:** temporarily allow your public IP, e.g. add a `network_acls` / IP rule, or
     flip those two resources to `public_network_access_enabled = true` for the bootstrap and
     tighten afterwards. (Ask if you want a `bootstrap_public_access` toggle added to the modules.)

2. **No public ingress yet (Front Door not in IaC).** The Container Apps environment uses
   `internal_load_balancer_enabled = true`, so `web_app_fqdn` resolves to a **private** VIP — it is
   not reachable from the internet. The blueprint's Front Door + WAF edge is **not** yet codified.
   Options: add an `azurerm_cdn_frontdoor_*` (or Application Gateway) module, or for a quick public
   smoke test set `internal_load_balancer_enabled = false` to give the environment a public endpoint.

3. **OpenAI is intentionally the public SDK against any OpenAI-compatible endpoint.** The app
   (`core/embedding_service.py`) uses `OpenAI(api_key=...)`, which reads `OPENAI_BASE_URL` from the
   environment. The Container Apps set `OPENAI_BASE_URL = var.openai_base_url` (default
   `https://api.openai.com/v1`) — point it at any 3rd-party OpenAI-compatible provider and set the
   matching `openai-api-key`. Azure OpenAI is **not** used unless you opt in with
   `deploy_azure_openai = true` (and note Azure OpenAI needs the separate `AzureOpenAI` client, so it
   is not a drop-in base-URL swap).

4. **Worker queue name.** The KEDA scaler watches the Redis list `celery` (the Celery default). If
   you later add custom `task_routes`/queues, update `celery_queue_name` (or add a rule per queue).

5. **Deprecation warning.** `enable_non_ssl_port` in the cache module is correct for azurerm 3.x; it
   becomes `non_ssl_port_enabled` only when you move to provider 4.x.

---

## 8. Day-2 operations

**Ship a new image version:**
```bash
docker build -f harmonaize/compose/production/django/Dockerfile -t "$ACR_LOGIN_SERVER/harmonaize_production_django:<tag>" harmonaize
docker push "$ACR_LOGIN_SERVER/harmonaize_production_django:<tag>"
# update container_app_image in tfvars, then:
terraform apply
```

**Tune worker autoscaling:** lower `worker_target_queue_length` (more aggressive) or raise
`max_replicas`, then `terraform apply`.

**Scale the database / cache:** change `db_sku_name`, `redis_capacity`, etc., then `terraform apply`.

**Tear everything down:**
```bash
terraform destroy
```
> Key Vault has soft-delete (7 days). To fully purge before reusing names:
> `az keyvault purge --name <kv-name>`. Storage/ACR names are globally unique (random suffix), so
> re-applies get fresh names automatically.

---

## 9. Command quick-reference

```bash
# auth
az login && az account set --subscription <SUB_ID>

# validate
terraform init && terraform fmt -recursive && terraform validate

# two-phase deploy
terraform apply -target=module.compute.azurerm_container_registry.acr
az acr login --name <acr-name>
docker build -f harmonaize/compose/production/django/Dockerfile -t <acr_login_server>/harmonaize_production_django:latest harmonaize
docker push <acr_login_server>/harmonaize_production_django:latest
terraform plan -out tfplan && terraform apply tfplan

# inspect
terraform output
az containerapp logs show -n harmonaize-prod-web -g harmonaize-prod-rg --tail 100
```
