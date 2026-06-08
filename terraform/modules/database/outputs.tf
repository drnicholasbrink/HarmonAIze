output "postgres_fqdn" {
  value       = azurerm_postgresql_flexible_server.pg.fqdn
  description = "The FQDN of the PostgreSQL server."
}

output "server_id" {
  value       = azurerm_postgresql_flexible_server.pg.id
  description = "The ID of the PostgreSQL server."
}

output "database_name" {
  value       = azurerm_postgresql_flexible_server_database.app.name
  description = "The name of the database."
}
