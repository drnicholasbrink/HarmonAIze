resource "azurerm_user_assigned_identity" "vm_identity" {
  name                = "${var.prefix}-${var.environment}-analysis-vm-identity"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_role_assignment" "vm_kv_secrets_user" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.vm_identity.principal_id
}

resource "azurerm_role_assignment" "vm_storage_reader" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.vm_identity.principal_id
}

resource "time_sleep" "wait_for_vm_rbac" {
  depends_on = [
    azurerm_role_assignment.vm_kv_secrets_user,
    azurerm_role_assignment.vm_storage_reader
  ]
  create_duration = "60s"
}

resource "azurerm_network_interface" "nic" {
  name                = "${var.prefix}-${var.environment}-analysis-nic"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = var.subnet_id
    private_ip_address_allocation = "Static"
    private_ip_address            = "10.0.8.4"
  }
}

resource "azurerm_private_dns_a_record" "armadillo" {
  name                = "armadillo"
  zone_name           = var.private_dns_zone_name
  resource_group_name = var.resource_group_name
  ttl                 = 300
  records             = ["10.0.8.4"]
}

resource "azurerm_managed_disk" "disk" {
  name                 = "${var.prefix}-${var.environment}-analysis-disk"
  location             = var.location
  resource_group_name  = var.resource_group_name
  storage_account_type = "Premium_LRS"
  create_option        = "Empty"
  disk_size_gb         = 128
  tags                 = var.tags
}

resource "azurerm_virtual_machine_data_disk_attachment" "disk_attach" {
  managed_disk_id    = azurerm_managed_disk.disk.id
  virtual_machine_id = azurerm_linux_virtual_machine.vm.id
  lun                = 10
  caching            = "ReadWrite"
}

resource "tls_private_key" "ssh" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "azurerm_key_vault_secret" "ssh_private_key" {
  name         = "analysis-vm-ssh-private-key"
  value        = tls_private_key.ssh.private_key_pem
  key_vault_id = var.key_vault_id
}

# Config container and blobs
resource "azurerm_storage_container" "analysis_config" {
  name                  = "analysis-config"
  storage_account_name  = var.storage_account_name
  container_access_type = "private"
}

resource "azurerm_storage_blob" "armadillo_json" {
  name                   = "Armadillo.json"
  storage_account_name   = var.storage_account_name
  storage_container_name = azurerm_storage_container.analysis_config.name
  type                   = "Block"
  source                 = "${path.module}/../../harmonaize/compose/production/analysis/keycloak/realms/Armadillo.json"
}

resource "azurerm_storage_blob" "application_yml" {
  name                   = "application.yml"
  storage_account_name   = var.storage_account_name
  storage_container_name = azurerm_storage_container.analysis_config.name
  type                   = "Block"
  source                 = "${path.module}/../../harmonaize/compose/production/analysis/config/application.yml"
}

resource "azurerm_storage_blob" "profiles_json" {
  name                   = "profiles.json"
  storage_account_name   = var.storage_account_name
  storage_container_name = azurerm_storage_container.analysis_config.name
  type                   = "Block"
  source                 = "${path.module}/../../harmonaize/compose/production/analysis/data/system/profiles.json"
}

resource "azurerm_storage_blob" "docker_compose" {
  name                   = "docker-compose.analysis.yml"
  storage_account_name   = var.storage_account_name
  storage_container_name = azurerm_storage_container.analysis_config.name
  type                   = "Block"
  source                 = "${path.module}/../../harmonaize/compose/production/analysis/docker-compose.analysis.yml"
}

resource "azurerm_linux_virtual_machine" "vm" {
  name                = "${var.prefix}-${var.environment}-analysis-vm"
  location            = var.location
  resource_group_name = var.resource_group_name
  size                = "Standard_D2s_v3"
  admin_username      = "azureuser"
  network_interface_ids = [
    azurerm_network_interface.nic.id
  ]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.vm_identity.id]
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  admin_ssh_key {
    username   = "azureuser"
    public_key = tls_private_key.ssh.public_key_openssh
  }

  custom_data = base64encode(templatefile("${path.module}/cloud-init.sh", {
    user_assigned_client_id = azurerm_user_assigned_identity.vm_identity.client_id
    key_vault_name          = var.key_vault_name
    storage_account_name    = var.storage_account_name
  }))

  tags = var.tags

  depends_on = [
    time_sleep.wait_for_vm_rbac,
    azurerm_storage_blob.armadillo_json,
    azurerm_storage_blob.application_yml,
    azurerm_storage_blob.profiles_json,
    azurerm_storage_blob.docker_compose,
    azurerm_key_vault_secret.ssh_private_key
  ]
}
