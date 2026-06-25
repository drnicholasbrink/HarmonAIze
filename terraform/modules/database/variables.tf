variable "prefix" {
  type        = string
  description = "Prefix for resources"
}

variable "environment" {
  type        = string
  description = "Environment name"
}

variable "location" {
  type        = string
  description = "Azure region"
}

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "tags" {
  type        = map(string)
  description = "Resource tags"
}

variable "delegated_subnet_id" {
  type        = string
  description = "ID of the subnet delegated to PostgreSQL"
}

variable "private_dns_zone_id" {
  type        = string
  description = "ID of the private DNS zone for PostgreSQL"
}

variable "admin_username" {
  type        = string
  description = "PostgreSQL admin username"
}

variable "admin_password" {
  type        = string
  sensitive   = true
  description = "PostgreSQL admin password"
}

variable "sku_name" {
  type        = string
  description = "SKU name for PostgreSQL"
}

variable "storage_mb" {
  type        = number
  description = "Storage size in MB"
}

variable "auto_grow_enabled" {
  type        = bool
  description = "Enable automatic storage growth for PostgreSQL"
}

variable "enable_ha" {
  type        = bool
  description = "Enable high availability for PostgreSQL"
}

variable "suffix" {
  type        = string
  description = "Random suffix appended to the globally-unique PostgreSQL server name (its FQDN is *.postgres.database.azure.com, so the name must be unique across Azure and survive deploy/destroy cycles)."
}
