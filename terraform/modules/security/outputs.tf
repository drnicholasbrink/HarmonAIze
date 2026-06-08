output "key_vault_id" {
  value       = azurerm_key_vault.kv.id
  description = "The ID of the Key Vault."
}

output "key_vault_uri" {
  value       = azurerm_key_vault.kv.vault_uri
  description = "The URI of the Key Vault."
}

# Versionless secret IDs (consumed by the Container Apps as managed-identity references).
output "django_secret_key_secret_id" {
  value       = azurerm_key_vault_secret.django_secret_key.versionless_id
  description = "KV secret ID for DJANGO_SECRET_KEY."
}

output "database_url_secret_id" {
  value       = azurerm_key_vault_secret.database_url.versionless_id
  description = "KV secret ID for DATABASE_URL."
}

output "redis_url_secret_id" {
  value       = azurerm_key_vault_secret.redis_url.versionless_id
  description = "KV secret ID for REDIS_URL / CELERY_BROKER_URL."
}

output "redis_password_secret_id" {
  value       = azurerm_key_vault_secret.redis_password.versionless_id
  description = "KV secret ID for the Redis access key (KEDA scaler)."
}

output "storage_account_key_secret_id" {
  value       = azurerm_key_vault_secret.storage_account_key.versionless_id
  description = "KV secret ID for DJANGO_AZURE_ACCOUNT_KEY."
}

output "django_admin_url_secret_id" {
  value       = azurerm_key_vault_secret.django_admin_url.versionless_id
  description = "KV secret ID for DJANGO_ADMIN_URL."
}

output "sendgrid_api_key_secret_id" {
  value       = azurerm_key_vault_secret.sendgrid_api_key.versionless_id
  description = "KV secret ID for SENDGRID_API_KEY."
}

output "sentry_dsn_secret_id" {
  value       = azurerm_key_vault_secret.sentry_dsn.versionless_id
  description = "KV secret ID for SENTRY_DSN."
}

output "openai_api_key_secret_id" {
  value       = azurerm_key_vault_secret.openai_api_key.versionless_id
  description = "KV secret ID for OPENAI_API_KEY."
}

output "google_geocoding_api_key_secret_id" {
  value       = azurerm_key_vault_secret.google_geocoding_api_key.versionless_id
  description = "KV secret ID for GOOGLE_GEOCODING_API_KEY."
}

output "gemini_api_key_secret_id" {
  value       = azurerm_key_vault_secret.gemini_api_key.versionless_id
  description = "KV secret ID for GEMINI_API_KEY."
}

output "mapbox_access_token_secret_id" {
  value       = azurerm_key_vault_secret.mapbox_access_token.versionless_id
  description = "KV secret ID for MAPBOX_ACCESS_TOKEN."
}

output "analysis_deidentification_salt_secret_id" {
  value       = azurerm_key_vault_secret.analysis_deidentification_salt.versionless_id
  description = "KV secret ID for ANALYSIS_DEIDENTIFICATION_SALT."
}
