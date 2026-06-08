output "environment_id" {
  value       = azurerm_container_app_environment.env.id
  description = "The ID of the Container Apps Environment."
}

output "web_fqdn" {
  value       = azurerm_container_app.web.ingress[0].fqdn
  description = "The FQDN of the web Container App."
}

output "acr_login_server" {
  value       = azurerm_container_registry.acr.login_server
  description = "The login server URL of the Container Registry."
}

output "identity_principal_id" {
  value       = azurerm_user_assigned_identity.app.principal_id
  description = "The principal ID of the user-assigned identity."
}
