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

variable "subnet_id" {
  type        = string
  description = "Subnet ID of the analysis subnet"
}

variable "key_vault_id" {
  type        = string
  description = "ID of the Key Vault"
}

variable "key_vault_name" {
  type        = string
  description = "Name of the Key Vault"
}

variable "private_dns_zone_name" {
  type        = string
  description = "Name of the Private DNS Zone (harmonaize.internal)"
}

variable "storage_account_name" {
  type        = string
  description = "Name of the Azure Storage Account"
}

variable "storage_account_id" {
  type        = string
  description = "ID of the Azure Storage Account"
}

# Versionless secret IDs to enforce Terraform dependencies
variable "armadillo_admin_password_secret_id" {
  type        = string
  description = "Secret ID for armadillo admin password"
}

variable "keycloak_admin_password_secret_id" {
  type        = string
  description = "Secret ID for keycloak admin password"
}

variable "keycloak_db_password_secret_id" {
  type        = string
  description = "Secret ID for keycloak db password"
}

variable "rock_admin_password_secret_id" {
  type        = string
  description = "Secret ID for rock admin password"
}

variable "rock_user_password_secret_id" {
  type        = string
  description = "Secret ID for rock user password"
}

variable "armadillo_oidc_client_secret_secret_id" {
  type        = string
  description = "Secret ID for armadillo oidc client secret"
}
