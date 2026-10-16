terraform {
  required_providers {
    azapi = {
      source = "Azure/azapi"
    }
    azurerm = {
      source = "hashicorp/azurerm"
    }
  }
}

locals {
  redis_name = "${var.prefix}-${var.environment}-redis-${var.suffix}"
}

# Azure Managed Redis (AMR). Both classic "Azure Cache for Redis" (azurerm_redis_cache) and
# "Azure Cache for Redis Enterprise" are retired/blocked for new creation; Azure now requires
# AMR — Microsoft.Cache/redisEnterprise with the Balanced_* SKUs. The pinned azurerm 3.x
# provider does not know these SKUs/API version, so we create it via azapi. TLS, port 10000.
resource "azapi_resource" "redis" {
  type      = "Microsoft.Cache/redisEnterprise@2025-04-01"
  name      = local.redis_name
  parent_id = var.resource_group_id
  location  = var.location
  tags      = var.tags

  body = {
    sku = {
      name = var.sku_name
    }
    properties = {
      minimumTlsVersion = "1.2"
    }
  }

  response_export_values = ["properties.hostName"]
}

resource "azapi_resource" "redis_db" {
  type      = "Microsoft.Cache/redisEnterprise/databases@2025-04-01"
  name      = "default"
  parent_id = azapi_resource.redis.id

  body = {
    properties = {
      clientProtocol = "Encrypted" # TLS only
      port           = 10000
      # EnterpriseCluster proxies a single logical endpoint (non-OSS-cluster semantics), keeping
      # standard Redis clients — Celery broker, django-redis, the KEDA scaler — working unchanged.
      clusteringPolicy = "EnterpriseCluster"
      # Never evict: the Celery broker stores queue state that must not be dropped under memory pressure.
      evictionPolicy = "NoEviction"
    }
  }
}

# Read the database access key (POST .../listKeys) to compose the Redis connection string.
resource "azapi_resource_action" "redis_keys" {
  type        = "Microsoft.Cache/redisEnterprise/databases@2025-04-01"
  resource_id = azapi_resource.redis_db.id
  action      = "listKeys"
  method      = "POST"

  response_export_values = ["primaryKey"]
}

resource "azurerm_private_endpoint" "redis" {
  name                = "${var.prefix}-${var.environment}-redis-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "redis-connection"
    is_manual_connection           = false
    private_connection_resource_id = azapi_resource.redis.id
    subresource_names              = ["redisEnterprise"]
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}
