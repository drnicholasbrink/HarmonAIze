locals {
  key_vault_name = substr(replace("${var.prefix}${var.environment}kv${var.suffix}", "-", ""), 0, 24)

  # Full connection strings are composed here so that the password/access-key never
  # appears in any non-secret Container App env var — only as a Key Vault secret.
  database_url = "postgres://${var.postgres_user}:${var.postgres_password}@${var.postgres_fqdn}:5432/${var.postgres_db}?sslmode=require"
  redis_url    = "rediss://:${var.redis_primary_access_key}@${var.redis_hostname}:${var.redis_ssl_port}/0"
}

resource "azurerm_key_vault" "kv" {
  name                          = local.key_vault_name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  tenant_id                     = var.tenant_id
  sku_name                      = "standard"
  enable_rbac_authorization     = true
  public_network_access_enabled = false
  purge_protection_enabled      = false
  soft_delete_retention_days    = 7
  tags                          = var.tags
}

resource "azurerm_role_assignment" "deployer_kv_secrets" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.deployer_object_id
}

# ---------------------------------------------------------------------------
# Terraform-owned secrets (generated passwords + composed connection strings).
# These are the single source of truth; the Container Apps read them by reference.
# ---------------------------------------------------------------------------
resource "azurerm_key_vault_secret" "postgres_password" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "postgres-password"
  value        = var.postgres_password
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "django_secret_key" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "django-secret-key"
  value        = var.django_secret
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "database_url" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "database-url"
  value        = local.database_url
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "redis_url" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "redis-url"
  value        = local.redis_url
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "redis_password" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "redis-password"
  value        = var.redis_primary_access_key
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "storage_account_key" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "storage-account-key"
  value        = var.storage_account_key
  key_vault_id = azurerm_key_vault.kv.id
}

# ---------------------------------------------------------------------------
# Externally-supplied secrets. Values may be seeded from tfvars on the first
# apply and rotated directly in Key Vault afterwards; Terraform ignores later
# value drift so manual rotations are never overwritten.
# ---------------------------------------------------------------------------
resource "azurerm_key_vault_secret" "django_admin_url" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "django-admin-url"
  value        = var.django_admin_url
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "sendgrid_api_key" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "sendgrid-api-key"
  value        = var.sendgrid_api_key
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "sentry_dsn" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "sentry-dsn"
  value        = var.sentry_dsn
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "openai_api_key" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "openai-api-key"
  value        = var.openai_api_key
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "google_geocoding_api_key" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "google-geocoding-api-key"
  value        = var.google_geocoding_api_key
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "gemini_api_key" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "gemini-api-key"
  value        = var.gemini_api_key
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "mapbox_access_token" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "mapbox-access-token"
  value        = var.mapbox_access_token
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "analysis_deidentification_salt" {
  depends_on   = [azurerm_role_assignment.deployer_kv_secrets]
  name         = "analysis-deidentification-salt"
  value        = var.analysis_deidentification_salt
  key_vault_id = azurerm_key_vault.kv.id
  lifecycle {
    ignore_changes = [value]
  }
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
