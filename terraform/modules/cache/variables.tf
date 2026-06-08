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

variable "sku_name" {
  type        = string
  description = "SKU name for Redis (Basic/Standard/Premium)"
}

variable "capacity" {
  type        = number
  description = "Redis capacity (0-6)"
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
