output "vnet_id" {
  value       = azurerm_virtual_network.vnet.id
  description = "The ID of the VNet."
}

output "vnet_name" {
  value       = azurerm_virtual_network.vnet.name
  description = "The name of the VNet."
}

output "aks_subnet_id" {
  value       = azurerm_subnet.aks.id
  description = "The ID of the AKS Subnet."
}

output "appgw_subnet_id" {
  value       = azurerm_subnet.appgw.id
  description = "The ID of the App Gateway Subnet."
}

output "db_subnet_id" {
  value       = azurerm_subnet.db.id
  description = "The ID of the DB Subnet."
}

output "endpoints_subnet_id" {
  value       = azurerm_subnet.endpoints.id
  description = "The ID of the Private Endpoints Subnet."
}

output "postgres_dns_zone_id" {
  value       = azurerm_private_dns_zone.postgres.id
  description = "The ID of the Private DNS Zone for Postgres."
}

output "postgres_dns_zone_name" {
  value       = azurerm_private_dns_zone.postgres.name
  description = "The name of the Private DNS Zone for Postgres."
}

output "keyvault_dns_zone_id" {
  value       = azurerm_private_dns_zone.keyvault.id
  description = "The ID of the Private DNS Zone for Key Vault."
}

output "redis_dns_zone_id" {
  value       = azurerm_private_dns_zone.redis.id
  description = "The ID of the Private DNS Zone for Redis."
}

output "blob_dns_zone_id" {
  value       = azurerm_private_dns_zone.blob.id
  description = "The ID of the Private DNS Zone for Blob Storage."
}

output "file_dns_zone_id" {
  value       = azurerm_private_dns_zone.file.id
  description = "The ID of the Private DNS Zone for File Storage."
}

output "openai_dns_zone_id" {
  value       = azurerm_private_dns_zone.openai.id
  description = "The ID of the Private DNS Zone for OpenAI."
}

output "containerapps_subnet_id" {
  value       = azurerm_subnet.containerapps.id
  description = "The ID of the Container Apps Subnet."
}
