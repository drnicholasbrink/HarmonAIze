locals {
  openai_name      = substr(replace("${var.prefix}${var.environment}openai${var.suffix}", "-", ""), 0, 24)
  custom_subdomain = substr(replace("${var.prefix}-${var.environment}-openai-${var.suffix}", "_", "-"), 0, 63)
}

resource "azurerm_cognitive_account" "openai" {
  name                          = local.openai_name
  location                      = var.cognitive_location
  resource_group_name           = var.resource_group_name
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = local.custom_subdomain
  public_network_access_enabled = false
  tags                          = var.tags
}

resource "azurerm_cognitive_deployment" "this" {
  for_each = { for model in var.models : model.name => model }

  cognitive_account_id = azurerm_cognitive_account.openai.id
  name                 = each.key

  model {
    format  = "OpenAI"
    name    = each.value.name
    version = each.value.version
  }

  scale {
    type     = "Standard"
    capacity = each.value.capacity
  }
}

resource "azurerm_private_endpoint" "openai" {
  name                = "${var.prefix}-${var.environment}-openai-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "openai-connection"
    is_manual_connection           = false
    private_connection_resource_id = azurerm_cognitive_account.openai.id
    subresource_names              = ["account"]
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}
