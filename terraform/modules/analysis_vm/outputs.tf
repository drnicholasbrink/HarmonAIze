output "vm_private_ip" {
  value       = azurerm_network_interface.nic.ip_configuration[0].private_ip_address
  description = "The private IP address of the Analysis VM."
}

output "vm_id" {
  value       = azurerm_linux_virtual_machine.vm.id
  description = "The ID of the Analysis VM."
}
