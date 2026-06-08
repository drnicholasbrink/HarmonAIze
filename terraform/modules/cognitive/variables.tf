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

variable "cognitive_location" {
  type        = string
  description = "Azure region for Cognitive Services (OpenAI)"
}

variable "models" {
  type = list(object({
    name     = string
    version  = string
    capacity = number
  }))
  description = "List of OpenAI models to deploy"
}

variable "subnet_id" {
  type        = string
  description = "ID of the subnet for private endpoints"
}

variable "private_dns_zone_id" {
  type        = string
  description = "ID of the private DNS zone for OpenAI"
}

variable "suffix" {
  type        = string
  description = "Random suffix for globally unique names"
}
