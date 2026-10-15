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

variable "resource_group_id" {
  type        = string
  description = "Resource ID of the resource group (azapi parent_id for the Managed Redis cluster)."
}

variable "tags" {
  type        = map(string)
  description = "Resource tags"
}

variable "sku_name" {
  type        = string
  description = "Azure Managed Redis SKU, e.g. Balanced_B0 (smallest), Balanced_B1, MemoryOptimized_M10."
}

variable "subnet_id" {
  type        = string
  description = "ID of the subnet for private endpoints"
}

variable "private_dns_zone_id" {
  type        = string
  description = "ID of the private DNS zone for Redis"
}

variable "suffix" {
  type        = string
  description = "Random suffix for globally unique names"
}
