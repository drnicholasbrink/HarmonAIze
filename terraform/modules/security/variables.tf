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

variable "tenant_id" {
  type        = string
  description = "Azure tenant ID"
}

variable "deployer_object_id" {
  type        = string
  description = "Object ID of the deployer (current user/principal)"
}

variable "subnet_id" {
  type        = string
  description = "ID of the subnet for private endpoints"
}

variable "private_dns_zone_id" {
  type        = string
  description = "ID of the private DNS zone for Key Vault"
}

variable "suffix" {
  type        = string
  description = "Random suffix for globally unique names"
}

# --- Generated / infrastructure secret material (Terraform-owned) ---
variable "postgres_user" {
  type        = string
  description = "PostgreSQL admin username (for the composed DATABASE_URL)"
}

variable "postgres_password" {
  type        = string
  sensitive   = true
  description = "PostgreSQL admin password"
}

variable "postgres_fqdn" {
  type        = string
  description = "PostgreSQL server FQDN (for the composed DATABASE_URL)"
}

variable "postgres_db" {
  type        = string
  description = "PostgreSQL database name (for the composed DATABASE_URL)"
}

variable "redis_hostname" {
  type        = string
  description = "Redis hostname (for the composed REDIS_URL)"
}

variable "redis_ssl_port" {
  type        = number
  description = "Redis SSL port (for the composed REDIS_URL)"
}

variable "redis_primary_access_key" {
  type        = string
  sensitive   = true
  description = "Redis primary access key (REDIS_URL auth + KEDA scaler password)"
}

variable "storage_account_key" {
  type        = string
  sensitive   = true
  description = "Storage account primary access key (DJANGO_AZURE_ACCOUNT_KEY)"
}

variable "django_secret" {
  type        = string
  sensitive   = true
  description = "Django SECRET_KEY"
}

# --- Externally-supplied application secrets (set via tfvars or directly in Key Vault) ---
variable "django_admin_url" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Value for DJANGO_ADMIN_URL (the admin path)."
}

variable "sendgrid_api_key" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Value for SENDGRID_API_KEY."
}

variable "sentry_dsn" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Value for SENTRY_DSN."
}

variable "openai_api_key" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Value for OPENAI_API_KEY."
}

variable "google_geocoding_api_key" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Value for GOOGLE_GEOCODING_API_KEY (geolocation)."
}

variable "gemini_api_key" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Value for GEMINI_API_KEY (LLM-assisted geolocation)."
}

variable "mapbox_access_token" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Value for MAPBOX_ACCESS_TOKEN (geolocation)."
}

variable "analysis_deidentification_salt" {
  type        = string
  sensitive   = true
  description = "Value for ANALYSIS_DEIDENTIFICATION_SALT (federated-analysis de-identification salt)."
}
