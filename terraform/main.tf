data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "random_password" "postgres" {
  length           = 24
  special          = true
  override_special = "!*-_=+"
}

resource "random_password" "django_secret" {
  length  = 50
  special = true
}

# Secure default for the de-identification salt so the insecure in-code default is never used.
# A user-supplied value (var.analysis_deidentification_salt) takes precedence.
resource "random_password" "deid_salt" {
  length  = 32
  special = false
}

resource "random_password" "flower" {
  length  = 24
  special = false
}

# Random, unguessable admin path used when django_admin_url is not supplied. An empty
# DJANGO_ADMIN_URL would collide with the home route (path("", ...)) and break the admin.
resource "random_string" "admin_path" {
  length  = 12
  special = false
  upper   = false
}

resource "azurerm_resource_group" "rg" {
  name     = "${var.prefix}-${var.environment}-rg"
  location = var.location
  tags     = var.tags
}

module "networking" {
  source = "./modules/networking"

  prefix                = var.prefix
  environment           = var.environment
  location              = var.location
  resource_group_name   = azurerm_resource_group.rg.name
  vnet_address_space    = var.vnet_address_space
  subnet_prefixes       = var.subnet_prefixes
  tags                  = var.tags
  deploy_analysis_stack = var.deploy_analysis_stack
  analyst_source_cidrs  = var.analyst_source_cidrs
  deploy_bastion        = var.deploy_bastion
  bastion_sku           = var.bastion_sku
}

module "security" {
  source = "./modules/security"

  prefix                  = var.prefix
  environment             = var.environment
  location                = var.location
  resource_group_name     = azurerm_resource_group.rg.name
  tags                    = var.tags
  tenant_id               = data.azurerm_client_config.current.tenant_id
  deployer_object_id      = data.azurerm_client_config.current.object_id
  subnet_id               = module.networking.endpoints_subnet_id
  private_dns_zone_id     = module.networking.keyvault_dns_zone_id
  suffix                  = random_string.suffix.result
  kv_bootstrap_allowed_ip = var.kv_bootstrap_allowed_ip

  # Generated / infrastructure secret material composed into Key Vault.
  postgres_user            = var.postgres_admin_username
  postgres_password        = random_password.postgres.result
  postgres_fqdn            = module.database.postgres_fqdn
  postgres_db              = module.database.database_name
  redis_hostname           = module.cache.redis_hostname
  redis_ssl_port           = module.cache.redis_ssl_port
  redis_primary_access_key = module.cache.redis_primary_access_key
  storage_account_key      = module.storage.primary_access_key
  django_secret            = random_password.django_secret.result
  flower_password          = random_password.flower.result

  # Externally-supplied application secrets.
  django_admin_url               = var.django_admin_url != "" ? var.django_admin_url : "manage-${random_string.admin_path.result}/"
  sendgrid_api_key               = var.sendgrid_api_key
  sentry_dsn                     = var.sentry_dsn
  openai_api_key                 = var.openai_api_key
  openai_base_url                = var.openai_base_url
  openai_embedding_model         = var.openai_embedding_model
  openai_transformation_model    = var.openai_transformation_model
  google_geocoding_api_key       = var.google_geocoding_api_key
  gemini_api_key                 = var.gemini_api_key
  mapbox_access_token            = var.mapbox_access_token
  analysis_deidentification_salt = var.analysis_deidentification_salt != "" ? var.analysis_deidentification_salt : random_password.deid_salt.result
  deploy_analysis_stack          = var.deploy_analysis_stack
}

module "database" {
  source = "./modules/database"

  prefix              = var.prefix
  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags

  delegated_subnet_id = module.networking.db_subnet_id
  private_dns_zone_id = module.networking.postgres_dns_zone_id
  admin_username      = var.postgres_admin_username
  admin_password      = random_password.postgres.result
  sku_name            = var.db_sku_name
  storage_mb          = var.db_storage_mb
  auto_grow_enabled   = var.db_auto_grow_enabled
  enable_ha           = var.enable_postgres_ha
}

module "cache" {
  source = "./modules/cache"

  prefix              = var.prefix
  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags

  sku_name            = var.redis_sku_name
  resource_group_id   = azurerm_resource_group.rg.id
  subnet_id           = module.networking.endpoints_subnet_id
  private_dns_zone_id = module.networking.redis_dns_zone_id
  suffix              = random_string.suffix.result
}

module "storage" {
  source = "./modules/storage"

  prefix              = var.prefix
  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags

  subnet_id           = module.networking.endpoints_subnet_id
  private_dns_zone_id = module.networking.blob_dns_zone_id
  replication_type    = var.storage_replication_type
  suffix              = random_string.suffix.result
}

module "cognitive" {
  source = "./modules/cognitive"
  count  = var.deploy_azure_openai ? 1 : 0

  prefix              = var.prefix
  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags

  cognitive_location  = var.cognitive_location
  models              = var.openai_models
  subnet_id           = module.networking.endpoints_subnet_id
  private_dns_zone_id = module.networking.openai_dns_zone_id
  suffix              = random_string.suffix.result
}

module "compute" {
  source = "./modules/compute"

  prefix              = var.prefix
  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags

