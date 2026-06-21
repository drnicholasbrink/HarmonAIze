resource "azurerm_postgresql_flexible_server" "pg" {
  name                          = "${var.prefix}-${var.environment}-pg"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  version                       = "16"
  delegated_subnet_id           = var.delegated_subnet_id
  private_dns_zone_id           = var.private_dns_zone_id
  administrator_login           = var.admin_username
  administrator_password        = var.admin_password
  zone                          = var.enable_ha ? "1" : null
  storage_mb                    = var.storage_mb
  sku_name                      = var.sku_name
  public_network_access_enabled = false
  tags                          = var.tags

  dynamic "high_availability" {
    for_each = var.enable_ha ? [1] : []
    content {
      mode                      = "ZoneRedundant"
      standby_availability_zone = "2"
    }
  }
}

resource "azurerm_postgresql_flexible_server_configuration" "vector" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.pg.id
  value     = "VECTOR"
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = "harmonaize"
  server_id = azurerm_postgresql_flexible_server.pg.id
  collation = "en_US.utf8"
  charset   = "UTF8"
}
