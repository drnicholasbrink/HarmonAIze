output "redis_hostname" {
  value       = azapi_resource.redis.output.properties.hostName
  description = "The hostname of the Azure Managed Redis cluster."
}

output "redis_id" {
  value       = azapi_resource.redis.id
  description = "The ID of the Azure Managed Redis cluster."
}

output "redis_ssl_port" {
  value       = 10000
  description = "The TLS port of the Azure Managed Redis database."
}

output "redis_primary_access_key" {
  value       = azapi_resource_action.redis_keys.output.primaryKey
  sensitive   = true
  description = "The primary access key for the Redis database (used as the KEDA scaler password)."
}
