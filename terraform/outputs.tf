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

output "migrate_job_name" {
  value       = module.compute.migrate_job_name
  description = "Name of the database migration Container Apps Job."
}

output "frontdoor_endpoint_hostname" {
  value       = var.deploy_frontdoor ? module.frontdoor[0].endpoint_hostname : null
  description = "The *.azurefd.net hostname of the Front Door endpoint (public entry point)."
}

output "frontdoor_id" {
  value       = var.deploy_frontdoor ? module.frontdoor[0].frontdoor_id : null
  description = "Front Door ID (X-Azure-FDID). Set this as var.frontdoor_id (second apply) to lock the origin to Front Door."
}

output "bastion_host_name" {
  value       = module.networking.bastion_host_name
  description = "Azure Bastion host name for SSH onto the analysis VM (null unless deploy_bastion = true)."
}

output "analysis_vm_name" {
  value       = var.deploy_analysis_stack ? "${var.prefix}-${var.environment}-analysis-vm" : null
  description = "Name of the analysis VM (null unless deploy_analysis_stack = true)."
}
