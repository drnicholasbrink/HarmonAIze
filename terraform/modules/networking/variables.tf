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
  description = "Primary Azure region"
}

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "vnet_address_space" {
  type        = list(string)
  description = "VNet address space"
}

variable "subnet_prefixes" {
  type        = map(string)
  description = "Subnet prefixes map"
}

variable "tags" {
  type        = map(string)
  description = "Resource tags"
}
