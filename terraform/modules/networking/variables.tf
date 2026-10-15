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

variable "deploy_analysis_stack" {
  type        = bool
  default     = false
  description = "Deploy Armadillo analysis VM stack"
}

variable "analyst_source_cidrs" {
  type        = list(string)
  default     = []
  description = "IP CIDR ranges for DataSHIELD analysts"
}

variable "deploy_bastion" {
  type        = bool
  default     = false
  description = "Deploy Azure Bastion for secure SSH to the analysis VM (no public IP on the VM)."
}

variable "bastion_sku" {
  type        = string
  default     = "Basic"
  description = "Azure Bastion SKU (Basic or Standard)."
}
