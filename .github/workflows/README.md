# CI/CD — Deploy to Azure Container Apps

[`deploy.yml`](./deploy.yml) builds the Django image, pushes it to ACR, runs DB migrations
as a Container Apps Job, and rolls the new image out to the **web / worker / beat / flower**
apps. It authenticates to Azure with **OIDC** (federated credential) — no stored Azure secret.

## Prerequisites

1. **Infra exists** — `terraform apply` has created the resource group, ACR, Container Apps,
   and the `*-migrate` Job (see [`terraform/DEPLOY_RUNBOOK.md`](../../terraform/DEPLOY_RUNBOOK.md)).
2. **Deploy identity** — run the one-time setup script, logged in as an Owner / User Access
   Administrator of the subscription:

   ```bash
   az login
   bash scripts/setup-github-oidc.sh --set-gh-vars
   ```

   It creates the app registration + service principal + federated credential, grants
   **AcrPush** (registry) and **Contributor** (resource group), and — with `--set-gh-vars` —
   populates the GitHub variables below via the `gh` CLI.

## Required GitHub Actions *variables*

Repo → **Settings → Secrets and variables → Actions → Variables** (these are not secret):

| Variable | Example | Source |
| :--- | :--- | :--- |
| `AZURE_CLIENT_ID` | app (client) ID | setup script |
| `AZURE_TENANT_ID` | `aab132f1-…` | setup script |
| `AZURE_SUBSCRIPTION_ID` | `5624a68c-…` | setup script |
| `RESOURCE_GROUP` | `harmonaize-prod-rg` | setup script |
| `ACR_LOGIN_SERVER` | `harmonaizeprodacrXXXX.azurecr.io` | `terraform output -raw acr_login_server` |

If your Terraform `prefix`/`environment` differ from `harmonaize`/`prod`, edit `APP_PREFIX`
in [`deploy.yml`](./deploy.yml) (the app/job names are `<APP_PREFIX>-web|worker|beat|flower|migrate`).

## Who assigns the roles?

Pick **one** path (doing both errors out — Azure rejects a duplicate role assignment):

- **Setup script (default)** — assigns AcrPush + Contributor directly. Turnkey.
- **Terraform** — run the script with `--no-roles`, then set `cicd_principal_id = "<SP object id>"`
  in `terraform.tfvars` and re-apply. IaC-managed, reproducible.

## Triggers

- Push to `main` touching `harmonaize/**` → build + deploy.
- Manual **Run workflow** (`workflow_dispatch`), optionally with a specific `image_tag`.

The image is tagged with the commit SHA (and `latest`). Migrations run **before** the rollout
and must reach `Succeeded` or the deploy fails.