  containerapps_subnet_id    = module.networking.containerapps_subnet_id
  container_image            = var.container_app_image
  acr_sku                    = var.acr_sku
  min_replicas               = var.min_replicas
  max_replicas               = var.max_replicas
  key_vault_id               = module.security.key_vault_id
  suffix                     = random_string.suffix.result
  allowed_hosts              = var.allowed_hosts
  analysis_enabled           = var.analysis_enabled
  storage_account_name       = module.storage.storage_account_name
  redis_hostname             = module.cache.redis_hostname
  redis_ssl_port             = module.cache.redis_ssl_port
  celery_queue_name          = var.celery_queue_name
  worker_target_queue_length = var.worker_target_queue_length

  # Public ingress + Front Door origin lockdown.
  enable_external_ingress = var.deploy_frontdoor
  frontdoor_id            = var.frontdoor_id

  # All application secrets are referenced from Key Vault.
  django_secret_key_secret_id              = module.security.django_secret_key_secret_id
  database_url_secret_id                   = module.security.database_url_secret_id
  redis_url_secret_id                      = module.security.redis_url_secret_id
  redis_password_secret_id                 = module.security.redis_password_secret_id
  storage_account_key_secret_id            = module.security.storage_account_key_secret_id
  django_admin_url_secret_id               = module.security.django_admin_url_secret_id
  sendgrid_api_key_secret_id               = module.security.sendgrid_api_key_secret_id
  sentry_dsn_secret_id                     = module.security.sentry_dsn_secret_id
  openai_api_key_secret_id                 = module.security.openai_api_key_secret_id
  openai_base_url_secret_id                = module.security.openai_base_url_secret_id
  openai_embedding_model_secret_id         = module.security.openai_embedding_model_secret_id
  openai_transformation_model_secret_id    = module.security.openai_transformation_model_secret_id
  google_geocoding_api_key_secret_id       = module.security.google_geocoding_api_key_secret_id
  gemini_api_key_secret_id                 = module.security.gemini_api_key_secret_id
  mapbox_access_token_secret_id            = module.security.mapbox_access_token_secret_id
  analysis_deidentification_salt_secret_id = module.security.analysis_deidentification_salt_secret_id
  flower_password_secret_id                = module.security.flower_password_secret_id
  deploy_analysis_stack                    = var.deploy_analysis_stack
  armadillo_admin_password_secret_id       = module.security.armadillo_admin_password_secret_id
  keycloak_admin_password_secret_id        = module.security.keycloak_admin_password_secret_id
}

# ---------------------------------------------------------------------------
# CI/CD (GitHub Actions) deploy identity — opt-in via var.cicd_principal_id.
# Grants the pipeline's service principal just enough to push images and roll
# out the Container Apps + run the migrate Job. It does NOT get Key Vault access:
# the apps read their secrets via their own managed identity at runtime.
# Alternative to running scripts/setup-github-oidc.sh with role assignment — use
# one or the other, not both (a duplicate (principal, role, scope) errors out).
# ---------------------------------------------------------------------------
resource "azurerm_role_assignment" "cicd_acr_push" {
  count                = var.cicd_principal_id != "" ? 1 : 0
  scope                = module.compute.acr_id
  role_definition_name = "AcrPush"
  principal_id         = var.cicd_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "cicd_rg_contributor" {
  count                = var.cicd_principal_id != "" ? 1 : 0
  scope                = azurerm_resource_group.rg.id
  role_definition_name = "Contributor"
  principal_id         = var.cicd_principal_id
  principal_type       = "ServicePrincipal"
}

module "analysis_vm" {
  count  = var.deploy_analysis_stack ? 1 : 0
  source = "./modules/analysis_vm"

  # Wait for the security module's Key Vault RBAC propagation (time_sleep.wait_for_kv_rbac)
  # before this module writes the VM's SSH key secret, otherwise the data-plane write can 403.
  depends_on = [module.security]

  prefix              = var.prefix
  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags

  subnet_id             = module.networking.analysis_subnet_id
  key_vault_id          = module.security.key_vault_id
  key_vault_name        = module.security.key_vault_name
  private_dns_zone_name = module.networking.internal_dns_zone_name
  storage_account_name  = module.storage.storage_account_name
  storage_account_id    = module.storage.storage_account_id

  # Pass generated secret IDs to enforce Terraform dependencies
  armadillo_admin_password_secret_id     = module.security.armadillo_admin_password_secret_id
  keycloak_admin_password_secret_id      = module.security.keycloak_admin_password_secret_id
  keycloak_db_password_secret_id         = module.security.keycloak_db_password_secret_id
  rock_admin_password_secret_id          = module.security.rock_admin_password_secret_id
  rock_user_password_secret_id           = module.security.rock_user_password_secret_id
  armadillo_oidc_client_secret_secret_id = module.security.armadillo_oidc_client_secret_secret_id
}

module "frontdoor" {
  source = "./modules/frontdoor"
  count  = var.deploy_frontdoor ? 1 : 0

  prefix              = var.prefix
  environment         = var.environment
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags

  web_origin_host    = module.compute.web_fqdn
  custom_domain_host = var.frontdoor_custom_domain
}
