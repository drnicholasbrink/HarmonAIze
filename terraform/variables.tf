variable "prefix" {
  type        = string
  description = "A prefix for all resources created in this deployment."
  default     = "harmonaize"
}

variable "environment" {
  type        = string
  description = "The environment name (e.g. dev, staging, prod)."
  default     = "prod"
}

variable "location" {
  type        = string
  description = "The primary Azure region to deploy HarmonAIze resources."
  default     = "southafricanorth"
}

variable "cognitive_location" {
  type        = string
  description = "The Azure region to deploy Cognitive Services (OpenAI) if not available in the primary region."
  default     = "swedencentral"
}

variable "vnet_address_space" {
  type        = list(string)
  description = "The IP address space for the virtual network."
  default     = ["10.0.0.0/16"]
}

variable "subnet_prefixes" {
  type        = map(string)
  description = "Map of subnet prefixes."
  default = {
    aks           = "10.0.1.0/24"
    appgw         = "10.0.2.0/24"
    db            = "10.0.3.0/24"
    endpoints     = "10.0.4.0/24"
    containerapps = "10.0.6.0/23"
  }
}

variable "aks_node_count" {
  type        = number
  description = "The number of nodes in the default AKS node pool."
  default     = 3
}

variable "aks_node_size" {
  type        = string
  description = "The size of the virtual machines in the AKS node pool."
  default     = "Standard_DS3_v2"
}

variable "db_sku_name" {
  type        = string
  description = "The SKU for the PostgreSQL Flexible Server database."
  default     = "GP_Standard_D2s_v3"
}

variable "db_storage_mb" {
  type        = number
  description = "The storage size of the database in MB."
  default     = 131072 # 128 GB
}

variable "redis_sku_name" {
  type        = string
  description = "The SKU tier of Redis to deploy (Basic/Standard/Premium)."
  default     = "Standard"
}

variable "redis_capacity" {
  type        = number
  description = "The capacity of Redis to deploy (0-6)."
  default     = 1
}

variable "openai_base_url" {
  type        = string
  description = "Base URL for the OpenAI-compatible API consumed by the app's public OpenAI SDK. Set to any 3rd-party OpenAI-compatible endpoint."
  default     = "https://api.openai.com/v1"
}

variable "deploy_azure_openai" {
  type        = bool
  description = "Whether to provision the Azure OpenAI (cognitive) module. Off by default: the app uses the public OpenAI SDK against api.openai.com or a 3rd-party endpoint (see openai_base_url)."
  default     = false
}

variable "openai_models" {
  type = list(object({
    name     = string
    version  = string
    capacity = number
  }))
  description = "The list of OpenAI models to deploy."
  default = [
    {
      name     = "gpt-4o"
      version  = "2024-05-13"
      capacity = 10
    },
    {
      name     = "text-embedding-3-small"
      version  = "1"
      capacity = 20
    }
  ]
}

variable "container_app_image" {
  type        = string
  description = "The container image for the web application."
  default     = "harmonaize_production_django:latest"
}

variable "min_replicas" {
  type        = number
  description = "The minimum number of replicas for Container Apps."
  default     = 0
}

variable "max_replicas" {
  type        = number
  description = "The maximum number of replicas for Container Apps."
  default     = 5
}

variable "acr_sku" {
  type        = string
  description = "The SKU tier of the Azure Container Registry."
  default     = "Standard"
}

variable "postgres_admin_username" {
  type        = string
  description = "The admin username for PostgreSQL."
  default     = "hadmin"
}

variable "enable_postgres_ha" {
  type        = bool
  description = "Enable high availability for PostgreSQL."
  default     = true
}

variable "storage_replication_type" {
  type        = string
  description = "The replication type for storage account (LRS, GRS, RAGRS, ZRS, GZRS, RAGZRS)."
  default     = "GRS"
}

variable "tags" {
  type        = map(string)
  description = "A mapping of tags to assign to the resources."
  default = {
    Project     = "HarmonAIze"
    ManagedBy   = "Terraform"
    Application = "Climate-Health-Harmonisation"
  }
}

# Externally-supplied application secrets, stored in Key Vault and consumed by the
# Container Apps via managed-identity Key Vault references. Leave blank to seed an
# empty secret and set the real value in Key Vault later (Terraform ignores value drift).
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
  default     = ""
  description = "Value for ANALYSIS_DEIDENTIFICATION_SALT. Leave blank to auto-generate a strong random salt (recommended for a fresh deployment; set explicitly if you must match existing de-identified data)."
}

variable "allowed_hosts" {
  type        = string
  default     = "harmonaize.org"
  description = "Comma-separated DJANGO_ALLOWED_HOSTS. Set to your real domain(s); avoid '*' in production."
}

# Worker autoscaling (KEDA Redis-list scaler)
variable "celery_queue_name" {
  type        = string
  default     = "celery"
  description = "Redis list name Celery uses as its default queue (KEDA listName)."
}

variable "worker_target_queue_length" {
  type        = number
  default     = 5
  description = "Target Celery queue length per worker replica before KEDA scales out."
}
