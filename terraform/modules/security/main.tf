locals {
  key_vault_name = substr(replace("${var.prefix}${var.environment}kv${var.suffix}", "-", ""), 0, 24)

  # Full connection strings are composed here so that the password/access-key never
  # appears in any non-secret Container App env var — only as a Key Vault secret.
  database_url = "postgres://${var.postgres_user}:${urlencode(var.postgres_password)}@${var.postgres_fqdn}:5432/${var.postgres_db}?sslmode=require"
  redis_url    = "rediss://:${urlencode(var.redis_primary_access_key)}@${var.redis_hostname}:${var.redis_ssl_port}/0"
}

resource "azurerm_key_vault" "kv" {
  name                       = local.key_vault_name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true
  purge_protection_enabled   = var.environment == "prod"
  soft_delete_retention_days = 7
  tags                       = var.tags

  # Private by default. When a bootstrap IP is supplied (deployer is outside the VNet), the
  # public endpoint is enabled but locked to that single IP via a default-Deny network ACL.
  # The private endpoint below always provides in-VNet access regardless of this setting.
  public_network_access_enabled = var.kv_bootstrap_allowed_ip != ""

  dynamic "network_acls" {
    for_each = var.kv_bootstrap_allowed_ip != "" ? [1] : []
    content {
      default_action = "Deny"
      bypass         = "AzureServices"
      ip_rules       = ["${var.kv_bootstrap_allowed_ip}/32"]
    }
  }
}

resource "azurerm_role_assignment" "deployer_kv_secrets" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.deployer_object_id
}

resource "time_sleep" "wait_for_kv_rbac" {
  depends_on      = [azurerm_role_assignment.deployer_kv_secrets]
  create_duration = "60s"
}

# ---------------------------------------------------------------------------
# Terraform-owned secrets (generated passwords + composed connection strings).
# These are the single source of truth; the Container Apps read them by reference.
# ---------------------------------------------------------------------------
resource "azurerm_key_vault_secret" "postgres_password" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "postgres-password"
  value        = var.postgres_password
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "django_secret_key" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "django-secret-key"
  value        = var.django_secret
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "database_url" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "database-url"
  value        = local.database_url
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "redis_url" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "redis-url"
  value        = local.redis_url
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "redis_password" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "redis-password"
  value        = var.redis_primary_access_key
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "storage_account_key" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "storage-account-key"
  value        = var.storage_account_key
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "flower_password" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "flower-password"
  value        = var.flower_password
  key_vault_id = azurerm_key_vault.kv.id
}

# ---------------------------------------------------------------------------
# Externally-supplied secrets. Values may be seeded from tfvars on the first
# apply and rotated directly in Key Vault afterwards; Terraform ignores later
# value drift so manual rotations are never overwritten.
# ---------------------------------------------------------------------------
resource "azurerm_key_vault_secret" "django_admin_url" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "django-admin-url"
  value        = var.django_admin_url
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "sendgrid_api_key" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "sendgrid-api-key"
  value        = var.sendgrid_api_key != "" ? var.sendgrid_api_key : "REPLACE-IN-KEY-VAULT"
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "sentry_dsn" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "sentry-dsn"
  value        = var.sentry_dsn != "" ? var.sentry_dsn : "REPLACE-IN-KEY-VAULT"
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "openai_api_key" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "openai-api-key"
  value        = var.openai_api_key != "" ? var.openai_api_key : "REPLACE-IN-KEY-VAULT"
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "google_geocoding_api_key" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "google-geocoding-api-key"
  value        = var.google_geocoding_api_key != "" ? var.google_geocoding_api_key : "REPLACE-IN-KEY-VAULT"
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "gemini_api_key" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "gemini-api-key"
  value        = var.gemini_api_key != "" ? var.gemini_api_key : "REPLACE-IN-KEY-VAULT"
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "mapbox_access_token" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "mapbox-access-token"
  value        = var.mapbox_access_token != "" ? var.mapbox_access_token : "REPLACE-IN-KEY-VAULT"
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "analysis_deidentification_salt" {
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "analysis-deidentification-salt"
  value        = var.analysis_deidentification_salt
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------------------
# Federated-Analysis Stack Secrets (conditional on deploy_analysis_stack)
# ---------------------------------------------------------------------------
resource "random_password" "armadillo_admin" {
  count   = var.deploy_analysis_stack ? 1 : 0
  length  = 24
  special = false
}

resource "azurerm_key_vault_secret" "armadillo_admin_password" {
  count        = var.deploy_analysis_stack ? 1 : 0
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "armadillo-admin-password"
  value        = random_password.armadillo_admin[0].result
  key_vault_id = azurerm_key_vault.kv.id
}

resource "random_password" "keycloak_admin" {
  count   = var.deploy_analysis_stack ? 1 : 0
  length  = 24
  special = false
}

resource "azurerm_key_vault_secret" "keycloak_admin_password" {
  count        = var.deploy_analysis_stack ? 1 : 0
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "keycloak-admin-password"
  value        = random_password.keycloak_admin[0].result
  key_vault_id = azurerm_key_vault.kv.id
}

resource "random_password" "keycloak_db" {
  count   = var.deploy_analysis_stack ? 1 : 0
  length  = 24
  special = false
}

resource "azurerm_key_vault_secret" "keycloak_db_password" {
  count        = var.deploy_analysis_stack ? 1 : 0
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "keycloak-db-password"
  value        = random_password.keycloak_db[0].result
  key_vault_id = azurerm_key_vault.kv.id
}

resource "random_password" "rock_admin" {
  count   = var.deploy_analysis_stack ? 1 : 0
  length  = 24
  special = false
}

resource "azurerm_key_vault_secret" "rock_admin_password" {
  count        = var.deploy_analysis_stack ? 1 : 0
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "rock-admin-password"
  value        = random_password.rock_admin[0].result
  key_vault_id = azurerm_key_vault.kv.id
}

resource "random_password" "rock_user" {
  count   = var.deploy_analysis_stack ? 1 : 0
  length  = 24
  special = false
}

resource "azurerm_key_vault_secret" "rock_user_password" {
  count        = var.deploy_analysis_stack ? 1 : 0
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "rock-user-password"
  value        = random_password.rock_user[0].result
  key_vault_id = azurerm_key_vault.kv.id
}

resource "random_password" "armadillo_oidc_client" {
  count   = var.deploy_analysis_stack ? 1 : 0
  length  = 32
  special = false
}

resource "azurerm_key_vault_secret" "armadillo_oidc_client_secret" {
  count        = var.deploy_analysis_stack ? 1 : 0
  depends_on   = [time_sleep.wait_for_kv_rbac]
  name         = "armadillo-oidc-client-secret"
  value        = random_password.armadillo_oidc_client[0].result
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_private_endpoint" "kv" {
  name                = "${var.prefix}-${var.environment}-kv-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "kv-connection"
    is_manual_connection           = false
    private_connection_resource_id = azurerm_key_vault.kv.id
    subresource_names              = ["vault"]
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}
