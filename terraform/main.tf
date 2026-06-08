data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "random_password" "postgres" {
  length           = 24
  special          = true
  override_special = "!#$%*-_=+"
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

resource "azurerm_resource_group" "rg" {
  name     = "${var.prefix}-${var.environment}-rg"
  location = var.location
  tags     = var.tags
}

module "networking" {
  source = "./modules/networking"

  prefix              = var.prefix
  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  vnet_address_space  = var.vnet_address_space
  subnet_prefixes     = var.subnet_prefixes
  tags                = var.tags
}

module "security" {
  source = "./modules/security"

  prefix              = var.prefix
  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags
  tenant_id           = data.azurerm_client_config.current.tenant_id
  deployer_object_id  = data.azurerm_client_config.current.object_id
  subnet_id           = module.networking.endpoints_subnet_id
  private_dns_zone_id = module.networking.keyvault_dns_zone_id
  suffix              = random_string.suffix.result

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

  # Externally-supplied application secrets.
  django_admin_url               = var.django_admin_url
  sendgrid_api_key               = var.sendgrid_api_key
  sentry_dsn                     = var.sentry_dsn
  openai_api_key                 = var.openai_api_key
  google_geocoding_api_key       = var.google_geocoding_api_key
  gemini_api_key                 = var.gemini_api_key
  mapbox_access_token            = var.mapbox_access_token
  analysis_deidentification_salt = var.analysis_deidentification_salt != "" ? var.analysis_deidentification_salt : random_password.deid_salt.result
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
  capacity            = var.redis_capacity
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
  openai_base_url            = var.openai_base_url
  storage_account_name       = module.storage.storage_account_name
  redis_hostname             = module.cache.redis_hostname
  redis_ssl_port             = module.cache.redis_ssl_port
  celery_queue_name          = var.celery_queue_name
  worker_target_queue_length = var.worker_target_queue_length

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
  google_geocoding_api_key_secret_id       = module.security.google_geocoding_api_key_secret_id
  gemini_api_key_secret_id                 = module.security.gemini_api_key_secret_id
  mapbox_access_token_secret_id            = module.security.mapbox_access_token_secret_id
  analysis_deidentification_salt_secret_id = module.security.analysis_deidentification_salt_secret_id
}
