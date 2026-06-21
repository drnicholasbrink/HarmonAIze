output "endpoint_hostname" {
  value       = azurerm_cdn_frontdoor_endpoint.ep.host_name
  description = "The *.azurefd.net hostname of the Front Door endpoint (public entry point)."
}

output "frontdoor_id" {
  value       = azurerm_cdn_frontdoor_profile.fd.resource_guid
  description = "Front Door ID (the value of the X-Azure-FDID header). Set as the app's FRONTDOOR_ID to lock the origin."
}

output "profile_id" {
  value       = azurerm_cdn_frontdoor_profile.fd.id
  description = "Resource ID of the Front Door profile."
}

output "custom_domain_validation_token" {
  value       = var.custom_domain_host != "" ? azurerm_cdn_frontdoor_custom_domain.cd[0].validation_token : null
  description = "DNS TXT validation token for the custom domain (record name _dnsauth.<domain>). Null when no custom domain."
}
