resource "azurerm_virtual_network" "vnet" {
  name                = "${var.prefix}-${var.environment}-vnet"
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = var.vnet_address_space
  tags                = var.tags
}

resource "azurerm_subnet" "db" {
  name                 = "db-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.subnet_prefixes["db"]]

  delegation {
    name = "db-delegation"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

resource "azurerm_subnet" "endpoints" {
  name                 = "private-endpoints-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.subnet_prefixes["endpoints"]]
}

resource "azurerm_subnet" "containerapps" {
  name                 = "containerapps-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.subnet_prefixes["containerapps"]]
  delegation {
    name = "containerapps-delegation"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

# Network Security Groups

resource "azurerm_network_security_group" "db_nsg" {
  name                = "${var.prefix}-${var.environment}-db-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  security_rule {
    name                       = "Allow-ContainerApps-Postgres"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "5432"
    source_address_prefix      = var.subnet_prefixes["containerapps"]
    destination_address_prefix = var.subnet_prefixes["db"]
  }
}

resource "azurerm_subnet_network_security_group_association" "db" {
  subnet_id                 = azurerm_subnet.db.id
  network_security_group_id = azurerm_network_security_group.db_nsg.id
}

# Private DNS Zones

resource "azurerm_private_dns_zone" "postgres" {
  name                = "${var.prefix}.postgres.database.azure.com"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "postgres-vnet-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
}

resource "azurerm_private_dns_zone" "keyvault" {
  name                = "privatelink.vaultcore.azure.net"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "keyvault" {
  name                  = "keyvault-vnet-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.keyvault.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
}

resource "azurerm_private_dns_zone" "redis" {
  name                = "privatelink.redis.cache.windows.net"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "redis" {
  name                  = "redis-vnet-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.redis.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
}

resource "azurerm_private_dns_zone" "blob" {
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "blob" {
  name                  = "blob-vnet-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.blob.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
}

resource "azurerm_private_dns_zone" "file" {
  name                = "privatelink.file.core.windows.net"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "file" {
  name                  = "file-vnet-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.file.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
}

resource "azurerm_private_dns_zone" "openai" {
  name                = "privatelink.openai.azure.com"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "openai" {
  name                  = "openai-vnet-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.openai.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
}

# --- Analysis Stack Networking ---

resource "azurerm_subnet" "analysis" {
  count                = var.deploy_analysis_stack ? 1 : 0
  name                 = "analysis-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.subnet_prefixes["analysis"]]
}

resource "azurerm_network_security_group" "analysis_nsg" {
  count               = var.deploy_analysis_stack ? 1 : 0
  name                = "${var.prefix}-${var.environment}-analysis-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  security_rule {
    name                       = "Allow-ContainerApps-Inbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["8080", "8081"]
    source_address_prefix      = var.subnet_prefixes["containerapps"]
    destination_address_prefix = var.subnet_prefixes["analysis"]
  }

  dynamic "security_rule" {
    for_each = length(var.analyst_source_cidrs) > 0 ? [1] : []
    content {
      name                       = "Allow-Analysts-Inbound"
      priority                   = 110
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_ranges    = ["8080", "8081"]
      source_address_prefixes    = var.analyst_source_cidrs
      destination_address_prefix = var.subnet_prefixes["analysis"]
    }
  }

  # SSH from Azure Bastion only (no public IP on the VM, no SSH from anywhere else).
  dynamic "security_rule" {
    for_each = var.deploy_bastion ? [1] : []
    content {
      name                       = "Allow-Bastion-SSH"
      priority                   = 120
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_range     = "22"
      source_address_prefix      = var.subnet_prefixes["bastion"]
      destination_address_prefix = var.subnet_prefixes["analysis"]
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "analysis" {
  count                     = var.deploy_analysis_stack ? 1 : 0
  subnet_id                 = azurerm_subnet.analysis[0].id
  network_security_group_id = azurerm_network_security_group.analysis_nsg[0].id
}

resource "azurerm_public_ip" "nat_gw" {
  count               = var.deploy_analysis_stack ? 1 : 0
  name                = "${var.prefix}-${var.environment}-natgw-ip"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_nat_gateway" "nat_gw" {
  count               = var.deploy_analysis_stack ? 1 : 0
  name                = "${var.prefix}-${var.environment}-natgw"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku_name            = "Standard"
  tags                = var.tags
}

resource "azurerm_nat_gateway_public_ip_association" "nat_gw" {
  count                = var.deploy_analysis_stack ? 1 : 0
  nat_gateway_id       = azurerm_nat_gateway.nat_gw[0].id
  public_ip_address_id = azurerm_public_ip.nat_gw[0].id
}

resource "azurerm_subnet_nat_gateway_association" "analysis" {
  count          = var.deploy_analysis_stack ? 1 : 0
  subnet_id      = azurerm_subnet.analysis[0].id
  nat_gateway_id = azurerm_nat_gateway.nat_gw[0].id
}

resource "azurerm_private_dns_zone" "internal" {
  count               = var.deploy_analysis_stack ? 1 : 0
  name                = "harmonaize.internal"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "internal" {
  count                 = var.deploy_analysis_stack ? 1 : 0
  name                  = "internal-vnet-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.internal[0].name
  virtual_network_id    = azurerm_virtual_network.vnet.id
}

# --- Azure Bastion (opt-in) — interactive SSH to the analysis VM without a public IP ---

resource "azurerm_subnet" "bastion" {
  count                = var.deploy_bastion ? 1 : 0
  name                 = "AzureBastionSubnet" # name is mandated by Azure; do not change
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.subnet_prefixes["bastion"]]
}

resource "azurerm_public_ip" "bastion" {
  count               = var.deploy_bastion ? 1 : 0
  name                = "${var.prefix}-${var.environment}-bastion-ip"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_bastion_host" "bastion" {
  count               = var.deploy_bastion ? 1 : 0
  name                = "${var.prefix}-${var.environment}-bastion"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = var.bastion_sku
  tags                = var.tags

  ip_configuration {
    name                 = "configuration"
    subnet_id            = azurerm_subnet.bastion[0].id
    public_ip_address_id = azurerm_public_ip.bastion[0].id
  }
}
