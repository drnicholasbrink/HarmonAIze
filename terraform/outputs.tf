output "resource_group_name" {
  value       = azurerm_resource_group.rg.name
  description = "The name of the resource group."
}

output "vnet_id" {
  value       = module.networking.vnet_id
  description = "The ID of the Virtual Network."
}

output "container_app_environment_id" {
  value       = module.compute.environment_id
  description = "The ID of the Container Apps Environment."
}

output "web_app_fqdn" {
  value       = module.compute.web_fqdn
  description = "The FQDN of the web Container App."
}

output "postgres_fqdn" {
  value       = module.database.postgres_fqdn
  description = "The FQDN of the PostgreSQL Flexible Server database."
}

output "redis_hostname" {
  value       = module.cache.redis_hostname
  description = "The hostname of the Azure Cache for Redis instance."
}

output "key_vault_uri" {
  value       = module.security.key_vault_uri
  description = "The URI of the Azure Key Vault."
}

output "openai_endpoint" {
  value       = var.deploy_azure_openai ? module.cognitive[0].openai_endpoint : null
  description = "The endpoint of the Azure OpenAI service (null unless deploy_azure_openai = true)."
}

output "storage_account_name" {
  value       = module.storage.storage_account_name
  description = "The name of the primary Storage Account."
}

output "acr_login_server" {
  value       = module.compute.acr_login_server
  description = "The login server of the Azure Container Registry."
}
