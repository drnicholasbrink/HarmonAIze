output "redis_hostname" {
  value       = azurerm_redis_cache.redis.hostname
  description = "The hostname of the Redis cache."
}

output "redis_id" {
  value       = azurerm_redis_cache.redis.id
  description = "The ID of the Redis cache."
}

output "redis_ssl_port" {
  value       = 6380
  description = "The SSL port of the Redis cache."
}

output "redis_primary_access_key" {
  value       = azurerm_redis_cache.redis.primary_access_key
  sensitive   = true
  description = "The primary access key for the Redis cache (used as the KEDA scaler password)."
}
