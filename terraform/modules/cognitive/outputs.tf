output "openai_endpoint" {
  value       = azurerm_cognitive_account.openai.endpoint
  description = "The endpoint of the OpenAI service."
}

output "openai_id" {
  value       = azurerm_cognitive_account.openai.id
  description = "The ID of the OpenAI service."
}
