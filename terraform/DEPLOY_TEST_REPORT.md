# HarmonAIze — Full Deployment Report (live apply)

**Date:** 2026-06-21
**Account:** `theo@hydrosig.com` · Subscription `Azure subscription 1` (`5624a68c-…`) · **Owner**
**Region:** `southafricanorth` · **Tiers deployed:** core + Front Door + analysis VM + Bastion (Azure OpenAI off)
**Tooling:** Terraform 1.15.5 · azurerm 3.117.1 · azapi 2.10.0 · az CLI 2.80.0

End-to-end deployment of the full stack to live Azure, fixing issues as they surfaced. The
infrastructure (48 resources) provisioned successfully; the app image required several fixes to
pass migrations.

---

## Deployment phases
| Phase | Action | Result |
| :--- | :--- | :--- |
| A | Create ACR (`terraform apply -target`) | ✅ |
| B | Build image (`az acr build`, cloud-side) | ✅ (after fixes 1–3) |
| D | Full `terraform apply` (107 planned) | ✅ 48 resources after fixes 4–7,10 |
| E | DB migration Job | ✅ after fixes 8–9 (+ deploy-time extension step) |
| H | Roll apps to fixed image + verify | ✅ |
| G | Front Door origin lockdown second pass | ⏭️ skipped — needs app middleware (gap noted) |

**Final verification (all ✅):** web revision Healthy; all 4 apps Running; migration Job Succeeded;
direct ACA origin `GET /` → **200**; Front Door edge `GET /` → **200** (after ~11 min propagation
following the #11 fix).

---

## Issues found & fixed

| # | Severity | Issue | Fix | Type |
| :-- | :-- | :-- | :-- | :-- |
| 1 | low | `.dockerignore` didn't exclude `vendor/` (27 MB Java source) | Added `vendor/`, analysis dir, py-caches | script (app) |
| 2 | info | `az acr build --file` is cwd-relative | Run from the context dir | process |
| 3 | info | `az acr build` log stream crashes on Windows (`cp1252`); `PYTHONIOENCODING`/`PYTHONUTF8` don't fully fix it | Poll `az acr task show-run` for status | environment |
| 4 | **blocking** | `analysis_vm` blob `source` paths used `../../` → resolved to `terraform/harmonaize` | Changed to `../../../` (repo root) | **script bug** |
| 5 | **blocking** | `analysis_vm` SSH-key Key Vault write raced RBAC propagation (403) | `depends_on = [module.security]` on the module | **script bug** |
| 6 | **blocking** | Classic Azure Cache for Redis creation blocked (retirement) | — superseded by #7 | forced |
| 7 | **blocking** | Redis *Enterprise* also blocked — only **Azure Managed Redis** allowed; azurerm 3.x can't express `Balanced_*` SKUs | Provision AMR via **azapi** (`Balanced_B0`, API `2025-04-01`); add azapi provider; declare `Azure/azapi` in cache module `required_providers`; new `privatelink.redisenterprise.cache.azure.net` zone; port 10000 | **script bug / forced** |
| 8 | **blocking** | `fido2` unpinned → pip pulled ≥1.2 which removed `webauthn_json_mapping`, breaking `django-allauth[mfa]==65.4.1` (URLconf load → all mgmt commands) | Pinned `fido2<1.2` in `requirements/base.txt`; removed the incorrect `local.txt` `>=1.2.0` pin | **script bug (app)** |
| 9 | **blocking** | `core/migrations/0001_initial.py` creates `VectorField` columns but never created the pgvector extension → `type "vector" does not exist` | Added `VectorExtension()` as first migration op; **and** a deploy-time `CREATE EXTENSION IF NOT EXISTS vector` step in the migrate Job command (works with the already-built image) | **script bug (app)** |
| 10 | **blocking** | `azurerm_container_app_environment` force-replaced on every apply (Azure-assigned `infrastructure_resource_group_name` `ME_…` → null), cascade-replacing all 4 apps (caused the recurring CAE deletions) | `lifecycle { ignore_changes = [infrastructure_resource_group_name] }` | **script bug** |
| 11 | high | Front Door returned 404: `origin_host_header` was set to the public azurefd.net host, but **ACA ingress routes by Host and 404s** anything other than the app FQDN | Set `origin_host_header = var.web_origin_host` (the ACA FQDN). App should set `USE_X_FORWARDED_HOST=True` to recover the public host | **script bug** |

Validated earlier and still holding: the `kv_bootstrap_allowed_ip` fix (all ~20 Key Vault secrets wrote from this off-VNet machine), the `analysis` subnet prefix, and the `tls`/azapi provider declarations.

---

## Front Door origin lockdown — completed
- Added `config/middleware.py` (`FrontDoorIDMiddleware`) + wired it in `production.py` with `FRONTDOOR_ID`
  and `USE_X_FORWARDED_HOST = True`; allow-listed `.azurefd.net`; ran the `frontdoor_id` second pass.
- **Verified:** direct ACA origin `GET /` → **403** (rejected); Front Door `GET /` → **200**.

## Known gaps (by design)
- **Azure OpenAI** left off (opt-in; needs separate access approval).
- **WAF managed rules** require the Premium SKU (Standard ships the custom rate-limit rule only).
- **Front Door propagation**: origin/route changes take several minutes (+ a health-probe cycle) to serve.
- **`:latest` mutability / build-poll timing**: pin image digests for deploys, and poll the *specific* ACR
  run id (not `--top 1`) — watching the latest run can match a prior build that already succeeded.
- **Azure OpenAI** left off (opt-in; needs separate access approval).
- **ACR layer-cache quirk:** a rebuild that changed only a migration file produced an identical digest, so the `VectorExtension()` migration didn't reach the running image — hence the deploy-time extension step in #9. Use `--no-cache` (or a unique tag) when only late-COPY files change.

## Cost note
Azure Managed Redis `Balanced_B0`, Postgres GP + zone-redundant HA, Front Door, the analysis VM, and Bastion are all billable; deallocate/destroy when the evaluation is done.
